from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_version_is_101_everywhere():
    assert 'APP_VERSION = "1.0.1"' in text("app/version.py")
    assert '#define MyAppVersion "1.0.1"' in text("build/installer.iss")
    version = text("version.txt")
    assert "filevers=(1, 0, 1, 0)" in version
    assert "prodvers=(1, 0, 1, 0)" in version


def test_uploaded_volume_icons_are_packaged_and_used():
    source = text("app/video_view.py")
    assert 'self._icons_dir / "video_volume.png"' in source
    assert 'self._icons_dir / "video_muted.png"' in source
    assert (ROOT / "resources/icons/video_volume.png").is_file()
    assert (ROOT / "resources/icons/video_muted.png").is_file()


def test_video_frame_capture_uses_current_qvideo_frame_without_second_decoder():
    view = text("app/video_view.py")
    window = text("app/reader_window.py")
    assert "def current_frame_image" in view
    assert "sink.videoFrame()" in view
    assert "frame.toImage()" in view
    assert "def save_current_video_frame" in window
    assert "current_frame_image()" in window
    assert "still_image_save_filter()" in window


def test_video_menu_has_repeat_auto_advance_and_speed():
    source = text("app/reader_window.py")
    assert "self.act_video_repeat" in source
    assert "self.act_video_auto_advance" in source
    assert "self.video_speed_actions" in source
    assert "def _on_video_ended" in source
    assert "QTimer.singleShot(0, self.next_page)" in source
    assert "view.restart()" in source


def test_thumbnail_and_contents_cards_use_painted_format_badges():
    delegate = text("app/format_badge_delegate.py")
    thumbs = text("app/thumbnail_panel.py")
    summary = text("app/summary_dialog.py")
    assert "class FormatBadgeDelegate" in delegate
    assert "painter.drawRoundedRect" in delegate
    assert "setItemDelegate(FormatBadgeDelegate" in thumbs
    assert "role == FORMAT_ROLE" in thumbs
    assert "setItemDelegate(FormatBadgeDelegate" in summary
    assert "setData(FORMAT_ROLE" in summary
