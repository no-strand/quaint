"""Coleções mistas de pasta e arquivos compactados.

Uma coleção pode reunir imagens avulsas, CBZ/CBR e outros compactados. O
painel de miniaturas usa dois escopos: imagens avulsas da coleção ou páginas
internas do arquivo atualmente aberto. Toda a leitura continua lazy para não
decodificar milhares de imagens durante a abertura.
"""
from __future__ import annotations

import io
import shutil
import tarfile
import tempfile
import threading
import zipfile
from pathlib import Path, PurePosixPath

from PIL import Image

from app.archive import (
    ArchiveError, ComicArchive, STANDALONE_IMAGE_EXTS, _ZipReaderPool, natural_key,
)
from app.animation_decode import ANIMATION_CANDIDATE_EXTS
from app.i18n import tr
from app.index_cache import load_index, save_index_async

_RARFILE = ...
_PY7ZR = ...
_OPTIONAL_LOCK = threading.Lock()

def _rarfile_module():
    global _RARFILE
    if _RARFILE is ...:
        with _OPTIONAL_LOCK:
            if _RARFILE is ...:
                try:
                    import rarfile as module
                except Exception:
                    module = None
                _RARFILE = module
    return _RARFILE

def _py7zr_module():
    global _PY7ZR
    if _PY7ZR is ...:
        with _OPTIONAL_LOCK:
            if _PY7ZR is ...:
                try:
                    import py7zr as module
                except Exception:
                    module = None
                _PY7ZR = module
    return _PY7ZR

COMIC_MEMBER_EXTS = {".cbz", ".cbr"}
IMAGE_MEMBER_EXTS = set(STANDALONE_IMAGE_EXTS)
SUPPORTED_MEMBER_EXTS = COMIC_MEMBER_EXTS | IMAGE_MEMBER_EXTS
CONTAINER_SUFFIXES = (
    ".zip", ".rar", ".7z", ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2",
    ".tar.xz", ".txz",
)


def is_container_path(path) -> bool:
    name = str(path).lower()
    return any(name.endswith(ext) for ext in CONTAINER_SUFFIXES)


def _safe_relative_name(name: str) -> str | None:
    name = str(name).replace("\\", "/")
    p = PurePosixPath(name)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        return None
    if p.parts and ":" in p.parts[0]:
        return None
    return p.as_posix()


