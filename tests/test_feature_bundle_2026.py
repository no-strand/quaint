from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
SETTINGS = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
PROVIDER = (ROOT / "app" / "pixmap_provider.py").read_text(encoding="utf-8")
VIEWS = (ROOT / "app" / "views.py").read_text(encoding="utf-8")
ADJUST = (ROOT / "app" / "adjustments_dialog.py").read_text(encoding="utf-8")
ARCHIVE = (ROOT / "app" / "archive.py").read_text(encoding="utf-8")


def test_save_original_and_save_changes_are_separate_commands():
    assert 'action.save_page' in READER
    assert 'action.save_changes' in READER
    assert 'def save_current_page_with_changes' in READER
    assert 'apply_image_effects(img, self.settings.get_adjustments(), self.settings.image_filter())' in READER


def test_bookmarks_panel_and_persistence_exist():
    assert 'bookmark_dock' in READER
    assert 'def toggle_current_bookmark' in READER
    assert 'def bookmarks(self, path)' in SETTINGS
    assert 'bookmarks_map' in SETTINGS


def test_shortcuts_sort_session_and_batch_export_exist():
    assert 'ShortcutsDialog' in READER and 'def open_shortcuts_dialog' in READER
    assert 'folder_sort_mode' in SETTINGS and 'def set_folder_sort' in READER
    assert 'restore_session_enabled' in SETTINGS and 'def _maybe_restore_session' in READER
    assert 'BatchExportDialog' in READER and 'def open_batch_export' in READER


def test_touch_and_touchpad_gestures_are_enabled():
    assert 'Qt.PinchGesture' in READER
    assert 'QEvent.NativeGesture' in READER
    assert 'QScroller.TouchGesture' in VIEWS


def test_animation_speed_frame_step_and_frame_save_exist():
    assert 'set_animation_speed' in PROVIDER
    assert 'step_animation' in PROVIDER
    assert '_frame_history' in PROVIDER
    assert 'def save_current_animation_frame' in READER


def test_filters_and_color_management_are_wired_to_display():
    assert 'filter_combo' in ADJUST
    assert 'set_filter(self.settings.image_filter())' in READER
    assert 'convert_embedded_profile_to_srgb' in ARCHIVE


def test_image_only_mode_handles_cursor_and_bookmarks():
    assert '_cursor_hide_timer' in READER
    assert 'Qt.BlankCursor' in READER
    assert 'self.bookmark_dock.hide()' in READER
