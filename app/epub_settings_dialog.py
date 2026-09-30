"""Configurações da leitura de EPUB textual paginado."""
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFontComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from app.i18n import tr


class EpubSettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setModal(True)
        self.setMinimumWidth(450)

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
        self.font_size_label.setStyleSheet("color: #ffffff;")
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 36)
        self.font_size.setValue(self.settings.epub_font_size())
        self.font_size.setStyleSheet(
            "QSpinBox { color: #ffffff; } "
            "QSpinBox QLineEdit { color: #ffffff; }"
        )
        self.form.addRow(self.font_size_label, self.font_size)

        self.theme_label = QLabel()
        self.theme_label.setStyleSheet("color: #ffffff;")
        self.theme_combo = QComboBox()
        self.theme_combo.setStyleSheet(
            "QComboBox { color: #ffffff; } "
            "QComboBox QAbstractItemView { color: #ffffff; }"
        )
        self.form.addRow(self.theme_label, self.theme_combo)
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
        self._select_theme(self.settings.epub_theme())

    def _select_theme(self, theme):
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == theme:
                self.theme_combo.setCurrentIndex(i)
                return
        self.theme_combo.setCurrentIndex(0)

    def retranslate_ui(self):
        current_theme = self.theme_combo.currentData() if self.theme_combo.count() else self.settings.epub_theme()
        self.setWindowTitle(tr("action.epub"))
        self.info.setText(tr("epub.info"))
        self.font_label.setText(tr("epub.font"))
        self.font_size_label.setText(tr("epub.font_size"))
        self.theme_label.setText(tr("epub.theme"))
        self.font_size.setSuffix(tr("unit.points_suffix"))
        self.theme_combo.clear()
        self.theme_combo.addItem(tr("epub.theme_light"), "light")
        self.theme_combo.addItem(tr("epub.theme_dark"), "dark")
        self.theme_combo.addItem(tr("epub.theme_sepia"), "sepia")
        self._select_theme(current_theme)
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
        self._select_theme("light")

    def _accept(self):
        self.settings.set_epub_settings(
            self.font_combo.currentFont().family(),
            self.font_size.value(),
            self.theme_combo.currentData() or "light",
        )
        self.accept()
