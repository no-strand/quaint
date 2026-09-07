from pathlib import Path

from app.archive import SUPPORTED_FILE_EXTS
from app.compressed_collection import is_container_path


def test_drop_validation_uses_every_supported_format():
    # O drop reutiliza exatamente as mesmas fontes de verdade do fluxo Abrir.
    expected_regular = {
        ".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".tif", ".tiff",
        ".bmp", ".ico", ".webm", ".cbz", ".cbr", ".pdf", ".epub",
        ".zip", ".rar", ".7z", ".tar", ".tgz", ".tbz2", ".txz",
    }
    assert expected_regular <= SUPPORTED_FILE_EXTS
    for name in (
        "collection.zip", "collection.rar", "collection.7z", "collection.tar",
        "collection.tgz", "collection.tar.gz", "collection.tar.bz2",
        "collection.tbz2", "collection.tar.xz", "collection.txz",
    ):
        assert is_container_path(name), name


def test_drag_drop_is_wired_to_the_normal_open_flow():
    root = Path(__file__).resolve().parents[1]
    reader = (root / "app" / "reader_window.py").read_text(encoding="utf-8")
    epub = (root / "app" / "epub_view.py").read_text(encoding="utf-8")

    assert "self.setAcceptDrops(True)" in reader
    assert "def _is_supported_drop_path(path):" in reader
    assert "path_obj.is_dir()" in reader
    assert "is_container_path(path_obj)" in reader
    assert "path_obj.suffix.lower() in SUPPORTED_FILE_EXTS" in reader
    assert "def dragEnterEvent(self, event):" in reader
    assert "def dragMoveEvent(self, event):" in reader
    assert "def dropEvent(self, event):" in reader
    assert "url.isLocalFile()" in reader
    assert "self.settings.set_last_open_directory(path)" in reader
    assert "self.open_path(path)" in reader
    # QTextBrowser não deve capturar o arquivo quando um EPUB textual estiver aberto.
    assert "self.setAcceptDrops(False)" in epub
