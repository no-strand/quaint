from app.archive import IMG_EXTS, SUPPORTED_FILE_EXTS


def test_modern_formats_are_recognized_everywhere():
    expected = {'.avif', '.heic', '.heif', '.jxl', '.svg', '.svgz'}
    assert expected <= IMG_EXTS
    assert expected <= SUPPORTED_FILE_EXTS


def test_svg_decodes_as_real_image(tmp_path):
    import pytest
    pytest.importorskip("PySide6.QtSvg")
    from app.archive import ComicArchive

    svg = tmp_path / "sample.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32">'
        '<rect width="64" height="32" fill="#336699"/></svg>',
        encoding="utf-8",
    )
    archive = ComicArchive(svg)
    try:
        image = archive.load_image(0)
        assert image.size == (64, 32)
        assert image.mode in {"RGB", "RGBA"}
    finally:
        archive.close()