class CompressedComicCollection:
    """Representa um compactado como uma pasta virtual de CBZ/CBR + imagens."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists() or not self.path.is_file():
            raise ArchiveError(tr("error.container_not_found", path=self.path))
        if not is_container_path(self.path):
            raise ArchiveError(tr("error.unsupported_container"))

        self._temp = tempfile.TemporaryDirectory(prefix="quaint_collection_")
        self.root = Path(self._temp.name)
        self._lock = threading.RLock()
        self._closed = False
        self._zip_pool = None
        self._active_index = None
        # (nome seguro exibido, nome original dentro do contêiner, tipo)
        # tipo = "comic" ou "image"
        self._members: list[tuple[str, str, str]] = []
        self._materialized: dict[int, Path] = {}

        scanned = False
        try:
            cached = load_index(self.path, "container_members")
            if cached is not None:
                try:
                    self._members = [
                        (str(item[0]), str(item[1]), str(item[2]))
                        for item in cached
                        if isinstance(item, (list, tuple)) and len(item) >= 3
                    ]
                except Exception:
                    self._members = []
            if not self._members:
                self._scan_members()
                scanned = True
        except Exception:
            self.close()
            raise

        if not self._members:
            self.close()
            raise ArchiveError(tr("error.container_no_items"))
        self._members.sort(key=lambda item: natural_key(item[0]))
        if scanned:
            save_index_async(
                self.path,
                "container_members",
                [[safe, raw, kind] for safe, raw, kind in self._members],
            )

    # ------------------------------------------------------------ indexação --
    @staticmethod
    def _accepted(raw_name: str):
        safe = _safe_relative_name(raw_name)
        if safe is None:
            return None
        suffix = Path(safe).suffix.lower()
        if suffix in COMIC_MEMBER_EXTS:
            kind = "comic"
        elif suffix in IMAGE_MEMBER_EXTS:
            kind = "image"
        elif is_container_path(safe):
            kind = "archive"
        else:
            return None
        return safe, str(raw_name), kind

    def _scan_members(self):
        lower = self.path.name.lower()
        if lower.endswith(".zip"):
            try:
                with zipfile.ZipFile(self.path, "r") as z:
                    for info in z.infolist():
                        if info.is_dir():
                            continue
                        item = self._accepted(info.filename)
                        if item:
                            self._members.append(item)
            except zipfile.BadZipFile as e:
                raise ArchiveError(tr("error.zip_invalid", error=e)) from e
            return

        if lower.endswith(".rar"):
            rarfile = _rarfile_module()
            if rarfile is None:
                raise ArchiveError(tr("error.rar_support"))
            try:
                with rarfile.RarFile(self.path, "r") as r:
                    for info in r.infolist():
                        if info.isdir():
                            continue
                        item = self._accepted(info.filename)
                        if item:
                            self._members.append(item)
            except rarfile.Error as e:
                raise ArchiveError(tr("error.rar_invalid", error=e)) from e
            return

        if lower.endswith(".7z"):
            py7zr = _py7zr_module()
            if py7zr is None:
                raise ArchiveError(tr("error.sevenz_support"))
            try:
                with py7zr.SevenZipFile(self.path, mode="r") as z:
                    for raw in z.getnames():
                        item = self._accepted(raw)
                        if item:
                            self._members.append(item)
            except Exception as e:  # noqa: BLE001
                raise ArchiveError(tr("error.sevenz_invalid", error=e)) from e
            return

        if any(lower.endswith(ext) for ext in (
            ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"
        )):
            try:
                with tarfile.open(self.path, mode="r:*") as t:
                    for info in t.getmembers():
                        if not info.isfile():
                            continue
                        item = self._accepted(info.name)
                        if item:
                            self._members.append(item)
            except (tarfile.TarError, OSError) as e:
                raise ArchiveError(tr("error.tar_invalid", error=e)) from e
            return

        raise ArchiveError(tr("error.unsupported_container"))

    # ------------------------------------------------------------- extração --
    @staticmethod
    def _copy_stream(src, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb", buffering=2 * 1024 * 1024) as out:
            shutil.copyfileobj(src, out, length=2 * 1024 * 1024)

    def _dest_for(self, safe: str) -> Path:
        return self.root.joinpath(*PurePosixPath(safe).parts)

    def _extract_one(self, safe: str, raw: str, dest: Path):
        lower = self.path.name.lower()
        if lower.endswith(".zip"):
            # Reutiliza handles ZIP persistentes. Em contêineres com milhares
            # de imagens isso evita reler o diretório central a cada página.
            if self._zip_pool is None:
                self._zip_pool = _ZipReaderPool(self.path, max_handles=3)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Um CBZ/arquivo interno pode ter centenas de MB. Escreve em
            # streaming para não duplicar o membro inteiro na RAM.
            self._zip_pool.copy_to(raw, dest)
            return

        if lower.endswith(".rar"):
            rarfile = _rarfile_module()
            if rarfile is None:
                raise ArchiveError(tr("error.rar_support"))
            try:
                with rarfile.RarFile(self.path, "r") as r:
                    with r.open(raw) as src:
                        self._copy_stream(src, dest)
            except Exception as e:  # noqa: BLE001
                raise ArchiveError(tr("error.extract_rar", name=safe)) from e
            return

        if lower.endswith(".7z"):
            py7zr = _py7zr_module()
            if py7zr is None:
                raise ArchiveError(tr("error.sevenz_support"))
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                with py7zr.SevenZipFile(self.path, mode="r") as z:
                    z.extract(path=self.root, targets=[raw], recursive=True)
            except Exception as e:  # noqa: BLE001
                raise ArchiveError(tr("error.extract_sevenz", name=safe, error=e)) from e
            if not dest.is_file():
                # Alguns arquivos 7Z usam barras invertidas; tenta localizar o
                # basename dentro da árvore temporária antes de falhar.
                matches = list(self.root.rglob(Path(safe).name))
                if matches:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if matches[0] != dest:
                        shutil.move(str(matches[0]), str(dest))
            return

        try:
            with tarfile.open(self.path, mode="r:*") as t:
                info = t.getmember(raw)
                src = t.extractfile(info)
                if src is None:
                    raise ArchiveError(tr("error.read_tar", name=safe))
                with src:
                    self._copy_stream(src, dest)
        except (tarfile.TarError, KeyError, OSError) as e:
            raise ArchiveError(tr("error.extract_tar", name=safe, error=e)) from e

    # --------------------------------------------------------------- API --
    def count(self) -> int:
        return len(self._members)

    def member_name(self, index: int) -> str:
        return self._members[index][0]

    def member_kind(self, index: int) -> str:
        return self._members[index][2]

    def member_is_image(self, index: int) -> bool:
        return self.member_kind(index) == "image"

    def image_member_indices(self):
        """Índices das imagens avulsas da coleção, em ordem natural."""
        return [i for i, item in enumerate(self._members) if item[2] == "image"]

    def has_non_image_members(self) -> bool:
        return any(item[2] != "image" for item in self._members)

    def load_member_image(self, index: int, max_dim=None):
        """Decodifica uma imagem avulsa sem manter extrações em massa."""
        index = int(index)
        if self.member_kind(index) != "image":
            raise ArchiveError(tr("error.member_not_loose_image"))
        suffix = Path(self.member_name(index)).suffix.lower()
        data = self._read_member_bytes_direct(index)
        # py7zr pode precisar materializar o membro para lê-lo. Assim que os
        # bytes estão em memória, descarte imagens não ativas para que rolar
        # milhares de miniaturas não encha o diretório temporário.
        if str(self.path).lower().endswith(".7z") and index != self._active_index:
            self.discard_member(index)
        if suffix == ".webm":
            return ComicArchive._load_webm_bytes_first_frame(data, max_dim)
        if suffix in (".svg", ".svgz", ".jxl"):
            return ComicArchive._decode_modern_bytes(data, suffix, max_dim)
        with Image.open(io.BytesIO(data)) as img:
            return ComicArchive._prepare_pil_image(img, max_dim)

    def member_animation_source(self, index: int):
        if self.member_kind(index) != "image":
            return None
        suffix = Path(self.member_name(index)).suffix.lower()
        if suffix not in ANIMATION_CANDIDATE_EXTS:
            return None
        return {"data": self._read_member_bytes_direct(index), "suffix": suffix}

    def member_path(self, index: int) -> Path:
        """Materializa somente este CBZ/CBR e devolve seu caminho temporário."""
        index = int(index)
        with self._lock:
            if self._closed:
                raise ArchiveError(tr("error.collection_closed"))
            existing = self._materialized.get(index)
            if existing is not None and existing.is_file():
                return existing
            safe, raw, _kind = self._members[index]
            dest = self._dest_for(safe)
            if not dest.is_file():
                self._extract_one(safe, raw, dest)
            if not dest.is_file():
                raise ArchiveError(tr("error.materialize_failed", name=safe))
            self._materialized[index] = dest
            return dest

    def _read_member_bytes_direct(self, index: int) -> bytes:
        """Lê um membro sem deixá-lo extraído na pasta temporária.

        É usado principalmente pelas capas de imagens no Sumário. Assim,
        percorrer milhares de miniaturas não deixa milhares de arquivos
        materializados em disco.
        """
        index = int(index)
        safe, raw, _kind = self._members[index]
        lower = self.path.name.lower()

        if lower.endswith(".zip"):
            with self._lock:
                if self._closed:
                    raise ArchiveError(tr("error.collection_closed"))
                if self._zip_pool is None:
                    self._zip_pool = _ZipReaderPool(self.path, max_handles=3)
                pool = self._zip_pool
            return pool.read(raw)

        if lower.endswith(".rar"):
            rarfile = _rarfile_module()
            if rarfile is None:
                raise ArchiveError(tr("error.rar_support"))
            try:
                with rarfile.RarFile(self.path, "r") as r:
                    with r.open(raw) as src:
                        return src.read()
            except Exception as e:  # noqa: BLE001
                raise ArchiveError(tr("error.read_rar", name=safe)) from e

        if any(lower.endswith(ext) for ext in (
            ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"
        )):
            try:
                with tarfile.open(self.path, mode="r:*") as t:
                    info = t.getmember(raw)
                    src = t.extractfile(info)
                    if src is None:
                        raise ArchiveError(tr("error.read_tar", name=safe))
                    with src:
                        return src.read()
            except (tarfile.TarError, KeyError, OSError) as e:
                raise ArchiveError(tr("error.read_tar_detail", name=safe, error=e)) from e

        # py7zr não oferece uma API de stream estável em todas as versões.
        # Para 7Z materializamos apenas este membro e o chamador pode descartá-lo
        # logo depois. Ainda assim, a abertura inicial continua 100% lazy.
        path = self.member_path(index)
        return path.read_bytes()

    def load_member_cover(self, index: int, max_dim=220):
        """Retorna a capa/primeiro quadro sem materializar imagens em massa."""
        index = int(index)
        kind = self.member_kind(index)
        if kind in ("comic", "archive"):
            path = self.member_path(index)
            archive = ContainerImageArchive(path) if kind == "archive" else ComicArchive(path)
            try:
                return archive.load_image(0, max_dim)
            finally:
                archive.close()
                # Compactados genéricos podem ser muito grandes e aparecem
                # apenas como capa no Sumário; não acumule centenas deles no
                # diretório temporário. CBZ/CBR continuam em cache.
                if kind == "archive" and index != self._active_index:
                    self.discard_member(index)

        try:
            return self.load_member_image(index, max_dim)
        finally:
            # Em 7Z a leitura direta acima pode ter precisado materializar o
            # arquivo. Se ele não for a página ativa, remove imediatamente.
            if str(self.path).lower().endswith(".7z") and index != self._active_index:
                self.discard_member(index)

    def set_active_member(self, index):
        with self._lock:
            self._active_index = None if index is None else int(index)

    def discard_member(self, index: int):
        """Remove uma imagem temporária que já não está em uso.

        CBZ/CBR continuam em cache temporário como antes, pois reextraí-los ao
        voltar de capítulo costuma custar mais. Imagens e compactados genéricos
        podem existir aos milhares; manter somente o item ativo evita crescimento
        indefinido do diretório temporário.
        """
        index = int(index)
        with self._lock:
            if self._closed or index == self._active_index:
                return
            if not (0 <= index < len(self._members)):
                return
            if self.member_kind(index) not in ("image", "archive"):
                return
            path = self._materialized.pop(index, None)
        if path is not None:
            try:
                path.unlink(missing_ok=True)
                # Remove diretórios vazios criados para caminhos internos.
                parent = path.parent
                while parent != self.root:
                    try:
                        parent.rmdir()
                    except OSError:
                        break
                    parent = parent.parent
            except OSError:
                pass

    def member_display_name(self, index: int) -> str:
        return self.member_name(index)

    def progress_key(self, index: int) -> str:
        try:
            outer = str(self.path.resolve())
        except OSError:
            outer = str(self.path)
        return f"{outer}::{self.member_name(index)}"

    def display_name(self) -> str:
        return self.path.name

    def materialized_count(self) -> int:
        with self._lock:
            return len(self._materialized)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._active_index = None
            self._materialized.clear()
            zip_pool = self._zip_pool
            self._zip_pool = None
            if zip_pool is not None:
                try:
                    zip_pool.close()
                except Exception:
                    pass
            try:
                self._temp.cleanup()
            except Exception:
                pass

    def __len__(self):
        return self.count()

    def __del__(self):  # pragma: no cover
        try:
            self.close()
        except Exception:
            pass


class DirectoryComicCollection:
    """Pasta mista de imagens, CBZ/CBR e outros compactados."""

    def __init__(self, path):
        import os
        self.path = Path(path)
        if not self.path.is_dir():
            raise ArchiveError(tr("error.folder_not_found", path=self.path))
        self._members = []
        cached = load_index(self.path, "dir_collection")
        if cached is not None:
            try:
                self._members = [
                    (str(item[0]), self.path / str(item[0]), str(item[1]))
                    for item in cached
                    if isinstance(item, (list, tuple)) and len(item) >= 2
                ]
            except Exception:
                self._members = []
        if not self._members:
            try:
                with os.scandir(self.path) as entries:
                    for entry in entries:
                        if not entry.is_file():
                            continue
                        name = entry.name
                        suffix = Path(name).suffix.lower()
                        if suffix in COMIC_MEMBER_EXTS:
                            kind = "comic"
                        elif suffix in IMAGE_MEMBER_EXTS:
                            kind = "image"
                        elif is_container_path(name):
                            kind = "archive"
                        else:
                            continue
                        self._members.append((name, Path(entry.path), kind))
            except OSError as e:
                raise ArchiveError(tr("error.folder_list", error=e)) from e
            self._members.sort(key=lambda item: natural_key(item[0]))
            if self._members:
                save_index_async(
                    self.path, "dir_collection",
                    [[name, kind] for name, _path, kind in self._members],
                )
        if not self._members:
            raise ArchiveError(tr("error.folder_no_collection_items"))

    def count(self):
        return len(self._members)

    def member_name(self, index):
        return self._members[int(index)][0]

    def member_display_name(self, index):
        return self.member_name(index)

    def member_kind(self, index):
        return self._members[int(index)][2]

    def member_is_image(self, index):
        return self.member_kind(index) == "image"

    def image_member_indices(self):
        return [i for i, item in enumerate(self._members) if item[2] == "image"]

    def has_non_image_members(self):
        return any(item[2] != "image" for item in self._members)

    def image_page_names(self):
        """Nomes já indexados para reaproveitar a varredura em pasta só-imagem."""
        return [item[0] for item in self._members if item[2] == "image"]

    def member_path(self, index):
        return self._members[int(index)][1]

    def set_active_member(self, _index):
        pass

    def discard_member(self, _index):
        pass

    def load_member_image(self, index, max_dim=None):
        path = self.member_path(index)
        if self.member_kind(index) != "image":
            raise ArchiveError(tr("error.member_not_loose_image"))
        suffix = path.suffix.lower()
        if suffix == ".webm":
            return ComicArchive._load_webm_first_frame(path, max_dim)
        if suffix in (".svg", ".svgz", ".jxl"):
            return ComicArchive._decode_modern_path(path, max_dim)
        with Image.open(path) as img:
            return ComicArchive._prepare_pil_image(img, max_dim)

    def member_animation_source(self, index):
        if self.member_kind(index) != "image":
            return None
        path = self.member_path(index)
        suffix = path.suffix.lower()
        if suffix not in ANIMATION_CANDIDATE_EXTS:
            return None
        return {"path": str(path), "suffix": suffix}

    def load_member_cover(self, index, max_dim=220):
        kind = self.member_kind(index)
        if kind == "image":
            return self.load_member_image(index, max_dim)
        path = self.member_path(index)
        archive = ContainerImageArchive(path) if kind == "archive" else ComicArchive(path)
        try:
            return archive.load_image(0, max_dim)
        finally:
            archive.close()

    def progress_key(self, index):
        try:
            outer = str(self.path.resolve())
        except OSError:
            outer = str(self.path)
        return f"{outer}::{self.member_name(index)}"

    def display_name(self):
        return self.path.name

    def materialized_count(self):
        return 0

    def close(self):
        pass

    def __len__(self):
        return self.count()


class CollectionImageGroupArchive:
    """Visão virtual somente das imagens avulsas da coleção para miniaturas."""

    kind = "collection_images"

    def __init__(self, collection):
        self.collection = collection
        self.path = collection.path
        self.member_indices = collection.image_member_indices()

    def count(self):
        return len(self.member_indices)

    def is_text_page(self, _index):
        return False

    def has_text_pages(self):
        return False

    def page_name(self, index):
        return self.collection.member_name(self.member_indices[int(index)])

    def page_suffix(self, index):
        return Path(self.page_name(index)).suffix.lower()

    def load_image(self, index, max_dim=None):
        return self.collection.load_member_image(self.member_indices[int(index)], max_dim)

    def animation_candidate(self, index):
        return self.page_suffix(index) in ANIMATION_CANDIDATE_EXTS

    def animation_source(self, index):
        return self.collection.member_animation_source(self.member_indices[int(index)])

    def close(self):
        pass


class ContainerImageArchive:
    """Compactado interno tratado como páginas de suas imagens avulsas."""

    kind = "container_images"

    def __init__(self, path):
        self.path = Path(path)
        self._collection = CompressedComicCollection(self.path)
        self._indices = self._collection.image_member_indices()
        if not self._indices:
            self._collection.close()
            raise ArchiveError(tr("error.container_no_loose_images"))
        self.read_only_override = True

    def count(self):
        return len(self._indices)

    def is_text_page(self, _index):
        return False

    def has_text_pages(self):
        return False

    def page_name(self, index):
        return self._collection.member_name(self._indices[int(index)])

    def page_suffix(self, index):
        return Path(self.page_name(index)).suffix.lower()

    def load_image(self, index, max_dim=None):
        return self._collection.load_member_image(self._indices[int(index)], max_dim)

    def animation_candidate(self, index):
        return self.page_suffix(index) in ANIMATION_CANDIDATE_EXTS

    def animation_source(self, index):
        return self._collection.member_animation_source(self._indices[int(index)])

    def text_html(self, _index):
        return ""

    def display_name(self):
        return getattr(self, "display_name_override", None) or self.path.name

    def kind_label(self):
        return tr("type.container_images")

    def file_size_bytes(self):
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def gather_metadata(self):
        return [], []

    def can_write_tags(self):
        return False

    def add_tags(self, _raw_text):
        raise ArchiveError(tr("error.read_only_collection"))

    def close(self):
        self._collection.close()
