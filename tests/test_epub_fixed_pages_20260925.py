from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
EPUB_VIEW = (ROOT / "app" / "epub_view.py").read_text(encoding="utf-8")
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
DIALOG = (ROOT / "app" / "epub_settings_dialog.py").read_text(encoding="utf-8")


def test_epub_text_is_paginated_on_fixed_page_geometry():
    assert "PAGE_WIDTH = 720" in EPUB_VIEW
    assert "PAGE_HEIGHT = 960" in EPUB_VIEW
    assert "setPageSize(QSizeF(CONTENT_WIDTH, CONTENT_HEIGHT))" in EPUB_VIEW
    assert "document.pageCount()" in EPUB_VIEW
    assert "documentLayout().draw" in EPUB_VIEW


def test_epub_supports_single_and_double_pages_but_forces_ltr():
    assert 'self._mode = "single"' in EPUB_VIEW
    assert 'mode = "double" if mode == "double" else "single"' in EPUB_VIEW
    assert "option.setTextDirection(Qt.LeftToRight)" in EPUB_VIEW
    assert "left + 1 if self._mode == \"double\"" in EPUB_VIEW
    assert 'self.act_mode_double.setEnabled(not single_image)' in READER
    assert 'continuous_allowed = not single_image and not text_epub' in READER
    assert 'self.act_mode_continuous.setVisible(True)' in READER
    assert 'self.archive.has_text_pages() and mode == MODE_CONTINUOUS' in READER


def test_epub_has_light_dark_and_sepia_backgrounds():
    assert 'EPUB_THEME_SEPIA = "sepia"' in EPUB_VIEW
    assert 'def epub_theme(self)' in SETTINGS
    assert 'self.theme_combo.addItem(tr("epub.theme_sepia"), "sepia")' in DIALOG
    for locale in ("pt_BR.json", "en_US.json", "es_ES.json"):
        data = json.loads((ROOT / "locales" / locale).read_text(encoding="utf-8"))
        assert data["epub.theme_light"]
        assert data["epub.theme_dark"]
        assert data["epub.theme_sepia"]


def test_reader_context_menu_is_content_aware():
    assert "def _show_reader_context_menu" in READER
    assert 'is_text = self.archive.is_text_page(self.current_index)' in READER
    assert 'epub_menu = menu.addMenu(tr("action.epub"))' in READER
    assert 'fit_menu = menu.addMenu(tr("context.fit"))' in READER
    assert 'menu.addAction(self.act_rotate_left)' in READER
    assert 'copy_text.triggered.connect(self.copy_epub_chapter_text)' in READER


def test_epub_navigation_walks_internal_pages_before_chapter_change():
    assert "if view.next_pages():" in READER
    assert "if view.prev_pages():" in READER
    assert 'self.go_to_page(prv, epub_position="last")' in READER
    assert 'self.go_to_page(nxt, epub_position="first")' in READER
