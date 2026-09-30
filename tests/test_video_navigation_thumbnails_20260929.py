from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_single_video_navigation_never_assumes_double_view_exists():
    source = text("app/reader_window.py")
    assert "def _double_page_navigation_active" in source
    assert 'self.archive.kind in ("image", "video")' in source
    assert "view = getattr(self, \"double_view\", None)" in source
    assert "elif self._double_page_navigation_active():" in source


def test_sibling_navigation_includes_mixed_supported_file_types():
    source = text("app/reader_window.py")
    assert "os.path.splitext(entry.name)[1].lower() in SUPPORTED_FILE_EXTS" in source
    assert "or is_container_path(entry.name)" in source
    assert "self._load_sibling_archive(+1)" in source
    assert "self._load_sibling_archive(-1)" in source


def test_video_thumbnails_use_windows_shell_provider_lazily():
    thumbs = text("app/thumbnail_panel.py")
    shell = text("app/windows_thumbnail.py")
    assert "from app.windows_thumbnail import explorer_thumbnail" in thumbs
    assert "SHCreateItemFromParsingName" in shell
    assert "IShellItemImageFactory" in shell
    assert "SIIGBF_THUMBNAILONLY" in shell
    assert "self._video_pool.setMaxThreadCount(2)" in thumbs
    assert "self._cancel_stale_video_tasks(video_rows)" in thumbs


def test_uploaded_play_pause_icons_are_used_by_video_player():
    source = text("app/video_view.py")
    assert 'self._icons_dir / "video_play.png"' in source
    assert 'self._icons_dir / "video_pause.png"' in source
    assert (ROOT / "resources/icons/video_play.png").is_file()
    assert (ROOT / "resources/icons/video_pause.png").is_file()
