"""Camada de acesso unificada para CBZ, CBR, PDF, EPUB, ZIP com CBZ e pastas."""
import base64
import gzip
import io
import json
import mimetypes
import os
import posixpath
import re
import shutil
import sys
import tempfile
import threading
import zipfile
from collections import OrderedDict
from pathlib import Path
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from PIL import Image, ImageOps
from app.image_format_support import ensure_pillow_codec

from app.index_cache import load_index, save_index_async
from app.animation_decode import ANIMATION_CANDIDATE_EXTS
from app.i18n import tr



# Dependências pesadas/opcionais são importadas somente quando o formato pede.
# Isso reduz perceptivelmente o tempo de abertura do Quaint e evita carregar
# DLLs de PDF/RAR/vídeo/codec moderno numa sessão que só lê JPG/PNG/CBZ.
_OPTIONAL_LOCK = threading.Lock()
_OPTIONAL_MODULES = {}

def _optional_import(cache_key, module_name, attr=None):
    cached = _OPTIONAL_MODULES.get(cache_key, ...)
    if cached is not ...:
        return cached
    with _OPTIONAL_LOCK:
        cached = _OPTIONAL_MODULES.get(cache_key, ...)
        if cached is not ...:
            return cached
        try:
            module = __import__(module_name, fromlist=[attr] if attr else ["*"])
            value = getattr(module, attr) if attr else module
        except Exception:
            value = None
        _OPTIONAL_MODULES[cache_key] = value
        return value

def _rarfile_module():
    return _optional_import("rarfile", "rarfile")

def _fitz_module():
    return _optional_import("fitz", "fitz")

def _beautiful_soup():
    return _optional_import("BeautifulSoup", "bs4", "BeautifulSoup")

def _imageio_module():
    return _optional_import("imageio", "imageio.v2")

def _imagecodecs_module():
    return _optional_import("imagecodecs", "imagecodecs")

def _configure_unrar():
    """Se houver um unrar.exe ao lado do executável (ou do main.py), usa-o."""
    rarfile = _rarfile_module()
    if rarfile is None:
        return
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else \
        os.path.dirname(os.path.abspath(__file__ + "/.."))
    for name in ("unrar.exe", "UnRAR.exe"):
        candidate = os.path.join(base, name)
        if os.path.exists(candidate):
            rarfile.UNRAR_TOOL = candidate
            break



IMG_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".gif",
    ".tif", ".tiff", ".ico", ".avif", ".heic", ".heif", ".jxl",
    ".svg", ".svgz",
}
STANDALONE_IMAGE_EXTS = IMG_EXTS | {".webm"}
PAGE_MEDIA_EXTS = STANDALONE_IMAGE_EXTS
FOLDER_MEDIA_EXTS = PAGE_MEDIA_EXTS
METADATA_EXTS = {".json", ".txt"}
SUPPORTED_FILE_EXTS = {
    ".cbz", ".cbr", ".pdf", ".epub",
    ".zip", ".rar", ".7z", ".tar", ".tgz", ".tbz2", ".txz",
    *STANDALONE_IMAGE_EXTS,
}

TAG_KEYS = {
    "tags", "tag", "genres", "genre", "categories", "category",
    "keywords", "keyword", "labels", "label",
}

_TXT_TAG_LINE = re.compile(
    r"^\s*(tags?|g[eê]neros?|categorias?|keywords?|labels?)\s*[:=]\s*(.+)$",
    re.IGNORECASE,
)


_NATURAL_SPLIT_RE = re.compile(r"(\d+)")


def natural_key(s: str):
    """Chave de ordenação natural otimizada para listas muito grandes."""
    return [int(t) if t.isdigit() else t.casefold() for t in _NATURAL_SPLIT_RE.split(str(s))]


def format_size(num_bytes) -> str:
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return "—"
    units = (
        (tr("unit.bytes"), True),
        (tr("unit.kilobytes"), False),
        (tr("unit.megabytes"), False),
        (tr("unit.gigabytes"), False),
    )
    for index, (unit, whole) in enumerate(units):
        if size < 1024.0 or index == len(units) - 1:
            return f"{size:.0f} {unit}" if whole else f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} {tr('unit.terabytes')}"


def _add_tag_value(value, seen, tags):
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            _add_tag_value(item, seen, tags)
        return
    if isinstance(value, (int, float, bool)):
        value = str(value)
    if not isinstance(value, str):
        return
    for part in re.split(r"[,;/]", value):
        part = part.strip()
        if part and part.lower() not in seen:
            seen.add(part.lower())
            tags.append(part)


def _scan_json_for_tags(node, seen, tags, depth=0):
    if depth > 6:
        return
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.strip().lower() in TAG_KEYS:
                _add_tag_value(value, seen, tags)
            elif isinstance(value, (dict, list)):
                _scan_json_for_tags(value, seen, tags, depth + 1)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _scan_json_for_tags(item, seen, tags, depth + 1)


def _local_name(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _resolve_epub_path(base_file, href):
    """Resolve um href relativo dentro do ZIP EPUB, removendo query/fragmento."""
    raw_path = unquote(urlsplit(href or "").path)
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_file), raw_path)).lstrip("/")


class ArchiveError(Exception):
    pass


