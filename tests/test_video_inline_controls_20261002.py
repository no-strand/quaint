from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_video_player_packages_inline_repeat_and_speed_icons():
    view = text("app/video_view.py")
    expected = (
        "video_repeat.png",
        "video_repeat_on.png",
        "video_speed_down.png",
        "video_speed_up.png",
    )
    for name in expected:
        assert (ROOT / "resources" / "icons" / name).is_file()
        assert name in view


def test_video_player_has_repeat_toggle_and_speed_step_buttons():
    view = text("app/video_view.py")
    assert 'setObjectName("videoRepeatButton")' in view
    assert 'setObjectName("videoSpeedDownButton")' in view
    assert 'setObjectName("videoSpeedUpButton")' in view
    assert 'setObjectName("videoSpeedLabel")' in view
    assert "repeat_changed = Signal(bool)" in view
    assert "playback_rate_changed = Signal(float)" in view
    assert "PLAYBACK_RATES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)" in view
    assert "def step_playback_rate" in view


def test_inline_controls_stay_synchronized_with_tools_video_actions():
    window = text("app/reader_window.py")
    assert "repeat_enabled=self._video_repeat" in window
    assert "self.video_view.repeat_changed.connect(self.set_video_repeat)" in window
    assert "self.video_view.playback_rate_changed.connect(self.set_video_speed)" in window
    assert "view.set_repeat_enabled(enabled, emit=False)" in window
    assert "view.set_repeat_enabled(False, emit=False)" in window
    assert "view.set_playback_rate(value)" in window


def test_inline_video_controls_are_styled_and_localized():
    style = text("resources/style.qss")
    for object_name in (
        "videoRepeatButton",
        "videoSpeedDownButton",
        "videoSpeedUpButton",
        "videoSpeedLabel",
    ):
        assert object_name in style
    for locale in ("pt_BR.json", "en_US.json", "es_ES.json"):
        source = text(f"locales/{locale}")
        assert '"video.repeat_enable"' in source
        assert '"video.repeat_disable"' in source
        assert '"video.speed_slower"' in source
        assert '"video.speed_faster"' in source
