from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPUB = (ROOT / "app" / "epub_view.py").read_text(encoding="utf-8")
PROVIDER = (ROOT / "app" / "pixmap_provider.py").read_text(encoding="utf-8")
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")


def test_epub_text_pages_keep_gui_thread_rendering_for_thumbnails():
    # Miniaturas de páginas textuais ainda precisam rasterizar QTextDocument
    # na GUI thread, mesmo sem existir modo contínuo de leitura para texto.
    assert "def requires_gui_thread_for(self, row):" in EPUB
    assert "return bool(self.position(row)[2])" in EPUB
    assert "self._document_cache = OrderedDict()" in EPUB
    assert "self._thread_local" not in EPUB


def test_pixmap_provider_serializes_gui_only_pages():
    assert "def _queue_gui_request(self, index, priority=100):" in PROVIDER
    assert "def _process_gui_request(self):" in PROVIDER
    assert "QTimer.singleShot(0, self._process_gui_request)" in PROVIDER
    assert 'self._pending[token] = ("gui", None)' in PROVIDER
    assert "if self._requires_gui_thread(index):" in PROVIDER
    assert "if foreground:" in PROVIDER
    assert "self._queue_gui_request(index, priority=priority)" in PROVIDER


def test_text_epub_continuous_cannot_be_entered_through_action_or_set_mode():
    assert "self.act_mode_continuous.setVisible(True)" in READER
    assert "self.act_mode_continuous.setEnabled(continuous_allowed)" in READER
    guard = "if self.archive and self.archive.has_text_pages() and mode == MODE_CONTINUOUS:"
    assert guard in READER
    guard_pos = READER.index(guard)
    return_pos = READER.index("return", guard_pos)
    settings_pos = READER.index('self.settings.set("mode", mode)', guard_pos)
    assert return_pos < settings_pos


def test_shutdown_clears_gui_render_queue():
    assert "self._gui_requests.clear()" in PROVIDER
    assert "self._gui_pump_scheduled = False" in PROVIDER


def test_dead_text_continuous_reader_pipeline_was_removed():
    assert "def _ensure_epub_continuous_view" not in READER
    assert "_epub_continuous_provider" not in READER
