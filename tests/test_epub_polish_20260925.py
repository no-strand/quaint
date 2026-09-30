from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPUB = (ROOT / "app" / "epub_view.py").read_text(encoding="utf-8")
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
DIALOG = (ROOT / "app" / "epub_settings_dialog.py").read_text(encoding="utf-8")


def test_epub_theme_only_colors_paper_not_outer_canvas():
    assert 'palette["canvas"]' not in EPUB
    assert 'def set_background_color(self, color, checker=False):' in EPUB
    assert '"epub_text_view"' in READER
    assert 'checker=self.page_background == BG_CHECKER' in READER


def test_epub_double_page_binding_shadow_follows_global_toggle():
    assert 'def set_shadow_enabled(self, enabled):' in EPUB
    assert 'QLinearGradient' in EPUB
    assert '"right" if double_mode else None' in EPUB
    assert '"left" if right_present else None' in EPUB
    assert 'epub_view.set_shadow_enabled(checked)' in READER


def test_epub_font_size_and_background_controls_use_white_text():
    assert 'self.font_size_label.setStyleSheet("color: #ffffff;")' in DIALOG
    assert 'QSpinBox { color: #ffffff; }' in DIALOG
    assert 'self.theme_label.setStyleSheet("color: #ffffff;")' in DIALOG
    assert 'QComboBox { color: #ffffff; }' in DIALOG


def test_comma_and_period_are_fixed_navigation_shortcuts():
    assert 'self.act_prev_comma.setShortcut(QKeySequence(","))' in READER
    assert 'self.act_prev_comma.triggered.connect(self.prev_page)' in READER
    assert 'self.act_next_period.setShortcut(QKeySequence("."))' in READER
    assert 'self.act_next_period.triggered.connect(self.next_page)' in READER
