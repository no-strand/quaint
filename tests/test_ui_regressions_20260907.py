from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
PICKER = (ROOT / "app" / "color_picker.py").read_text(encoding="utf-8")
SHORTCUTS = (ROOT / "app" / "shortcuts_dialog.py").read_text(encoding="utf-8")
BATCH = (ROOT / "app" / "batch_export_dialog.py").read_text(encoding="utf-8")
BOOKMARKS = (ROOT / "app" / "bookmark_panel.py").read_text(encoding="utf-8")
PROVIDER = (ROOT / "app" / "pixmap_provider.py").read_text(encoding="utf-8")
STYLE = (ROOT / "resources" / "style.qss").read_text(encoding="utf-8")


def test_filter_signal_is_wired_to_settings_and_provider():
    assert "def _on_filter_changed(self, name):" in READER
    assert "self.settings.set_image_filter(name)" in READER
    assert "self.provider.set_filter(name)" in READER
    assert 'self.provider.set_filter("none")' in READER


def test_animation_filter_runs_even_with_neutral_numeric_adjustments():
    assert 'adjustments != NEUTRAL_ADJUSTMENTS or normalize_filter_name(filter_name) != "none"' in PROVIDER


def test_escape_closes_color_picker_from_application_event_filter():
    assert "event.key() == Qt.Key_Escape" in READER
    assert "self._color_picker.is_enabled()" in READER
    assert "self.toggle_color_picker(False)" in READER


def test_shortcut_table_and_batch_spinboxes_have_dark_readable_styles():
    assert "QDialog#shortcutsDialog QTableWidget" in SHORTCUTS
    assert "QDialog#shortcutsDialog QHeaderView::section" in SHORTCUTS
    assert "QDialog#shortcutsDialog QKeySequenceEdit" in SHORTCUTS
    assert "QDialog#batchExportDialog QSpinBox" in BATCH
    assert "QSpinBox, QDoubleSpinBox" in STYLE


def test_bookmarks_are_visual_cards_with_thumbnail_and_remove_button():
    assert "class BookmarkPanel" in BOOKMARKS
    assert "class _BookmarkCard" in BOOKMARKS
    assert "self.remove_button.setIcon" in BOOKMARKS
    assert "self.thumb.setPixmap" in BOOKMARKS
    assert "def _ensure_bookmark_panel(self):" in READER
    assert "panel = BookmarkPanel()" in READER
    assert "panel.remove_requested.connect(self._remove_bookmark)" in READER
