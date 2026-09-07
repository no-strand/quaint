from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
VIEWS = (ROOT / "app" / "views.py").read_text(encoding="utf-8")


def test_image_only_keeps_shortcuts_bound_to_main_window():
    assert "def _ensure_window_shortcut_actions" in READER
    assert "action.setShortcutContext(Qt.WindowShortcut)" in READER
    assert "self.addAction(action)" in READER
    assert "self._ensure_window_shortcut_actions()" in READER


def test_image_only_removes_and_restores_native_title_bar():
    assert "self.setWindowFlag(Qt.FramelessWindowHint, True)" in READER
    assert "def _enter_image_only_frame" in READER
    assert "def _leave_image_only_frame" in READER
    assert "self.setWindowFlags(flags)" in READER
    assert "self._interface_prev_window_geometry" in READER


def test_fullscreen_and_image_only_use_edge_to_edge_view_layout():
    assert "def _refresh_edge_to_edge_mode" in READER
    assert "self._interface_hidden or self.isFullScreen()" in READER
    assert "QTimer.singleShot(0, self._refresh_edge_to_edge_mode)" in READER
    assert VIEWS.count("def set_edge_to_edge(self, enabled):") >= 3
    assert "inset = 0 if self._edge_to_edge else 4" in VIEWS
    assert "margin = 0 if (self._edge_to_edge or self.layout_mode == LAYOUT_FULL_WIDTH) else 20" in VIEWS
    assert "inset = 0 if self._edge_to_edge else 24" in VIEWS
