from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_space_is_contextual_and_not_a_next_page_shortcut():
    source = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
    next_block = source.split('self.act_next = QAction(tr("action.next_page"), self)', 1)[1].split(
        'self.act_first = QAction(tr("action.first_page"), self)', 1
    )[0]
    assert "Qt.Key_Space" in next_block  # aparece no QAction contextual
    # O espaço deve estar depois da conexão do next, no bloco act_space.
    before_space_action = next_block.split("self.act_space = QAction", 1)[0]
    assert "Qt.Key_Space" not in before_space_action
    assert "toggle_animation_pause" in source
    assert "_schedule_animation_sync" in source


def test_thumbnails_never_start_animation_loops():
    source = (ROOT / "app" / "thumbnail_panel.py").read_text(encoding="utf-8")
    assert "set_animation_indices" not in source
    assert "animation_frame_ready" not in source


def test_provider_uses_dedicated_daemon_threads_for_visible_animations():
    source = (ROOT / "app" / "pixmap_provider.py").read_text(encoding="utf-8")
    assert "daemon=True" in source
    assert "self._animation_workers" in source
    assert "stop_animations(wait=True)" in source
    assert "iter_webm_frames" in source
