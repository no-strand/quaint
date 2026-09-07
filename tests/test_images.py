import io
import tempfile
from pathlib import Path

import imageio.v2 as imageio
import numpy as np
from PIL import Image

from app.archive import ComicArchive, STANDALONE_IMAGE_EXTS
from app.save_helpers import (
    READABLE_IMAGE_EXTS,
    ensure_extension,
    image_save_filter,
    save_converted_image,
)
from app.win_registration import FILE_TYPE_GROUPS


def _make_still(path: Path, fmt: str):
    img = Image.new("RGB", (64, 64), (40, 120, 220))
    if fmt == "GIF":
        img = img.convert("P", palette=Image.Palette.ADAPTIVE)
    img.save(path, format=fmt)


def main():
    expected = {
        ".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif",
        ".tif", ".tiff", ".bmp", ".ico", ".webm",
    }
    assert expected <= STANDALONE_IMAGE_EXTS
    assert expected == READABLE_IMAGE_EXTS

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        formats = {
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
            ".jfif": "JPEG",
            ".png": "PNG",
            ".webp": "WEBP",
            ".gif": "GIF",
            ".tif": "TIFF",
            ".tiff": "TIFF",
            ".bmp": "BMP",
            ".ico": "ICO",
        }
        for ext, fmt in formats.items():
            path = root / f"entrada{ext}"
            _make_still(path, fmt)
            archive = ComicArchive(path)
            assert archive.kind == "image"
            assert archive.count() == 1
            assert archive.page_name(0) == path.name
            with Image.open(io.BytesIO(archive.read_bytes(0))) as check:
                check.load()
                assert check.width > 0 and check.height > 0
            archive.close()
        print("Imagens avulsas Pillow: OK")

        # WEBM: um quadro deve ser exposto como uma imagem/página única.
        webm = root / "entrada.webm"
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        frame[:, :, 1] = 180
        writer = imageio.get_writer(str(webm), fps=1, codec="libvpx-vp9", macro_block_size=None)
        writer.append_data(frame)
        writer.close()
        archive = ComicArchive(webm)
        assert archive.kind == "image" and archive.count() == 1
        with Image.open(io.BytesIO(archive.read_bytes(0))) as check:
            check.load()
            assert check.size == (64, 64)
        archive.close()
        print("WEBM como imagem de um quadro: OK")

        # Conversão de saída para os mesmos formatos de leitura.
        source = Image.new("RGBA", (64, 64), (200, 80, 40, 220))
        for ext in sorted(expected):
            target = root / f"saida{ext}"
            save_converted_image(source, target)
            assert target.exists() and target.stat().st_size > 0
            if ext == ".webm":
                reader = imageio.get_reader(str(target))
                try:
                    out = reader.get_data(0)
                    assert out.shape[0:2] == (64, 64)
                finally:
                    reader.close()
            else:
                with Image.open(target) as check:
                    check.load()
                    assert check.width > 0 and check.height > 0
        print("Conversão para todos os formatos suportados: OK")

    assert ensure_extension("pagina", "TIFF (*.tif *.tiff)").suffix == ".tif"
    assert ensure_extension("pagina", "WEBM (*.webm)").suffix == ".webm"
    assert "WEBM (*.webm)" in image_save_filter()

    image_group = FILE_TYPE_GROUPS["images"]["extensions"]
    assert set(image_group) == expected

    installer = Path(__file__).resolve().parents[1] / "build" / "installer.iss"
    installer_text = installer.read_text(encoding="utf-8")
    assert 'Name: "associateimages"' in installer_text
    for ext in expected:
        assert f'Software\\Classes\\{ext}' in installer_text

    reader_source = (Path(__file__).resolve().parents[1] / "app" / "reader_window.py").read_text(encoding="utf-8")
    assert 'self.archive.kind == "image"' in reader_source
    assert 'tr("status.image_single_only")' in reader_source

    print("Suporte a imagens avulsas/associação: OK")


if __name__ == "__main__":
    main()
