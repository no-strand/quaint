import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

from app.image_effects import (
    ADJUSTMENT_KEYS,
    DEFAULT_ADJUSTMENTS,
    apply_image_effects,
    normalize_adjustments,
    rotate_image,
)
from app.save_helpers import initial_save_directory
from app.wallpaper import save_wallpaper_bitmap


def _different(a, b):
    return ImageChops.difference(a.convert("RGB"), b.convert("RGB")).getbbox() is not None


def main():
    # Compatibilidade com as configurações antigas de 3 controles.
    old = normalize_adjustments((1.1, 0.9, 1.2))
    assert len(old) == len(ADJUSTMENT_KEYS)
    assert old[:3] == (1.1, 0.9, 1.2)
    assert old[3:] == tuple(DEFAULT_ADJUSTMENTS[k] for k in ADJUSTMENT_KEYS[3:])

    # Imagem com sombras, meios-tons e destaques para exercitar os controles.
    arr = np.zeros((80, 120, 3), dtype=np.uint8)
    arr[:, :40] = (30, 40, 60)
    arr[:, 40:80] = (120, 100, 80)
    arr[:, 80:] = (225, 215, 200)
    base = Image.fromarray(arr, "RGB")

    neutral = apply_image_effects(base, DEFAULT_ADJUSTMENTS)
    assert not _different(base, neutral), "ajustes neutros devem preservar a imagem"

    cases = {
        "brightness": 1.25,
        "contrast": 1.25,
        "saturation": 1.5,
        "highlights": 0.7,
        "exposure": 0.6,
        "shadows": 0.7,
        "vignette": 0.8,
        "warmth": 0.7,
        "tint": 0.7,
        "gamma": 1.7,
        "hue": 0.35,
        "whites": 0.7,
        "blacks": -0.7,
        "sharpness": 1.4,
        "auto_levels": 1.0,
    }
    for key, value in cases.items():
        settings = dict(DEFAULT_ADJUSTMENTS)
        settings[key] = value
        out = apply_image_effects(base, settings)
        assert out.size == base.size
        assert _different(base, out), f"o controle {key} deve alterar a imagem"

    rotated_right = rotate_image(base, 90)
    rotated_left = rotate_image(base, -90)
    assert rotated_right.size == (80, 120)
    assert rotated_left.size == (80, 120)
    assert rotate_image(base, 360).size == base.size

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source_dir = root / "origem"
        source_dir.mkdir()
        image_path = source_dir / "foto.jpg"
        image_path.write_bytes(b"x")

        first = initial_save_directory(
            archive_path=image_path, archive_kind="image"
        )
        assert first == source_dir.resolve()

        saved_dir = root / "salvos"
        saved_dir.mkdir()
        remembered = initial_save_directory(
            session_dir=saved_dir,
            archive_path=image_path,
            archive_kind="image",
        )
        assert remembered == saved_dir

        folder_archive = root / "pasta_imagens"
        folder_archive.mkdir()
        assert initial_save_directory(
            archive_path=folder_archive, archive_kind="dir"
        ) == folder_archive.resolve()

        outer = root / "colecao.zip"
        outer.write_bytes(b"x")
        temp_member = root / "temp" / "capitulo.cbz"
        assert initial_save_directory(
            archive_path=temp_member,
            archive_kind="zip",
            collection_path=outer,
        ) == root.resolve()

        bmp = save_wallpaper_bitmap(base, root / "wallpaper.bmp")
        assert bmp.exists() and bmp.stat().st_size > 0
        with Image.open(bmp) as check:
            assert check.format == "BMP"
            assert check.size == base.size

    project = Path(__file__).resolve().parents[1]
    reader = (project / "app" / "reader_window.py").read_text(encoding="utf-8")
    dialog = (project / "app" / "adjustments_dialog.py").read_text(encoding="utf-8")
    provider = (project / "app" / "pixmap_provider.py").read_text(encoding="utf-8")

    assert 'QAction(tr("action.rotate_left")' in reader
    assert 'QAction(tr("action.rotate_right")' in reader
    assert 'QAction(tr("action.wallpaper")' in reader
    assert "self._session_save_dir = None" in reader
    assert "self._session_save_dir = str(Path(saved[0]).parent)" in reader

    for key in (
        "adjustments.brightness", "adjustments.exposure", "adjustments.contrast",
        "adjustments.highlights", "adjustments.shadows", "adjustments.saturation",
        "adjustments.warmth", "adjustments.tint", "adjustments.vignette",
        "adjustments.gamma", "adjustments.hue", "adjustments.whites",
        "adjustments.blacks", "adjustments.sharpness", "adjustments.auto_levels",
    ):
        assert f'tr("{key}")' in dialog

    # Regressão: a janela não pode voltar a exibir conteúdo branco com texto
    # claro e mover os sliders não pode invalidar o cache-base/reler o arquivo.
    assert 'QWidget#adjustmentsContent' in dialog
    assert 'background-color: #1c1c24' in dialog
    assert 'self._emit_timer.setInterval(30)' in dialog
    set_adjustments_body = provider.split('def set_adjustments(self, *values):', 1)[1].split('def rotation_for', 1)[0]
    assert 'self.cache.clear()' not in set_adjustments_body
    assert 'self._generation += 1' not in set_adjustments_body
    assert 'self._display_cache.clear()' in set_adjustments_body
    assert 'adjustments=None' in provider
    assert '_pixmap_to_pil(base_pixmap)' in provider

    print("Salvar na pasta da sessão + rotação + papel de parede + ajustes sem recarga: OK")


if __name__ == "__main__":
    main()
