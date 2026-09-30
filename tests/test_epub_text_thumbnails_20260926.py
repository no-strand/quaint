from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPUB_VIEW = (ROOT / "app" / "epub_view.py").read_text(encoding="utf-8")
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
THUMBS = (ROOT / "app" / "thumbnail_panel.py").read_text(encoding="utf-8")


def test_epub_thumbnail_archive_expands_text_chapters_into_visual_pages():
    assert "class EpubThumbnailArchive" in EPUB_VIEW
    assert "self._source_counts = None" in EPUB_VIEW
    assert "self._prefix_ends = None" in EPUB_VIEW
    assert "bisect_right(self._prefix_ends, row)" in EPUB_VIEW
    assert "page_map.append" not in EPUB_VIEW
    assert "_configure_epub_document" in EPUB_VIEW
    assert "_paint_document_page" in EPUB_VIEW
    assert "return _qimage_to_pil(image)" in EPUB_VIEW


def test_epub_thumbnail_click_opens_exact_internal_text_page():
    assert "archive_index, inner_page, is_text = self._epub_thumbnail_scope.position" in READER
    assert 'view.go_to_page(int(inner_page or 0))' in READER
    assert "self._sync_thumbnail_current(self.current_index)" in READER


def test_text_thumbnail_scope_uses_normal_pixmap_provider_lazy_loading():
    assert "self._thumb_provider.get(row" in THUMBS
    assert "return False" in EPUB_VIEW[EPUB_VIEW.index("class EpubThumbnailArchive"):EPUB_VIEW.index("def _checker_brush")]
    assert "if self.thumb_panel is None:\n                return" in READER