class _ZipReaderPool:
    """Pool pequeno de ZipFile persistentes.

    Abrir um ZipFile força o Python a reler o diretório central inteiro. Em um
    CBZ com dezenas de milhares de páginas, fazer isso a cada página é muito
    mais caro que a própria imagem. Os handles abaixo são criados sob demanda
    e reutilizados durante toda a leitura.
    """

    def __init__(self, path, initial_handle=None, max_handles=3):
        self.path = Path(path)
        self.max_handles = max(1, int(max_handles))
        self._idle = []
        self._created = 0
        self._cond = threading.Condition()
        self._closed = False
        if initial_handle is not None:
            self._idle.append(initial_handle)
            self._created = 1

    def acquire(self):
        with self._cond:
            while True:
                if self._closed:
                    raise ArchiveError(tr("error.archive_closed"))
                if self._idle:
                    return self._idle.pop()
                if self._created < self.max_handles:
                    self._created += 1
                    create = True
                    break
                self._cond.wait()
        if create:
            try:
                return zipfile.ZipFile(self.path, "r")
            except Exception:
                with self._cond:
                    self._created -= 1
                    self._cond.notify()
                raise

    def release(self, handle):
        if handle is None:
            return
        should_close = False
        with self._cond:
            if self._closed:
                self._created = max(0, self._created - 1)
                should_close = True
            else:
                self._idle.append(handle)
            self._cond.notify()
        if should_close:
            try:
                handle.close()
            except Exception:
                pass

    def read(self, name):
        handle = self.acquire()
        try:
            return handle.read(name)
        finally:
            self.release(handle)

    def copy_to(self, name, dest, *, chunk_size=2 * 1024 * 1024):
        """Descompacta diretamente para disco sem criar um bytes gigante."""
        handle = self.acquire()
        try:
            with handle.open(name, "r") as src, open(dest, "wb", buffering=chunk_size) as out:
                shutil.copyfileobj(src, out, length=chunk_size)
        finally:
            self.release(handle)

    def names(self):
        handle = self.acquire()
        try:
            return [i.filename for i in handle.infolist() if not i.is_dir()]
        finally:
            self.release(handle)

    def close(self):
        with self._cond:
            if self._closed:
                return
            self._closed = True
            idle = self._idle
            self._idle = []
            self._created = max(0, self._created - len(idle))
            self._cond.notify_all()
        # Handles que estejam em uso são fechados no release(); isso evita
        # invalidar uma descompressão que ainda esteja terminando.
        for handle in idle:
            try:
                handle.close()
            except Exception:
                pass


class _PdfReaderPool:
    """Pool de Documents PyMuPDF persistentes, um uso por handle.

    Reabrir um PDF para cada página relê estruturas internas e custa tempo,
    especialmente em documentos grandes. Handles distintos permitem decode em
    paralelo sem compartilhar um mesmo Document entre threads.
    """

    def __init__(self, path, initial_handle=None, max_handles=3):
        self.path = Path(path)
        self.max_handles = max(1, int(max_handles))
        self._idle = []
        self._created = 0
        self._cond = threading.Condition()
        self._closed = False
        if initial_handle is not None:
            self._idle.append(initial_handle)
            self._created = 1

    def acquire(self):
        with self._cond:
            while True:
                if self._closed:
                    raise ArchiveError(tr("error.archive_closed"))
                if self._idle:
                    return self._idle.pop()
                if self._created < self.max_handles:
                    self._created += 1
                    break
                self._cond.wait()
        fitz = _fitz_module()
        if fitz is None:
            with self._cond:
                self._created -= 1
                self._cond.notify()
            raise ArchiveError(tr("error.pdf_support"))
        try:
            return fitz.open(str(self.path))
        except Exception:
            with self._cond:
                self._created -= 1
                self._cond.notify()
            raise

    def release(self, handle):
        if handle is None:
            return
        should_close = False
        with self._cond:
            if self._closed:
                self._created = max(0, self._created - 1)
                should_close = True
            else:
                self._idle.append(handle)
            self._cond.notify()
        if should_close:
            try:
                handle.close()
            except Exception:
                pass

    def close(self):
        with self._cond:
            if self._closed:
                return
            self._closed = True
            idle = self._idle
            self._idle = []
            self._created = max(0, self._created - len(idle))
            self._cond.notify_all()
        for handle in idle:
            try:
                handle.close()
            except Exception:
                pass


