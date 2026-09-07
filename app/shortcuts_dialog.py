from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QKeySequenceEdit, QPushButton, QHeaderView
from app.i18n import tr

class ShortcutsDialog(QDialog):
    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('shortcuts.title'))
        self.resize(680, 560)
        self.setObjectName("shortcutsDialog")
        self.setStyleSheet("""
            QDialog#shortcutsDialog {
                background-color: #1c1c24;
                color: #e6e6ec;
            }
            QDialog#shortcutsDialog QTableWidget {
                background-color: #14141a;
                alternate-background-color: #181820;
                color: #e6e6ec;
                gridline-color: #2f2f39;
                border: 1px solid #2f2f39;
                selection-background-color: #7c5cff;
                selection-color: #ffffff;
            }
            QDialog#shortcutsDialog QTableWidget::item {
                background-color: #14141a;
                color: #e6e6ec;
                padding: 7px 10px;
            }
            QDialog#shortcutsDialog QTableWidget::item:selected {
                background-color: #7c5cff;
                color: #ffffff;
            }
            QDialog#shortcutsDialog QHeaderView::section {
                background-color: #23232c;
                color: #f2f2f6;
                border: none;
                border-right: 1px solid #343440;
                border-bottom: 1px solid #343440;
                padding: 7px;
                font-weight: 600;
            }
            QDialog#shortcutsDialog QKeySequenceEdit,
            QDialog#shortcutsDialog QLineEdit {
                background-color: #101014;
                color: #f2f2f6;
                border: 1px solid #3a3a47;
                border-radius: 6px;
                padding: 7px 12px;
                selection-background-color: #7c5cff;
            }
            QDialog#shortcutsDialog QPushButton {
                background-color: #2b2b35;
                color: #f2f2f6;
                border: 1px solid #3a3a47;
                border-radius: 7px;
                padding: 7px 12px;
            }
            QDialog#shortcutsDialog QPushButton:hover {
                background-color: #383845;
            }
        """)
        self.entries = list(entries)
        lay = QVBoxLayout(self)
        self.table = QTableWidget(len(self.entries), 2)
        self.table.setHorizontalHeaderLabels([tr('shortcuts.action'), tr('shortcuts.shortcut')])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 180)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self._editors = {}
        for row, entry in enumerate(self.entries):
            key, label, shortcut = entry[:3]
            item = QTableWidgetItem(label)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, item)
            edit = QKeySequenceEdit(QKeySequence(shortcut or ''))
            edit.setMinimumHeight(32)
            edit.setMinimumWidth(150)
            self.table.setCellWidget(row, 1, edit)
            self._editors[key] = edit
        lay.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        reset = QPushButton(tr('shortcuts.reset'))
        ok = QPushButton(tr('common.ok'))
        cancel = QPushButton(tr('common.cancel'))
        reset.clicked.connect(self._reset)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(reset); buttons.addStretch(1); buttons.addWidget(cancel); buttons.addWidget(ok)
        lay.addLayout(buttons)

    def _reset(self):
        for entry in self.entries:
            key = entry[0]
            default = entry[3] if len(entry) > 3 else entry[2]
            self._editors[key].setKeySequence(QKeySequence(default or ''))

    def values(self):
        return {key: editor.keySequence().toString(QKeySequence.PortableText) for key, editor in self._editors.items()}
