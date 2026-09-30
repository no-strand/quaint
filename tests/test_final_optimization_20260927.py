from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_disabled_options_are_visibly_gray_and_text_epub_keeps_continuous_visible():
    style = text("resources/style.qss")
    reader = text("app/reader_window.py")
    assert "QMenu::item:disabled" in style
    assert "QAbstractItemView::item:disabled" in style
    assert "*:disabled" in style
    assert "self.act_mode_continuous.setVisible(True)" in reader
    assert "self.act_mode_continuous.setEnabled(continuous_allowed)" in reader


def test_epub_reopen_index_and_html_memory_are_bounded():
    archive = text("app/archive.py")
    assert 'load_index(self.path, "epub_pages")' in archive
    assert 'save_index_async(self.path, "epub_pages", pages)' in archive
    assert "self._epub_html_cache = OrderedDict()" in archive
    assert "self._epub_html_cache_limit = 16 * 1024 * 1024" in archive
    assert 'entry.get("html_raw"' not in archive
    assert 'body.decode_contents()' not in archive.split("def _parse_epub_document", 1)[1].split("def _prepare_epub_html", 1)[0]


def test_epub_first_open_prefers_light_xml_parser_before_beautifulsoup():
    archive = text("app/archive.py")
    parser = archive.split("def _parse_epub_document", 1)[1].split("def _prepare_epub_html", 1)[0]
    assert "ET.fromstring(raw)" in parser
    assert "except ET.ParseError" in parser
    assert "BeautifulSoup = _beautiful_soup()" in parser


def test_page_dimensions_are_cached_without_pixels_and_pdf_thumbs_render_near_target():
    archive = text("app/archive.py")
    assert "self._page_size_cache = OrderedDict()" in archive
    assert "self._page_size_cache_limit = 512" in archive
    assert "def _compute_page_size" in archive
    assert "max(0.03, float(max_dim) / longest)" in archive


def test_navigation_and_thumbnail_queues_drop_stale_decodes():
    provider = text("app/pixmap_provider.py")
    views = text("app/views.py")
    thumbs = text("app/thumbnail_panel.py")
    assert "def retain_indices" in provider
    assert 'kinds.append("foreground")' in provider
    assert "self.provider.retain_indices([index])" in views
    assert "self.provider.retain_indices(i for i in (left, right) if i is not None)" in views
    assert "self._thumb_provider.retain_indices(image_rows)" in thumbs
    assert "self._cancel_stale_video_tasks(video_rows)" in thumbs
    assert "self._thumb_provider.retain_indices([])" in thumbs


def test_text_thumbnail_mapping_is_compact_per_source_not_per_visual_page():
    epub = text("app/epub_view.py")
    assert "self._source_counts = None" in epub
    assert "self._prefix_ends = None" in epub
    assert "bisect_right(self._prefix_ends, row)" in epub
    assert "self._row_lookup" not in epub
    assert "page_map.append" not in epub


def test_epub_thumbnail_pagination_cache_is_bounded_lru():
    epub = text("app/epub_view.py")
    assert "OrderedDict(cache or ())" in epub
    assert "while len(cache) > 4096" in epub
    assert "cache.popitem(last=False)" in epub


def test_summary_cover_queue_is_bounded_to_current_generation_and_visible_rows():
    summary = text("app/summary_dialog.py")
    assert "self._cover_generation = 0" in summary
    assert "def _cancel_stale_cover_tasks" in summary
    assert "self.pool.tryTake(task)" in summary
    assert "if generation != self._cover_generation:" in summary


def test_continuous_action_also_stays_visible_in_text_epub_context_menu():
    reader = text("app/reader_window.py")
    context = reader.split("def _show_reader_context_menu", 1)[1].split("def contextMenuEvent", 1)[0]
    assert "view_menu.addAction(self.act_mode_continuous)" in context
    assert "if not is_text" not in context.split("view_menu =", 1)[1].split("if is_text", 1)[0]
