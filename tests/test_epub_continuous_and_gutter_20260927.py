from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EPUB = (ROOT / "app" / "epub_view.py").read_text(encoding="utf-8")
VIEWS = (ROOT / "app" / "views.py").read_text(encoding="utf-8")
READER = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")


def test_epub_text_reuses_exact_cbz_pdf_book_gutter():
    assert "class _BookGutter(QWidget):" in VIEWS
    assert "WIDTH = 1" in VIEWS
    assert "from app.views import _BookGutter" in EPUB
    assert "self.gutter = _BookGutter(self._container)" in EPUB
    assert 'self._layout.setSpacing(0 if self._mode == "double" else 18)' in EPUB
    assert 'gap = self.gutter.width() if self._mode == "double" else 0' in EPUB
    assert "a borda interna não é desenhada pela folha" in EPUB


def test_pdf_cbz_and_image_epub_share_double_page_view_path():
    assert "view = DoublePageView(self.provider)" in READER
    assert "self.stack.setCurrentWidget(self._ensure_double_view())" in READER
    assert "if self.archive.has_text_pages():" in READER


def test_text_epub_continuous_mode_stays_visible_but_is_disabled_and_blocked():
    assert "text_epub = self.archive.has_text_pages()" in READER
    assert "continuous_allowed = not single_image and not text_epub" in READER
    assert "self.act_mode_continuous.setEnabled(continuous_allowed)" in READER
    assert "self.act_mode_continuous.setVisible(True)" in READER
    assert 'tr("tooltip.epub_text_no_continuous") if text_epub' in READER
    assert "self.archive.has_text_pages() and mode == MODE_CONTINUOUS" in READER
    assert 'self.statusBar().showMessage(tr("status.epub_fixed_pages_only"), 3500)' in READER


def test_text_epub_loaded_from_continuous_session_falls_back_before_views_build():
    marker = "if self.archive.has_text_pages() and self.mode == MODE_CONTINUOUS:"
    assert marker in READER
    marker_pos = READER.index(marker)
    rebuild_pos = READER.index("self._rebuild_views()", marker_pos)
    fallback_pos = READER.index("self.mode = MODE_SINGLE", marker_pos)
    assert fallback_pos < rebuild_pos


def test_image_only_epub_keeps_standard_continuous_mode_enabled():
    # EPUB somente de imagens tem has_text_pages() == False e não é imagem
    # avulsa; por isso continua no ContinuousView padrão de PDF/CBZ.
    assert 'single_image = self.archive.kind in ("image", "video")' in READER
    assert "continuous_allowed = not single_image and not text_epub" in READER
    assert "elif self.mode == MODE_CONTINUOUS:" in READER
    assert "view = self._ensure_continuous_view()" in READER
