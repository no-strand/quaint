from pathlib import Path


def test_compare_tool_has_synchronized_zoom_and_scroll():
    dialog = Path("app/compare_dialog.py").read_text(encoding="utf-8")
    assert "zoom_requested" in dialog
    assert "sync_horizontal" in dialog
    assert "sync_vertical" in dialog
    # Zoom must not allocate a freshly scaled QPixmap on every slider tick.
    assert "self.label.setScaledContents(True)" in dialog
    set_zoom = dialog.split("def set_zoom", 1)[1].split("def wheelEvent", 1)[0]
    assert "self._source.scaled(" not in set_zoom

    main = Path("app/reader_window.py").read_text(encoding="utf-8")
    assert "show_compare, max_dim=4096" in main


def test_full_res_callback_is_bridged_to_gui_thread():
    provider = Path("app/pixmap_provider.py").read_text(encoding="utf-8")
    assert "class _FullResBridge(QObject)" in provider
    assert "@Slot(int, QImage, int)" in provider
    assert "QPixmap.fromImage(qimg)" in provider
    full_res = provider.split("def full_res", 1)[1].split("# ---------------------------------------------------------- animation", 1)[0]
    assert "_FullResBridge(" in full_res
    assert "task.signals.done.connect(done)" not in full_res
