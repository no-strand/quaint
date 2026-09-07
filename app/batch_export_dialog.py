from pathlib import Path
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox, QSpinBox, QCheckBox, QFileDialog
from app.i18n import tr

class BatchExportDialog(QDialog):
    def __init__(self, initial_dir, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('batch.title'))
        self.resize(520, 260)
        self.setObjectName('batchExportDialog')
        self.setStyleSheet("""
            QDialog#batchExportDialog { background-color: #1c1c24; color: #e6e6ec; }
            QDialog#batchExportDialog QLabel, QDialog#batchExportDialog QCheckBox {
                color: #e6e6ec; background: transparent;
            }
            QDialog#batchExportDialog QComboBox,
            QDialog#batchExportDialog QSpinBox,
            QDialog#batchExportDialog QLineEdit {
                background-color: #26262e;
                color: #f2f2f6;
                border: 1px solid #343440;
                border-radius: 7px;
                padding: 6px 9px;
                min-height: 22px;
            }
            QDialog#batchExportDialog QSpinBox::up-button,
            QDialog#batchExportDialog QSpinBox::down-button {
                background-color: #33333e;
                border: none;
                width: 18px;
            }
            QDialog#batchExportDialog QComboBox QAbstractItemView {
                background-color: #1c1c24; color: #e6e6ec;
                selection-background-color: #7c5cff;
            }
        """)
        root = QVBoxLayout(self); form = QFormLayout()
        row = QHBoxLayout(); self.dir_edit = QLineEdit(str(initial_dir)); browse=QPushButton(tr('common.browse'))
        browse.clicked.connect(self._browse); row.addWidget(self.dir_edit,1); row.addWidget(browse)
        form.addRow(tr('batch.output'), row)
        self.format_combo=QComboBox();
        for label,ext in [('PNG','.png'),('JPEG','.jpg'),('WebP','.webp')]: self.format_combo.addItem(label,ext)
        form.addRow(tr('batch.format'), self.format_combo)
        self.max_dim=QSpinBox(); self.max_dim.setRange(0,20000); self.max_dim.setSpecialValueText(tr('batch.original_size')); self.max_dim.setValue(0)
        form.addRow(tr('batch.max_dimension'), self.max_dim)
        self.quality=QSpinBox(); self.quality.setRange(30,100); self.quality.setValue(92)
        form.addRow(tr('batch.quality'), self.quality)
        self.apply_changes=QCheckBox(tr('batch.apply_changes')); self.apply_changes.setChecked(True); form.addRow('', self.apply_changes)
        root.addLayout(form)
        buttons=QHBoxLayout(); ok=QPushButton(tr('batch.export')); cancel=QPushButton(tr('common.cancel')); ok.clicked.connect(self.accept); cancel.clicked.connect(self.reject)
        buttons.addStretch(1); buttons.addWidget(cancel); buttons.addWidget(ok); root.addLayout(buttons)
    def _browse(self):
        path=QFileDialog.getExistingDirectory(self,tr('batch.output'),self.dir_edit.text())
        if path:self.dir_edit.setText(path)
    def options(self):
        return {'directory':Path(self.dir_edit.text()).expanduser(),'extension':self.format_combo.currentData(),'max_dim':self.max_dim.value() or None,'quality':self.quality.value(),'apply_changes':self.apply_changes.isChecked()}
