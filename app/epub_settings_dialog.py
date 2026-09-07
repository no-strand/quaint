"""EPUB text reading settings."""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel,
    QFontComboBox, QSpinBox, QPushButton, QDialogButtonBox,
)

from app.i18n import tr


class EpubSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setModal(True)
        self.setMinimumWidth(430)

        outer = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        outer.addWidget(self.info)

        self.form = QFormLayout()
        self.font_label = QLabel()
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(self.settings.epub_font())
        self.form.addRow(self.font_label, self.font_combo)

        self.font_size_label = QLabel()
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 36)
        self.font_size.setValue(self.settings.epub_font_size())
        self.font_size.setStyleSheet("QSpinBox { color: #000000; } QSpinBox QLineEdit { color: #000000; }")
        self.form.addRow(self.font_size_label, self.font_size)

        self.text_width_label = QLabel()
        self.text_width = QSpinBox()
        self.text_width.setRange(520, 1400)
        self.text_width.setSingleStep(20)
        self.text_width.setValue(self.settings.epub_text_width())
        self.text_width.setStyleSheet("QSpinBox { color: #000000; } QSpinBox QLineEdit { color: #000000; }")
        self.form.addRow(self.text_width_label, self.text_width)
        outer.addLayout(self.form)

        self.defaults = QLabel()
        self.defaults.setWordWrap(True)
        self.defaults.setStyleSheet("color: #8f8f9c;")
        outer.addWidget(self.defaults)

        row = QHBoxLayout()
        self.reset_button = QPushButton()
        self.reset_button.clicked.connect(self._reset)
        row.addWidget(self.reset_button)
        row.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        row.addWidget(self.buttons)
        outer.addLayout(row)
        self.retranslate_ui()

    def retranslate_ui(self):
        self.setWindowTitle(tr("action.epub"))
        self.info.setText(tr("epub.info"))
        self.font_label.setText(tr("epub.font"))
        self.font_size_label.setText(tr("epub.font_size"))
        self.text_width_label.setText(tr("epub.text_width"))
        self.font_size.setSuffix(tr("unit.points_suffix"))
        self.text_width.setSuffix(tr("unit.pixels_suffix"))
        self.defaults.setText(tr("epub.defaults"))
        self.reset_button.setText(tr("epub.reset"))
        ok = self.buttons.button(QDialogButtonBox.Ok)
        cancel = self.buttons.button(QDialogButtonBox.Cancel)
        if ok is not None:
            ok.setText(tr("common.ok"))
        if cancel is not None:
            cancel.setText(tr("common.cancel"))

    def _reset(self):
        self.font_combo.setCurrentFont(self.settings.default_epub_font())
        self.font_size.setValue(self.settings.DEFAULT_EPUB_FONT_SIZE)
        self.text_width.setValue(self.settings.DEFAULT_EPUB_TEXT_WIDTH)

    def _accept(self):
        self.settings.set_epub_settings(
            self.font_combo.currentFont().family(),
            self.font_size.value(),
            self.text_width.value(),
        )
        self.accept()
