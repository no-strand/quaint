from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
VIEWS = (ROOT / "app" / "views.py").read_text(encoding="utf-8")
EPUB = (ROOT / "app" / "epub_view.py").read_text(encoding="utf-8")


def test_epub_cover_uses_double_page_layout_for_text_and_image_epubs():
    assert "def _is_epub_cover_page" in READER
    assert 'self.archive.kind == "epub"' in READER
    assert "and self.mode == MODE_DOUBLE" in READER
    assert "self._show_epub_cover_spread(index)" in READER
    assert "view.show_epub_cover" in READER


def test_epub_cover_is_wrapped_in_logical_paper_sheet():
    assert "def show_epub_cover" in VIEWS
    assert "def _epub_cover_page_pixmap" in VIEWS
    assert "page_w, page_h = 900, 1200" in VIEWS
    assert "framed.fill(self._epub_cover_paper_color)" in VIEWS
    assert "epub_paper_color" in EPUB


def test_regular_spreads_clear_cover_only_rendering_mode():
    assert "self._epub_cover_index = None" in VIEWS
    assert "def show_spread" in VIEWS
