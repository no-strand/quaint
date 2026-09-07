"""Funções puras usadas pelo comando 'Salvar página como'."""
from pathlib import Path

from PIL import Image

from app.i18n import tr


def image_save_filter():
    return ";;".join((
        tr("save_filter.png"), tr("save_filter.jpeg"), tr("save_filter.webp"),
        tr("save_filter.gif"), tr("save_filter.tiff"), tr("save_filter.bmp"),
        tr("save_filter.ico"), tr("save_filter.webm"),
    ))


READABLE_IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif",
    ".tif", ".tiff", ".bmp", ".ico", ".webm",
}



def initial_save_directory(session_dir=None, archive_path=None, archive_kind=None, collection_path=None):
    """Escolhe a pasta inicial do diálogo Salvar como.

    Prioridade:
    1) última pasta usada com sucesso nesta sessão;
    2) pasta do compactado externo, quando a HQ veio de uma coleção;
    3) pasta da imagem/arquivo atualmente aberto;
    4) diretório pessoal.
    """
    if session_dir:
        remembered = Path(session_dir)
        if remembered.is_dir():
            return remembered

    if collection_path:
        return Path(collection_path).expanduser().resolve().parent

    if archive_path:
        path = Path(archive_path).expanduser().resolve()
        if archive_kind == "dir":
            return path
        return path.parent

    return Path.home()

def ensure_extension(path, selected_filter=""):
    """Adiciona a extensão correspondente ao filtro quando o usuário a omite."""
    p = Path(path)
    if p.suffix:
        return p
    selected = (selected_filter or "").upper()
    choices = (
        ("WEBM", ".webm"),
        ("WEBP", ".webp"),
        ("JPEG", ".jpg"),
        ("JPG", ".jpg"),
        ("GIF", ".gif"),
        ("TIFF", ".tif"),
        ("TIF", ".tif"),
        ("BMP", ".bmp"),
        ("ICO", ".ico"),
        ("PNG", ".png"),
    )
    for token, ext in choices:
        if token in selected:
            return p.with_suffix(ext)
    return p.with_suffix(".png")


def build_save_targets(selected_path, page_indices, selected_filter=""):
    """Retorna pares ``(índice, caminho)`` para as páginas exibidas.

    Uma página mantém exatamente o caminho escolhido. Para duas páginas,
    usa o nome escolhido como base e acrescenta o número real de cada página,
    por exemplo ``paginas_001.png`` e ``paginas_002.png``.
    """
    indices = []
    for value in page_indices:
        if value is None:
            continue
        value = int(value)
        if value not in indices:
            indices.append(value)
    indices.sort()
    if not indices:
        return []

    p = ensure_extension(selected_path, selected_filter)
    if len(indices) == 1:
        return [(indices[0], p)]

    base = p.stem
    ext = p.suffix
    return [
        (index, p.with_name(f"{base}_{index + 1:03d}{ext}"))
        for index in indices
    ]


def save_converted_image(image: Image.Image, target, quality=95):
    """Salva uma imagem convertendo-a para o formato indicado pela extensão.

    WEBM é suportado como um arquivo de um único quadro, para manter o mesmo
    conjunto de formatos disponíveis tanto na abertura quanto no Salvamento.
    """
    target = Path(target)
    ext = target.suffix.lower()
    if ext not in READABLE_IMAGE_EXTS:
        raise ValueError(tr("error.save_image_format", ext=(ext or tr("error.no_extension"))))

    if ext == ".webm":
        try:
            import imageio.v2 as imageio
            import numpy as np
        except ImportError as e:
            raise RuntimeError(tr("error.save_webm_support")) from e
        frame = np.asarray(image.convert("RGB"))
        writer = imageio.get_writer(
            str(target), fps=1, codec="libvpx-vp9", quality=8, macro_block_size=None
        )
        try:
            writer.append_data(frame)
        finally:
            writer.close()
        return

    if ext in {".jpg", ".jpeg", ".jfif"}:
        image.convert("RGB").save(str(target), format="JPEG", quality=int(quality))
        return
    if ext == ".png":
        mode = "RGBA" if "A" in image.getbands() else "RGB"
        image.convert(mode).save(str(target), format="PNG")
        return
    if ext == ".webp":
        mode = "RGBA" if "A" in image.getbands() else "RGB"
        image.convert(mode).save(str(target), format="WEBP", quality=int(quality))
        return
    if ext == ".gif":
        # GIF usa paleta indexada; ADAPTIVE oferece uma conversão visual melhor.
        image.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE).save(
            str(target), format="GIF"
        )
        return
    if ext in {".tif", ".tiff"}:
        mode = "RGBA" if "A" in image.getbands() else "RGB"
        image.convert(mode).save(str(target), format="TIFF")
        return
    if ext == ".bmp":
        image.convert("RGB").save(str(target), format="BMP")
        return
    if ext == ".ico":
        image.convert("RGBA").save(str(target), format="ICO")
        return

    raise ValueError(tr("error.save_image_format", ext=ext))
