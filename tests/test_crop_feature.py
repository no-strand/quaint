from pathlib import Path


def test_crop_tool_uses_persistent_qimage_selection_path():
    source = Path("app/reader_window.py").read_text(encoding="utf-8")
    assert "def open_crop_tool" in source
    assert "as_qimage=True" in source

    crop = Path("app/crop_dialog.py").read_text(encoding="utf-8")
    assert "self.source_image" in crop
    assert "self._dragging = False" in crop
    assert "def paintEvent" in crop
    assert "def clear_selection" in crop
    assert "selectionChanged = Signal(QRect)" in crop
    assert "self.copy_button.setEnabled(False)" in crop
    assert "self.save_button.setEnabled(False)" in crop
    assert "QApplication.clipboard().setImage(image)" in crop
    assert "def copy_crop" in crop
    assert "def save_crop" in crop
    # A seleção não depende mais de QRubberBand, que podia desaparecer/perder
    # estado ao soltar o mouse ou mudar o foco.
    assert "from PySide6.QtWidgets import QRubberBand" not in crop
    assert "self.rubber" not in crop
    # Only the small display image becomes a QPixmap; source stays QImage.
    assert "QPixmap.fromImage(self.display_image)" in crop
