from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def _load_helper():
    path = ROOT / "app" / "windows_chrome.py"
    spec = importlib.util.spec_from_file_location("quaint_windows_chrome_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_colorref_matches_win32_bgr_layout():
    mod = _load_helper()
    assert mod.colorref(0x17, 0x17, 0x1C) == 0x001C1717
    assert mod.QUAINT_FRAME_COLOR == 0x001C1717


def test_edge_chrome_is_safe_noop_off_windows():
    mod = _load_helper()
    if mod.sys.platform != "win32":
        assert mod.apply_edge_chrome(123, True) is False
        assert mod.apply_edge_chrome(123, False) is False


def test_reader_syncs_native_edge_on_fullscreen_and_window_state():
    src = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
    assert "def _sync_native_window_edge" in src
    assert "self.isMaximized() or self.isFullScreen()" in src
    assert "apply_edge_chrome(int(self.winId()), enlarged)" in src
    assert "def changeEvent(self, event):" in src
    assert "self._queue_native_window_edge_sync()" in src
    # F11 remains native: no frameless flag is introduced in its function.
    fs = src[src.index("    def toggle_fullscreen"):src.index("    def _prepare_thumbnail_scope")]
    assert "self.showFullScreen()" in fs
    assert "setWindowFlag(Qt.FramelessWindowHint" not in fs