class ComicArchive:
    """Interface única sobre todos os formatos suportados pelo leitor.

    Páginas de imagem expõem ``read_bytes`` normalmente. Em EPUBs com texto,
    uma posição do spine pode ser uma página textual; use ``is_text_page`` e
    ``text_html`` para consultá-la.
    """

    PDF_ZOOM = 2.0  # 144 dpi: boa nitidez sem memória excessiva.

    def __init__(self, path, *, directory_pages=None):
        self.path = Path(path)
        self._preindexed_directory_pages = (
            list(directory_pages) if directory_pages is not None else None
        )
        self.kind = None  # zip | rar | dir | pdf | epub | image
        self._zip = None
        self._zip_pool = None
        self._rar = None
        self._pdf = None
        self._pdf_pool = None
        self._lock = threading.RLock()
        self.pages = []
        self._single_image_bytes = None
        self._single_image_frame = None
        self._has_text_pages = False
        # Cache pequeno dos bytes compactados/originais. Ele é compartilhado
        # pelo leitor principal e pelas miniaturas, evitando descompactar a
        # mesma página mais de uma vez quando ambos a pedem quase juntos.
        self._raw_cache = OrderedDict()
        self._raw_cache_bytes = 0
        self._raw_cache_limit = 32 * 1024 * 1024
        self._raw_inflight = {}
        self._zip_names = None
        self._open()

    # -------------------------------------------------------------- Abrir --
    def _open(self):
        p = self.path
        if not p.exists():
            raise ArchiveError(tr("error.path_not_found", path=p))

        if p.is_dir():
            self.kind = "dir"
            if self._preindexed_directory_pages is not None:
                self.pages = self._preindexed_directory_pages
                self._preindexed_directory_pages = None
                self._folder_sort_applied = ("name", False)
                save_index_async(p, "dir", self.pages)
                return
            cached = load_index(p, "dir")
            if cached is not None:
                # Nomes relativos são muito mais leves que dezenas de milhares
                # de objetos Path e são convertidos em caminho só quando usados.
                self.pages = cached
                self._folder_sort_applied = ("name", False)
                return
            try:
                with os.scandir(p) as entries:
                    names = [
                        entry.name for entry in entries
                        if entry.is_file()
                        and os.path.splitext(entry.name)[1].lower() in FOLDER_MEDIA_EXTS
                    ]
            except OSError as e:
                raise ArchiveError(tr("error.folder_list", error=e)) from e
            if not names:
                raise ArchiveError(tr("error.folder_no_images"))
            names.sort(key=natural_key)
            self.pages = names
            self._folder_sort_applied = ("name", False)
            save_index_async(p, "dir", names)
            return

        suffix = p.suffix.lower()
        if suffix == ".pdf":
            self._open_pdf()
        elif suffix == ".epub":
            self._open_epub()
        elif suffix in STANDALONE_IMAGE_EXTS:
            self._open_single_image()
        elif suffix == ".zip":
            raise ArchiveError(tr("error.zip_collection_layer"))
        elif suffix == ".cbz":
            self._open_cbz()
        elif suffix == ".cbr":
            self._open_cbr()
        elif zipfile.is_zipfile(p):
            # Compatibilidade com CBZ sem extensão convencional.
            self._open_cbz()
        else:
            self._open_cbr()


    @staticmethod
    def _decode_svg_bytes(data: bytes, suffix: str, max_dim=None) -> Image.Image:
        """Renderiza SVG/SVGZ com o QtSvg já distribuído com PySide6.

        Usa o QtSvg já distribuído com PySide6, evitando dependências gráficas
        nativas externas que não acompanham automaticamente um executável
        one-file do PyInstaller no Windows.
        """
        svg_data = gzip.decompress(data) if suffix == ".svgz" else data
        try:
            from PySide6.QtCore import QByteArray
            from PySide6.QtGui import QImage, QPainter
            from PySide6.QtSvg import QSvgRenderer
        except Exception as e:
            raise ArchiveError(tr("error.svg_support")) from e

        painter = None
        try:
            renderer = QSvgRenderer(QByteArray(svg_data))
            if not renderer.isValid():
                raise ValueError("QtSvg rejected the SVG document")

            size = renderer.defaultSize()
            width = int(size.width())
            height = int(size.height())
            if width <= 0 or height <= 0:
                view_box = renderer.viewBoxF()
                width = max(1, int(round(view_box.width())))
                height = max(1, int(round(view_box.height())))

            # SVGs podem declarar dimensões absurdamente grandes. Para a
            # visualização em tela não há benefício em rasterizar dezenas de
            # milhares de pixels; max_dim continua tendo prioridade quando
            # fornecido pelo chamador.
            target_limit = int(max_dim) if max_dim else 16384
            largest = max(width, height)
            if largest > target_limit:
                scale = target_limit / float(largest)
                width = max(1, int(round(width * scale)))
                height = max(1, int(round(height * scale)))

            # RGBA8888 permite transferir o raster diretamente para Pillow.
            # A versão anterior codificava o QImage em PNG na memória e logo
            # depois o decodificava de novo, um custo grande em SVGs extensos.
            qimage = QImage(width, height, QImage.Format.Format_RGBA8888)
            qimage.fill(0)
            painter = QPainter(qimage)
            renderer.render(painter)
            painter.end()
            painter = None

            raw = qimage.bits().tobytes()
            img = Image.frombuffer(
                "RGBA", (width, height), raw, "raw", "RGBA",
                qimage.bytesPerLine(), 1,
            ).copy()
            return ComicArchive._prepare_pil_image(img, max_dim)
        except ArchiveError:
            raise
        except Exception as e:
            raise ArchiveError(tr("error.svg_invalid", error=e)) from e
        finally:
            if painter is not None and painter.isActive():
                painter.end()

    @staticmethod
    def _svg_dimensions_bytes(data: bytes, suffix: str):
        svg_data = gzip.decompress(data) if suffix == ".svgz" else data
        try:
            from PySide6.QtCore import QByteArray
            from PySide6.QtSvg import QSvgRenderer
            renderer = QSvgRenderer(QByteArray(svg_data))
            if not renderer.isValid():
                return 0, 0
            size = renderer.defaultSize()
            w, h = int(size.width()), int(size.height())
            if w <= 0 or h <= 0:
                box = renderer.viewBoxF()
                w, h = int(round(box.width())), int(round(box.height()))
            return max(0, w), max(0, h)
        except Exception:
            return 0, 0

    @staticmethod
    def _modern_dimensions_bytes(data: bytes, suffix: str):
        suffix = str(suffix or "").lower()
        if suffix in (".svg", ".svgz"):
            return ComicArchive._svg_dimensions_bytes(data, suffix)
        ensure_pillow_codec(suffix)
        try:
            with Image.open(io.BytesIO(data)) as img:
                return tuple(map(int, img.size))
        except Exception:
            if suffix == ".jxl":
                # Fallback raro: alguns builds de JPEG XL só expõem decode.
                imagecodecs = _imagecodecs_module()
                if imagecodecs is not None:
                    try:
                        arr = imagecodecs.jpegxl_decode(data)
                        return int(arr.shape[1]), int(arr.shape[0])
                    except Exception:
                        pass
            return 0, 0

    @staticmethod
    def _decode_modern_bytes(data: bytes, suffix: str, max_dim=None) -> Image.Image:
        suffix = str(suffix or "").lower()
        if suffix in (".svg", ".svgz"):
            return ComicArchive._decode_svg_bytes(data, suffix, max_dim)

        # Carrega apenas o plugin associado à extensão atual.
        ensure_pillow_codec(suffix)
        try:
            with Image.open(io.BytesIO(data)) as img:
                return ComicArchive._prepare_pil_image(img, max_dim)
        except Exception as pillow_error:
            imagecodecs = _imagecodecs_module() if suffix == ".jxl" else None
            if suffix == ".jxl" and imagecodecs is not None:
                try:
                    arr = imagecodecs.jpegxl_decode(data)
                    img = Image.fromarray(arr)
                    return ComicArchive._prepare_pil_image(img, max_dim)
                except Exception as e:
                    raise ArchiveError(tr("error.jxl_invalid", error=e)) from e
            raise ArchiveError(tr("error.image_invalid", error=pillow_error)) from pillow_error

    @staticmethod
    def _decode_modern_path(path, max_dim=None) -> Image.Image:
        path = Path(path)
        return ComicArchive._decode_modern_bytes(path.read_bytes(), path.suffix.lower(), max_dim)

    @staticmethod
    def _modern_dimensions_path(path):
        """Lê somente metadados/dimensões de formatos modernos quando possível."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in (".svg", ".svgz"):
            try:
                return ComicArchive._svg_dimensions_bytes(path.read_bytes(), suffix)
            except OSError:
                return 0, 0
        ensure_pillow_codec(suffix)
        try:
            with Image.open(path) as img:
                return tuple(map(int, img.size))
        except Exception:
            if suffix == ".jxl":
                try:
                    return ComicArchive._modern_dimensions_bytes(path.read_bytes(), suffix)
                except OSError:
                    return 0, 0
            return 0, 0

    def _open_single_image(self):
        """Abre uma imagem avulsa como uma única página.

        WebM é tratado como imagem pelo leitor a pedido do usuário. A abertura
        é lazy: o FFmpeg só é iniciado quando a página precisa aparecer.
        """
        suffix = self.path.suffix.lower()
        if suffix == ".webm":
            if _imageio_module() is None:
                raise ArchiveError(tr("error.webm_support"))
            # Não inicializa o FFmpeg aqui. A abertura da janela deve ser
            # instantânea; o primeiro quadro e a animação são decodificados
            # sob demanda em threads de fundo.
        else:
            try:
                if suffix in (".svg", ".svgz", ".jxl"):
                    # Não decodifica pixels só para validar a abertura. O
                    # decode real fica para o worker da página atual.
                    width, height = self._modern_dimensions_path(self.path)
                    if width <= 0 or height <= 0:
                        raise ValueError(tr("error.invalid_dimensions"))
                else:
                    ensure_pillow_codec(suffix)
                    # Image.open lê apenas o cabeçalho. A antiga chamada verify()
                    # percorria o arquivo inteiro e duplicava I/O antes da exibição.
                    with Image.open(self.path) as img:
                        if img.width <= 0 or img.height <= 0:
                            raise ValueError(tr("error.invalid_dimensions"))
            except Exception as e:  # noqa: BLE001
                if isinstance(e, ArchiveError):
                    raise
                raise ArchiveError(tr("error.image_invalid", error=e)) from e

        self.kind = "image"
        self.pages = [self.path]

    def _open_cbz(self):
        try:
            self._zip = zipfile.ZipFile(self.path, "r")
            self.kind = "zip"
            cached = load_index(self.path, "cbz")
            if cached is not None:
                names = cached
            else:
                # infolist() já existe dentro do ZipFile; namelist()+set()
                # duplicava dezenas de milhares de strings sem necessidade.
                names = [
                    info.filename for info in self._zip.infolist()
                    if not info.is_dir()
                    and os.path.splitext(info.filename)[1].lower() in PAGE_MEDIA_EXTS
                ]
                names.sort(key=natural_key)
                save_index_async(self.path, "cbz", names)
        except zipfile.BadZipFile as e:
            raise ArchiveError(tr("error.cbz_invalid", error=e)) from e
        if not names:
            raise ArchiveError(tr("error.archive_no_images"))
        self.pages = names
        # O primeiro handle já pagou o custo de ler o diretório central.
        # Ele entra no pool e será reutilizado em vez de reabrir o CBZ por página.
        self._zip_pool = _ZipReaderPool(self.path, self._zip, max_handles=3)

    def _open_cbr(self):
        rarfile = _rarfile_module()
        if rarfile is None:
            raise ArchiveError(tr("error.cbr_support"))
        _configure_unrar()
        try:
            self._rar = rarfile.RarFile(self.path, "r")
            self.kind = "rar"
            cached = load_index(self.path, "cbr")
            if cached is not None:
                names = cached
            else:
                names = [
                    info.filename for info in self._rar.infolist()
                    if not info.isdir()
                    and os.path.splitext(info.filename)[1].lower() in PAGE_MEDIA_EXTS
                ]
                names.sort(key=natural_key)
                save_index_async(self.path, "cbr", names)
        except rarfile.Error as e:
            raise ArchiveError(tr("error.cbr_invalid", error=e)) from e
        if not names:
            raise ArchiveError(tr("error.archive_no_images"))
        self.pages = names

    def _open_pdf(self):
        fitz = _fitz_module()
        if fitz is None:
            raise ArchiveError(tr("error.pdf_support"))
        try:
            self._pdf = fitz.open(str(self.path))
        except Exception as e:  # noqa: BLE001
            raise ArchiveError(tr("error.pdf_invalid", error=e)) from e
        if self._pdf.page_count <= 0:
            raise ArchiveError(tr("error.pdf_no_pages"))
        self.kind = "pdf"
        self.pages = range(self._pdf.page_count)
        self._pdf_pool = _PdfReaderPool(self.path, self._pdf, max_handles=3)

    def _open_epub(self):
        BeautifulSoup = _beautiful_soup()
        if BeautifulSoup is None:
            raise ArchiveError(tr("error.epub_support"))
        try:
            self._zip = zipfile.ZipFile(self.path, "r")
            self._zip_names = set(self._zip.namelist())
        except zipfile.BadZipFile as e:
            raise ArchiveError(tr("error.epub_invalid", error=e)) from e

        try:
            container_xml = self._zip.read("META-INF/container.xml")
            root = ET.fromstring(container_xml)
            rootfile = next(
                el.attrib.get("full-path") for el in root.iter()
                if _local_name(el.tag) == "rootfile" and el.attrib.get("full-path")
            )
            opf_raw = self._zip.read(rootfile)
            opf = ET.fromstring(opf_raw)
        except Exception as e:  # noqa: BLE001
            self._zip.close()
            self._zip = None
            raise ArchiveError(tr("error.epub_opf", error=e)) from e

        manifest = {}
        for el in opf.iter():
            if _local_name(el.tag) != "item":
                continue
            item_id = el.attrib.get("id")
            href = el.attrib.get("href")
            if item_id and href:
                manifest[item_id] = {
                    "path": _resolve_epub_path(rootfile, href),
                    "media_type": el.attrib.get("media-type", ""),
                    "properties": el.attrib.get("properties", ""),
                }

        spine_ids = [
            el.attrib.get("idref") for el in opf.iter()
            if _local_name(el.tag) == "itemref" and el.attrib.get("idref")
        ]

        pages = []
        for item_id in spine_ids:
            item = manifest.get(item_id)
            if not item:
                continue
            item_path = item["path"]
            media_type = item["media_type"].lower()

            if media_type.startswith("image/") or Path(item_path).suffix.lower() in IMG_EXTS:
                if item_path in self._zip_names:
                    pages.append({"type": "image", "path": item_path, "source": item_path})
                continue

            if not ("html" in media_type or Path(item_path).suffix.lower() in {".xhtml", ".html", ".htm"}):
                continue

            try:
                raw = self._zip.read(item_path)
            except KeyError:
                continue
            chapter_pages = self._parse_epub_document(item_path, raw)
            pages.extend(chapter_pages)

        # Fallback para EPUBs de imagens com spine incompleto/não convencional.
        if not pages:
            image_paths = sorted(
                [item["path"] for item in manifest.values()
                 if item["media_type"].lower().startswith("image/")
                 and Path(item["path"]).suffix.lower() in IMG_EXTS
                 and item["path"] in self._zip_names],
                key=natural_key,
            )
            pages = [{"type": "image", "path": p, "source": p} for p in image_paths]

        if not pages:
            self._zip.close()
            self._zip = None
            raise ArchiveError(tr("error.epub_no_content"))

        self.kind = "epub"
        self.pages = pages
        self._has_text_pages = any(p.get("type") == "text" for p in pages)
        self._zip_pool = _ZipReaderPool(self.path, self._zip, max_handles=2)

    def _parse_epub_document(self, item_path, raw):
        BeautifulSoup = _beautiful_soup()
        if BeautifulSoup is None:
            raise ArchiveError(tr("error.epub_support"))
        soup = BeautifulSoup(raw, "html.parser")
        body = soup.body or soup
        for tag in body.find_all(["script", "style", "noscript", "form"]):
            tag.decompose()

        image_refs = []
        for img in body.find_all("img"):
            src = img.get("src")
            if src:
                resolved = _resolve_epub_path(item_path, src)
                if resolved in self._zip_names and Path(resolved).suffix.lower() in IMG_EXTS:
                    image_refs.append(resolved)
        # EPUBs de mangá às vezes usam SVG <image href=...> como página.
        for svg_img in body.find_all("image"):
            src = svg_img.get("href") or svg_img.get("xlink:href")
            if src:
                resolved = _resolve_epub_path(item_path, src)
                if resolved in self._zip_names and Path(resolved).suffix.lower() in IMG_EXTS:
                    image_refs.append(resolved)

        text = " ".join(body.stripped_strings)
        compact_text_len = len(re.sub(r"[^\w]+", "", text, flags=re.UNICODE))
        # Se não há imagens, qualquer texto visível deve ser preservado (até
        # capítulos muito curtos). Com imagens, o limiar evita classificar
        # legendas técnicas de páginas de mangá como capítulos textuais.
        meaningful = bool(text.strip()) and (not image_refs or compact_text_len >= 12)

        if not meaningful and image_refs:
            # Página puramente ilustrada: cada imagem vira uma página normal,
            # exatamente como CBZ/CBR/PDF.
            return [
                {"type": "image", "path": img_path, "source": item_path}
                for img_path in image_refs
            ]

        if meaningful:
            # Não embute imagens/base64 de todos os capítulos durante a
            # abertura. O XHTML limpo é preparado somente quando o usuário
            # realmente chega a este capítulo textual.
            return [{
                "type": "text",
                "html": None,
                "html_raw": body.decode_contents(),
                "source": item_path,
            }]

        return []

    def _prepare_epub_html(self, item_path, body):
        """Limpa o XHTML e embute imagens como data URI para o QTextBrowser."""
        BeautifulSoup = _beautiful_soup()
        if BeautifulSoup is None:
            raise ArchiveError(tr("error.epub_support"))
        # Removemos estilos autorais agressivos; o leitor aplica uma tipografia
        # consistente, mas preserva tags semânticas (títulos, ênfase, citações...).
        for tag in body.find_all(True):
            tag.attrs.pop("style", None)
            tag.attrs.pop("class", None)
            tag.attrs.pop("id", None)

        for img in body.find_all("img"):
            src = img.get("src")
            if not src:
                continue
            resolved = _resolve_epub_path(item_path, src)
            try:
                data = self._zip_pool.read(resolved)
            except KeyError:
                continue
            mime = mimetypes.guess_type(resolved)[0] or "image/jpeg"
            img["src"] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            img["alt"] = img.get("alt", "")

        # Converte imagens SVG referenciadas para <img> quando apontam a um
        # bitmap; isso cobre muitos EPUBs de mangá gerados por conversores.
        for svg_img in list(body.find_all("image")):
            src = svg_img.get("href") or svg_img.get("xlink:href")
            if not src:
                continue
            resolved = _resolve_epub_path(item_path, src)
            try:
                data = self._zip_pool.read(resolved)
            except KeyError:
                continue
            mime = mimetypes.guess_type(resolved)[0] or "image/jpeg"
            replacement = BeautifulSoup("<img/>", "html.parser").img
            replacement["src"] = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
            svg_img.replace_with(replacement)

        return body.decode_contents()

    # ------------------------------------------------------ Cache/decodificação --
    def _raw_cache_get(self, key):
        with self._lock:
            value = self._raw_cache.get(key)
            if value is not None:
                self._raw_cache.move_to_end(key)
            return value

    def _raw_cache_put(self, key, data: bytes):
        # Permite que uma única página grande use quase todo o cache. Isso é
        # especialmente útil para GIF/WebP animados dentro de CBZ/EPUB: leitor
        # estático e player podem pedir a mesma página ao mesmo tempo e não
        # devem descompactar dezenas de MB duas vezes.
        if not data or len(data) > self._raw_cache_limit:
            return
        with self._lock:
            old = self._raw_cache.pop(key, None)
            if old is not None:
                self._raw_cache_bytes -= len(old)
            self._raw_cache[key] = data
            self._raw_cache_bytes += len(data)
            while self._raw_cache and self._raw_cache_bytes > self._raw_cache_limit:
                _old_key, old_data = self._raw_cache.popitem(last=False)
                self._raw_cache_bytes -= len(old_data)

    @staticmethod
    def _prepare_pil_image(img: Image.Image, max_dim=None) -> Image.Image:
        """Decodifica/redimensiona com o mínimo de pixels intermediários.

        JPEG usa ``draft`` quando disponível, fazendo o libjpeg decodificar
        diretamente em 1/2, 1/4 ou 1/8 da resolução antes do LANCZOS final.
        ``thumbnail(..., reducing_gap=3)`` também faz redução inteira rápida
        antes da etapa de alta qualidade em PNG/WebP/TIFF e afins.
        """
        try:
            img.seek(0)
        except Exception:
            pass
        if max_dim:
            try:
                img.draft("RGB", (int(max_dim), int(max_dim)))
            except Exception:
                pass
        # ImageOps.exif_transpose() cria uma cópia mesmo quando não há rotação.
        # Só o chamamos quando a orientação EXIF realmente exige transformação,
        # evitando uma cópia full-res desnecessária na maioria das páginas.
        try:
            orientation = img.getexif().get(274, 1)
        except Exception:
            orientation = 1
        if orientation not in (None, 1):
            img = ImageOps.exif_transpose(img)
        if img.info.get("icc_profile"):
            try:
                from app.color_management import convert_embedded_profile_to_srgb
                img = convert_embedded_profile_to_srgb(img)
            except Exception:
                pass
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGBA" if "transparency" in img.info or "A" in img.getbands() else "RGB")
        if max_dim and max(img.size) > int(max_dim):
            img.thumbnail(
                (int(max_dim), int(max_dim)),
                Image.Resampling.LANCZOS,
                reducing_gap=3.0,
            )
        return img.copy()

    @staticmethod
    def _load_webm_first_frame(path, max_dim=None) -> Image.Image:
        imageio = _imageio_module()
        if imageio is None:
            raise ArchiveError(tr("error.webm_support"))
        reader = imageio.get_reader(str(path))
        try:
            frame = reader.get_data(0)
        except Exception as e:  # noqa: BLE001
            raise ArchiveError(tr("error.webm_invalid", error=e)) from e
        finally:
            reader.close()
        img = Image.fromarray(frame).convert("RGB")
        return ComicArchive._prepare_pil_image(img, max_dim)

    @staticmethod
    def _load_webm_bytes_first_frame(data: bytes, max_dim=None) -> Image.Image:
        handle = tempfile.NamedTemporaryFile(
            prefix="quaint_webm_", suffix=".webm", delete=False
        )
        temp_path = handle.name
        try:
            handle.write(data)
            handle.close()
            return ComicArchive._load_webm_first_frame(temp_path, max_dim)
        finally:
            try:
                handle.close()
            except Exception:
                pass
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def page_suffix(self, index: int) -> str:
        """Extensão lógica da página sem tocar nos bytes da imagem."""
        entry = self.pages[index]
        if self.kind == "dir":
            return Path(entry).suffix.lower()
        if self.kind == "image":
            return self.path.suffix.lower()
        if self.kind in ("zip", "rar"):
            return Path(entry).suffix.lower()
        if self.kind == "epub" and isinstance(entry, dict) and entry.get("type") == "image":
            return Path(entry.get("path", "")).suffix.lower()
        return ""

    def animation_candidate(self, index: int) -> bool:
        """Teste O(1) usado pela UI; não varre a coleção nem decodifica frames."""
        if self.is_text_page(index):
            return False
        return self.page_suffix(index) in ANIMATION_CANDIDATE_EXTS

    def animation_source(self, index: int):
        """Fonte lazy para o player de animação.

        Caminhos reais são preferidos porque GIF/WebP/WebM podem ser lidos em
        streaming. Para páginas dentro de CBZ/CBR/EPUB, somente a página ativa
        é descompactada e mantida enquanto estiver sendo animada.
        """
        if not self.animation_candidate(index):
            return None
        suffix = self.page_suffix(index)
        entry = self.pages[index]
        if self.kind == "dir":
            return {"path": str(self.path / entry), "suffix": suffix}
        if self.kind == "image":
            return {"path": str(self.path), "suffix": suffix}
        return {"data": self.read_bytes(index), "suffix": suffix}

    def load_image(self, index: int, max_dim=None) -> Image.Image:
        """Retorna a página já decodificada em PIL, otimizada para o alvo.

        Para PDF o raster é gerado diretamente próximo da resolução pedida,
        evitando renderizar a 144 dpi para depois jogar fora a maior parte dos
        pixels em uma miniatura. Para imagens em arquivo/pasta evita uma cópia
        intermediária desnecessária para ``bytes``.
        """
        if self.is_text_page(index):
            raise ArchiveError(tr("error.epub_text_no_image"))

        entry = self.pages[index]
        if self.kind == "pdf":
            fitz = _fitz_module()
            if fitz is None:
                raise ArchiveError(tr("error.pdf_support"))
            pool = self._pdf_pool
            if pool is None:
                raise ArchiveError(tr("error.pdf_support"))
            doc = pool.acquire()
            try:
                page = doc.load_page(int(entry))
                rect = page.rect
                zoom = self.PDF_ZOOM
                if max_dim:
                    longest = max(float(rect.width), float(rect.height), 1.0)
                    zoom = min(self.PDF_ZOOM, max(0.12, float(max_dim) / longest))
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                if max_dim and max(img.size) > int(max_dim):
                    img.thumbnail((int(max_dim), int(max_dim)), Image.Resampling.LANCZOS, reducing_gap=3.0)
                return img
            finally:
                pool.release(doc)

        if self.kind == "dir":
            source_path = self.path / entry
            suffix = source_path.suffix.lower()
            if suffix == ".webm":
                return self._load_webm_first_frame(source_path, max_dim)
            if suffix in (".svg", ".svgz", ".jxl"):
                return self._decode_modern_path(source_path, max_dim)
            ensure_pillow_codec(suffix)
            with Image.open(source_path) as img:
                return self._prepare_pil_image(img, max_dim)

        if self.kind == "image" and self.path.suffix.lower() == ".webm":
            return self._load_webm_first_frame(self.path, max_dim)
        if self.kind == "image" and self.path.suffix.lower() in (".svg", ".svgz", ".jxl"):
            return self._decode_modern_path(self.path, max_dim)

        if self.kind == "image" and self._single_image_frame is not None:
            return self._prepare_pil_image(self._single_image_frame.copy(), max_dim)

        if self.kind == "image" and self._single_image_bytes is None:
            ensure_pillow_codec(self.path.suffix.lower())
            with Image.open(self.path) as img:
                return self._prepare_pil_image(img, max_dim)

        data = self.read_bytes(index)
        suffix = self.page_suffix(index)
        if suffix == ".webm":
            return self._load_webm_bytes_first_frame(data, max_dim)
        if suffix in (".svg", ".svgz", ".jxl"):
            return self._decode_modern_bytes(data, suffix, max_dim)
        ensure_pillow_codec(suffix)
        with Image.open(io.BytesIO(data)) as img:
            return self._prepare_pil_image(img, max_dim)

    def raw_cache_info(self):
        with self._lock:
            return {
                "entries": len(self._raw_cache),
                "bytes": self._raw_cache_bytes,
                "limit": self._raw_cache_limit,
            }

    # --------------------------------------------------------------- Página --
    def count(self):
        return len(self.pages)

    def is_text_page(self, index: int) -> bool:
        return self.kind == "epub" and self.pages[index].get("type") == "text"

    def has_text_pages(self) -> bool:
        return bool(self._has_text_pages)

    def text_html(self, index: int) -> str:
        if not self.is_text_page(index):
            raise ArchiveError(tr("error.epub_not_text"))
        entry = self.pages[index]
        prepared = entry.get("html")
        if prepared is None:
            BeautifulSoup = _beautiful_soup()
            if BeautifulSoup is None:
                raise ArchiveError(tr("error.epub_support"))
            soup = BeautifulSoup(entry.get("html_raw", ""), "html.parser")
            body = soup.body or soup
            prepared = self._prepare_epub_html(entry.get("source", ""), body)
            entry["html"] = prepared
            # O HTML preparado substitui o bruto para não manter duas cópias
            # do capítulo na memória pelo resto da sessão.
            entry.pop("html_raw", None)
        return prepared

    def read_bytes(self, index: int) -> bytes:
        entry = self.pages[index]
        if self.kind == "pdf":
            fitz = _fitz_module()
            if fitz is None:
                raise ArchiveError(tr("error.pdf_support"))
            # Mantém compatibilidade da API antiga; o caminho rápido normal é
            # ``load_image()``, que rasteriza já na resolução necessária.
            pool = self._pdf_pool
            if pool is None:
                raise ArchiveError(tr("error.pdf_support"))
            doc = pool.acquire()
            try:
                page = doc.load_page(int(entry))
                matrix = fitz.Matrix(self.PDF_ZOOM, self.PDF_ZOOM)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                return pix.tobytes("png")
            finally:
                pool.release(doc)
        if self.kind == "epub" and entry.get("type") == "text":
            raise ArchiveError(tr("error.epub_text_no_bytes"))

        cache_key = (self.kind, int(index))

        # Se leitor e miniaturas pedirem a mesma página ao mesmo tempo, apenas
        # uma thread descompacta/lê. As demais aguardam o resultado do cache.
        while True:
            with self._lock:
                cached = self._raw_cache.get(cache_key)
                if cached is not None:
                    self._raw_cache.move_to_end(cache_key)
                    return cached
                event = self._raw_inflight.get(cache_key)
                if event is None:
                    event = threading.Event()
                    self._raw_inflight[cache_key] = event
                    owner = True
                else:
                    owner = False
            if owner:
                break
            event.wait()

        try:
            if self.kind == "dir":
                data = (self.path / entry).read_bytes()
            elif self.kind == "image":
                if self._single_image_bytes is not None:
                    data = self._single_image_bytes
                elif self._single_image_frame is not None:
                    bio = io.BytesIO()
                    self._single_image_frame.save(bio, format="PNG")
                    data = bio.getvalue()
                    self._single_image_bytes = data
                elif self.path.suffix.lower() == ".webm":
                    # Preserve the read_bytes() contract: callers receive an
                    # image payload, not the raw video container. The decode
                    # is still lazy and only happens if bytes are explicitly
                    # requested; animation playback itself streams from path.
                    frame = self._load_webm_first_frame(self.path, None)
                    bio = io.BytesIO()
                    frame.save(bio, format="PNG")
                    data = bio.getvalue()
                    self._single_image_bytes = data
                else:
                    data = self.path.read_bytes()
            elif self.kind == "zip":
                # Reutiliza handles persistentes. Em CBZs gigantes, reabrir um
                # ZipFile aqui faria o Python reindexar todo o diretório central
                # a cada página.
                data = self._zip_pool.read(entry)
            elif self.kind == "rar":
                # rarfile/UnRAR permanece serializado por segurança do backend.
                with self._lock:
                    data = self._rar.read(entry)
            elif self.kind == "epub":
                data = self._zip_pool.read(entry["path"])
            else:
                raise ArchiveError(tr("error.unsupported_format"))
            self._raw_cache_put(cache_key, data)
            return data
        finally:
            with self._lock:
                done = self._raw_inflight.pop(cache_key, None)
                if done is not None:
                    done.set()

    def page_name(self, index: int) -> str:
        entry = self.pages[index]
        if self.kind == "dir":
            return Path(entry).name
        if self.kind == "image":
            return self.path.name
        if self.kind in ("zip", "rar"):
            return Path(entry).name
        if self.kind == "pdf":
            return tr("page.label", number=index + 1)
        if self.kind == "epub":
            if entry.get("type") == "text":
                return tr("text.label", number=index + 1)
            return Path(entry["path"]).name
        return str(index + 1)

    def page_size(self, index: int):
        if self.kind == "pdf":
            pool = self._pdf_pool
            if pool is None:
                return 0, 0
            doc = pool.acquire()
            try:
                rect = doc.load_page(int(self.pages[index])).rect
                return int(rect.width * self.PDF_ZOOM), int(rect.height * self.PDF_ZOOM)
            finally:
                pool.release(doc)
        if self.is_text_page(index):
            return 0, 0
        if self.kind == "image" and self._single_image_frame is not None:
            return self._single_image_frame.size
        if self.kind == "image":
            suffix = self.path.suffix.lower()
            if suffix == ".webm":
                return self._load_webm_first_frame(self.path, None).size
            if suffix in (".svg", ".svgz", ".jxl"):
                try:
                    return self._modern_dimensions_bytes(self.path.read_bytes(), suffix)
                except OSError:
                    return 0, 0
            ensure_pillow_codec(suffix)
            with Image.open(self.path) as img:
                return img.size
        if self.kind == "dir":
            source_path = self.path / self.pages[index]
            suffix = source_path.suffix.lower()
            if suffix == ".webm":
                return self._load_webm_first_frame(source_path, None).size
            if suffix in (".svg", ".svgz", ".jxl"):
                try:
                    return self._modern_dimensions_bytes(source_path.read_bytes(), suffix)
                except OSError:
                    return 0, 0
            ensure_pillow_codec(suffix)
            with Image.open(source_path) as img:
                return img.size
        data = self.read_bytes(index)
        suffix = self.page_suffix(index)
        if suffix == ".webm":
            return self._load_webm_bytes_first_frame(data, None).size
        if suffix in (".svg", ".svgz", ".jxl"):
            return self._modern_dimensions_bytes(data, suffix)
        ensure_pillow_codec(suffix)
        with Image.open(io.BytesIO(data)) as img:
            return img.size

    def display_name(self) -> str:
        return getattr(self, "display_name_override", None) or self.path.name

    # ------------------------------------------------------- Métricas/Info --
    def kind_label(self) -> str:
        keys = {
            "zip": "type.cbz",
            "rar": "type.cbr",
            "dir": "type.image_folder",
            "image": "type.image",
            "pdf": "type.pdf",
            "epub": "type.epub",
        }
        key = keys.get(self.kind)
        return tr(key) if key else (self.kind or "—")

    def file_size_bytes(self) -> int:
        if self.kind == "dir":
            total = 0
            for name in self.pages:
                try:
                    total += (self.path / name).stat().st_size
                except OSError:
                    pass
            return total
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def _all_entry_names(self):
        if self.kind == "dir":
            try:
                with os.scandir(self.path) as entries:
                    return [e.name for e in entries if e.is_file()]
            except OSError:
                return []
        if self.kind in ("zip", "epub") and self._zip_pool:
            return self._zip_pool.names()
        if self.kind == "rar":
            return [n for n in self._rar.namelist() if not n.endswith("/")]
        return []

    def _read_entry_bytes(self, name: str) -> bytes:
        if self.kind == "dir":
            return (self.path / name).read_bytes()
        if self.kind in ("zip", "epub"):
            return self._zip_pool.read(name)
        if self.kind == "rar":
            return self._rar.read(name)
        raise ArchiveError(tr("error.entries_unavailable"))

    def gather_metadata(self):
        names = [n for n in self._all_entry_names() if Path(n).suffix.lower() in METADATA_EXTS]
        tags = []
        seen = set()

        for name in sorted(names, key=natural_key):
            try:
                raw = self._read_entry_bytes(name)
            except Exception:  # noqa: BLE001
                continue
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                try:
                    text = raw.decode("latin-1")
                except Exception:  # noqa: BLE001
                    continue

            ext = Path(name).suffix.lower()
            stem_hint = "tag" in Path(name).stem.lower()
            if ext == ".json":
                try:
                    data = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(data, list):
                    _add_tag_value(data, seen, tags)
                else:
                    _scan_json_for_tags(data, seen, tags)
            else:
                found_line = False
                for line in text.splitlines():
                    m = _TXT_TAG_LINE.match(line)
                    if m:
                        _add_tag_value(m.group(2), seen, tags)
                        found_line = True
                if not found_line:
                    stripped = text.strip()
                    if stripped.startswith("[") and stripped.endswith("]"):
                        try:
                            data = json.loads(stripped)
                        except (json.JSONDecodeError, ValueError):
                            data = None
                        if isinstance(data, list):
                            _add_tag_value(data, seen, tags)
                            found_line = True
                    if not found_line and stem_hint:
                        for line in text.splitlines():
                            _add_tag_value(line.strip(), seen, tags)

        return tags, sorted(names, key=natural_key)

    # ---------------------------------------------------- Adicionar tags --
    TAGS_FILE_DEFAULT_NAME = "Tags.json"

    def _find_tags_json_name(self):
        for n in self._all_entry_names():
            if Path(n).name.lower() == self.TAGS_FILE_DEFAULT_NAME.lower():
                return n
        return None

    def can_write_tags(self) -> bool:
        # Arquivos materializados de uma coleção compactada são temporários:
        # permitir escrita neles faria a alteração sumir ao fechar a coleção.
        if getattr(self, "read_only_override", False):
            return False
        # Apenas CBZ e pasta mantêm o comportamento de escrita original.
        return self.kind in ("zip", "dir")

    def write_entry(self, name: str, data: bytes):
        if self.kind == "dir":
            (self.path / name).write_bytes(data)
            return

        if self.kind == "zip":
            if self._zip_pool is not None:
                self._zip_pool.close()
                self._zip_pool = None
                self._zip = None
            elif self._zip is not None:
                self._zip.close()
                self._zip = None
            tmp_path = self.path.with_name(self.path.name + ".tmp")
            try:
                with zipfile.ZipFile(self.path, "r") as zin, \
                        zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
                    replaced = False
                    for item in zin.infolist():
                        if item.filename == name:
                            zout.writestr(name, data)
                            replaced = True
                        else:
                            zout.writestr(item, zin.read(item.filename))
                    if not replaced:
                        zout.writestr(name, data)
                tmp_path.replace(self.path)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                self._zip = zipfile.ZipFile(self.path, "r")
                self._zip_pool = _ZipReaderPool(self.path, self._zip, max_handles=3)
            return

        raise ArchiveError(tr("error.tags_write_format"))

    def add_tags(self, raw_text: str):
        if not self.can_write_tags():
            raise ArchiveError(tr("error.tags_add_format"))

        candidates = [t.strip() for t in re.split(r"[,\s]+", raw_text or "") if t.strip()]
        if not candidates:
            return [], []

        existing_tags, _ = self.gather_metadata()
        existing_lower = {t.lower() for t in existing_tags}
        added, skipped = [], []
        seen_in_batch = set()
        for tag in candidates:
            low = tag.lower()
            if low in existing_lower or low in seen_in_batch:
                skipped.append(tag)
                continue
            seen_in_batch.add(low)
            added.append(tag)

        if not added:
            return added, skipped

        tags_entry_name = self._find_tags_json_name() or self.TAGS_FILE_DEFAULT_NAME
        current_list = []
        if self._find_tags_json_name():
            try:
                raw = self._read_entry_bytes(tags_entry_name)
                data = json.loads(raw.decode("utf-8-sig"))
                if isinstance(data, list):
                    current_list = [str(x) for x in data]
            except Exception:  # noqa: BLE001
                current_list = []

        current_list.extend(added)
        payload = json.dumps(current_list, ensure_ascii=False, indent=2).encode("utf-8")
        self.write_entry(tags_entry_name, payload)
        return added, skipped

    def close(self):
        # O lock garante que uma leitura CBR/read_bytes em andamento termine
        # antes de fecharmos handles compartilhados.
        with self._lock:
            self._raw_cache.clear()
            self._raw_cache_bytes = 0
            for event in self._raw_inflight.values():
                event.set()
            self._raw_inflight.clear()
            self._zip_names = None
            self._single_image_frame = None
            self._single_image_bytes = None
            if self._zip_pool:
                self._zip_pool.close()
                self._zip_pool = None
                self._zip = None
            elif self._zip:
                self._zip.close()
                self._zip = None
            if self._rar:
                self._rar.close()
                self._rar = None
            if self._pdf_pool:
                self._pdf_pool.close()
                self._pdf_pool = None
                self._pdf = None
            elif self._pdf:
                self._pdf.close()
                self._pdf = None
