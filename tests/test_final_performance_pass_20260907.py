from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def text(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_empty_startup_keeps_heavy_readers_and_panels_lazy():
    reader = text("app/reader_window.py")
    watcher = text("app/file_watcher_utils.py")
    assert "def _ensure_continuous_view(self):" in reader
    assert "def _ensure_double_view(self):" in reader
    assert "def _ensure_magnifier(self):" in reader
    assert "def _ensure_color_picker(self):" in reader
    assert "self._fs_watcher = None" in reader
    assert "def _ensure_file_watcher(self):" in reader
    assert "from app.archive" not in watcher
    assert "from app.compressed_collection" not in watcher
    assert "from app.format_defs" in watcher


def test_continuous_view_virtualization_is_conservative():
    views = text("app/views.py")
    assert "VIRTUAL_THRESHOLD = 240" in views
    assert "WINDOW_PAGES = 36" in views
    assert "WINDOW_SHIFT = 14" in views


def test_settings_avoid_redundant_registry_writes():
    settings = text("app/settings.py")
    assert "if cached is not self._MISSING and cached == value:" in settings
    assert "current = self.get_float(setting_key, DEFAULT_ADJUSTMENTS[key])" in settings
    assert "if current != value:" in settings
    assert "self._json_cache" in settings


def test_container_reopen_and_nested_zip_extraction_are_cached_streamed():
    archive = text("app/archive.py")
    collection = text("app/compressed_collection.py")
    assert "def copy_to(self, name, dest" in archive
    assert "shutil.copyfileobj(src, out" in archive
    assert 'load_index(self.path, "container_members")' in collection
    assert 'save_index_async(' in collection
    assert '"container_members"' in collection
    assert ".copy_to(raw, dest)" in collection


def test_modern_formats_use_header_dimension_fast_path():
    archive = text("app/archive.py")
    assert "def _modern_dimensions_path" in archive
    assert "_modern_dimensions_path(path)" in archive


def test_animation_history_has_memory_cap():
    provider = text("app/pixmap_provider.py")
    assert "self._history_limit_bytes = 48 * 1024 * 1024" in provider
    assert "self._history_bytes" in provider


def test_main_build_is_startup_optimized_onedir_without_upx():
    spec = text("Quaint.spec")
    build = text("build/build_exe.bat")
    portable = text("build/build_portable.bat")
    assert "COLLECT(" in spec
    assert "upx=False" in spec
    assert "--clean Quaint.spec" in build
    assert "QuaintPortable.spec" in portable

def test_index_cache_uses_one_coalescing_daemon_writer():
    cache = text("app/index_cache.py")
    assert "_WRITE_QUEUE = queue.Queue()" in cache
    assert "_PENDING_WRITES" in cache
    assert "def _writer_loop():" in cache
    assert 'name="QuaintIndexCache", daemon=True' in cache
    # regressão: não volte a criar uma nova threading.Thread dentro de cada save
    save_part = cache.split("def save_index_async", 1)[1].split("def _trim_cache", 1)[0]
    assert "threading.Thread(" not in save_part


def test_effect_hot_paths_use_pillow_luts_instead_of_full_float_arrays():
    effects = text("app/image_effects.py")
    assert "img.point(lut * len(img.getbands()))" in effects
    assert "ImageStat.Stat(sample)" in effects
    assert "gray.point([0 if i < cutoff else 255" in effects
    assert "Image.blend(img, Image.new" in effects


def test_vibrance_clarity_dehaze_hot_paths_avoid_numpy_full_res():
    import ast
    effects = text("app/image_effects.py")
    tree = ast.parse(effects)
    wanted = {"_apply_vibrance", "_apply_clarity", "_apply_dehaze"}
    segments = {}
    lines = effects.splitlines()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in wanted:
            segments[node.name] = "\n".join(lines[node.lineno - 1: node.end_lineno])
    assert set(segments) == wanted
    assert all("np." not in body for body in segments.values())
    assert "Image.composite(boosted, img, mask)" in segments["_apply_vibrance"]
    assert "ImageFilter.UnsharpMask" in segments["_apply_clarity"]
    assert "ImageEnhance.Contrast(img)" in segments["_apply_dehaze"]
