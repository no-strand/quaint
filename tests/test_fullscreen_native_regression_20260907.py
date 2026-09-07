from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")


def _method_block(name: str, next_name: str) -> str:
    start = READER.index(f"    def {name}(")
    end = READER.index(f"    def {next_name}(", start)
    return READER[start:end]


def test_fullscreen_uses_native_qt_fullscreen_without_frameless_flag():
    block = _method_block("toggle_fullscreen", "_prepare_thumbnail_scope")
    assert "self.showFullScreen()" in block
    assert "entering = not self.isFullScreen()" in block
    assert "self.setWindowFlag(Qt.FramelessWindowHint, True)" not in block
    assert "self.setWindowFlags(" not in block


def test_fullscreen_exit_is_based_on_real_window_state_not_action_checked_state():
    block = _method_block("toggle_fullscreen", "_prepare_thumbnail_scope")
    assert "checked = bool(checked)" not in block
    assert "entering = not self.isFullScreen()" in block
    assert "self._restore_window_after_fullscreen()" in block


def test_image_only_remains_the_only_mode_that_directly_sets_frameless():
    image_only = _method_block("_enter_image_only_frame", "_leave_image_only_frame")
    assert "self.setWindowFlag(Qt.FramelessWindowHint, True)" in image_only
    assert "if self._interface_entered_during_fullscreen:" in image_only
    assert "return" in image_only


def test_image_only_started_during_fullscreen_restores_pre_fullscreen_geometry():
    restore = _method_block("_restore_window_after_fullscreen", "toggle_fullscreen")
    assert "self._interface_entered_during_fullscreen" in restore
    assert "self._interface_prev_window_state = state" in restore
    assert "self._interface_prev_window_geometry = geometry" in restore
