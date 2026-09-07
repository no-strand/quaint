"""Current file information and tag editing dialog."""
import math
import subprocess
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout, QLabel,
    QPushButton, QGroupBox, QInputDialog, QWidget
)

from app.archive import format_size, ArchiveError
from app.i18n import tr
from app.image_metadata import extract_image_metadata
from app.message_boxes import information as show_information, warning as show_warning, critical as show_critical


class FileInfoDialog(QDialog):
    """Shows all file/image metadata without nested scrolling areas.

    Image details and metadata files are laid out top-to-bottom and then into
    additional columns. This keeps every item visible while avoiding the small
    scrolling list boxes that were previously used here.
    """

    DETAILS_ROWS_PER_COLUMN = 7
    METADATA_ROWS_PER_COLUMN = 7

    def __init__(self, archive, tags, metadata_files, parent=None, current_index=0):
        super().__init__(parent)
        self.archive = archive
        self.current_index = int(current_index)
        self.setModal(True)
        self.setMinimumWidth(760)
        self.resize(820, 500)

        outer = QVBoxLayout(self)

        self.box_info = QGroupBox()
        self.form = QFormLayout(self.box_info)
        self.form.setLabelAlignment(Qt.AlignRight)

        self.name_caption = QLabel()
        self.name_label = QLabel(); self.name_label.setWordWrap(True)
        self.form.addRow(self.name_caption, self.name_label)
        self.path_caption = QLabel()
        self.path_label = QLabel(); self.path_label.setWordWrap(True); self.path_label.setStyleSheet("color: #9d9dae;")
        self.form.addRow(self.path_caption, self.path_label)
        self.folder_caption = QLabel()
        self.folder_label = QLabel(); self.folder_label.setWordWrap(True)
        self.folder_label.setTextFormat(Qt.RichText)
        self.folder_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.folder_label.setOpenExternalLinks(False)
        self.folder_label.linkActivated.connect(self.open_folder)
        self.form.addRow(self.folder_caption, self.folder_label)
        self.type_caption = QLabel(); self.type_label = QLabel(); self.form.addRow(self.type_caption, self.type_label)
        self.size_caption = QLabel(); self.size_label = QLabel(); self.form.addRow(self.size_caption, self.size_label)
        self.pages_caption = QLabel(); self.pages_label = QLabel(); self.form.addRow(self.pages_caption, self.pages_label)
        outer.addWidget(self.box_info)

        # Image details and metadata files share the horizontal space. Each
        # group fills rows first and then continues in a new column, so all
        # values stay visible without an inner scrollbar.
        details_row = QHBoxLayout()
        details_row.setSpacing(10)

        self.box_image = QGroupBox()
        image_layout = QVBoxLayout(self.box_image)
        self.image_meta_widget = QWidget()
        self.image_meta_grid = QGridLayout(self.image_meta_widget)
        self.image_meta_grid.setContentsMargins(0, 0, 0, 0)
        self.image_meta_grid.setHorizontalSpacing(18)
        self.image_meta_grid.setVerticalSpacing(5)
        image_layout.addWidget(self.image_meta_widget)
        details_row.addWidget(self.box_image, 3)

        self.box_meta = QGroupBox()
        meta_layout = QVBoxLayout(self.box_meta)
        self.meta_widget = QWidget()
        self.meta_grid = QGridLayout(self.meta_widget)
        self.meta_grid.setContentsMargins(0, 0, 0, 0)
        self.meta_grid.setHorizontalSpacing(18)
        self.meta_grid.setVerticalSpacing(5)
        meta_layout.addWidget(self.meta_widget)
        details_row.addWidget(self.box_meta, 2)

        outer.addLayout(details_row)

        self.box_tags = QGroupBox()
        tags_layout = QVBoxLayout(self.box_tags)
        self.tags_label = QLabel(); self.tags_label.setWordWrap(True)
        tags_layout.addWidget(self.tags_label)
        tags_btn_row = QHBoxLayout(); tags_btn_row.addStretch(1)
        self.btn_add_tags = QPushButton(); self.btn_add_tags.clicked.connect(self.add_tags_dialog)
        tags_btn_row.addWidget(self.btn_add_tags); tags_layout.addLayout(tags_btn_row)
        outer.addWidget(self.box_tags)

        outer.addStretch(1)
        btn_row = QHBoxLayout(); btn_row.addStretch(1)
        self.btn_close = QPushButton(); self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close); outer.addLayout(btn_row)

        self.retranslate_ui()
        self._populate(tags, metadata_files)

    def retranslate_ui(self):
        self.setWindowTitle(tr("file_info.title"))
        self.box_info.setTitle(tr("file_info.info_group"))
        captions = [
            (self.name_caption, "file_info.name"), (self.path_caption, "file_info.path"),
            (self.folder_caption, "file_info.folder"), (self.type_caption, "file_info.type"),
            (self.size_caption, "file_info.size"), (self.pages_caption, "file_info.pages"),
        ]
        for label, key in captions:
            label.setText(tr(key))
        self.box_image.setTitle(tr("file_info.image_details_group"))
        self.box_meta.setTitle(tr("file_info.metadata_group"))
        self.box_tags.setTitle(tr("file_info.tags_group"))
        self.btn_add_tags.setText(tr("common.add"))
        self.btn_close.setText(tr("common.close"))

    @staticmethod
    def _clear_grid(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    @staticmethod
    def _fill_grid(layout, lines, rows_per_column=7, muted=False):
        """Fill down first, then continue in columns; never creates scrolling."""
        FileInfoDialog._clear_grid(layout)
        lines = list(lines)
        if not lines:
            return
        rows = max(1, min(int(rows_per_column), len(lines)))
        column_count = int(math.ceil(len(lines) / rows))
        for idx, text in enumerate(lines):
            column = idx // rows
            row = idx % rows
            label = QLabel(str(text))
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setWordWrap(False)
            if muted:
                label.setStyleSheet("color: #9d9dae;")
            layout.addWidget(label, row, column)
        for column in range(column_count):
            layout.setColumnStretch(column, 1)

    def _populate(self, tags, metadata_files):
        archive = self.archive
        self.name_label.setText(archive.display_name())
        source_container = getattr(archive, "source_container_path", None)
        source_member = getattr(archive, "source_member_name", None)
        if source_container is not None and source_member:
            self.path_label.setText(f"{source_container} :: {source_member}")
            folder = source_container.resolve().parent
        else:
            self.path_label.setText(str(archive.path))
            folder = archive.path.resolve().parent
        folder_url = QUrl.fromLocalFile(str(folder)).toString()
        self.folder_label.setText(f'<a href="{folder_url}" style="color: #7aa2f7;">{folder}</a>')

        self.type_label.setText(archive.kind_label())
        self.size_label.setText(format_size(archive.file_size_bytes()))
        self.pages_label.setText(str(archive.count()))

        image_details = extract_image_metadata(archive, self.current_index)
        self.box_image.setVisible(bool(image_details))
        image_lines = [f"{tr(key)}: {value}" for key, value in image_details]
        self._fill_grid(self.image_meta_grid, image_lines, self.DETAILS_ROWS_PER_COLUMN)

        if metadata_files:
            metadata_lines = [str(name) for name in metadata_files]
            self._fill_grid(self.meta_grid, metadata_lines, self.METADATA_ROWS_PER_COLUMN)
        else:
            self._fill_grid(
                self.meta_grid,
                [tr("file_info.no_metadata")],
                self.METADATA_ROWS_PER_COLUMN,
                muted=True,
            )

        if tags:
            self.tags_label.setText(", ".join(tags)); self.tags_label.setStyleSheet("")
        else:
            self.tags_label.setText(tr("file_info.no_tags")); self.tags_label.setStyleSheet("color: #9d9dae;")

        writable = archive.can_write_tags()
        self.btn_add_tags.setEnabled(writable)
        self.btn_add_tags.setToolTip("" if writable else tr("file_info.read_only_tags"))

        # Recalculate once Qt has processed the newly created labels. This makes
        # the window grow to fit all columns rather than exposing scrollbars.
        QTimer.singleShot(0, self.adjustSize)

    def _refresh(self):
        tags, metadata_files = self.archive.gather_metadata()
        self._populate(tags, metadata_files)

    def open_folder(self, url):
        source_container = getattr(self.archive, "source_container_path", None)
        file_path = str((source_container or self.archive.path).resolve())
        try:
            if sys.platform.startswith("win"):
                subprocess.run(["explorer", "/select,", file_path])
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", file_path])
            else:
                QDesktopServices.openUrl(QUrl(url))
        except Exception:
            QDesktopServices.openUrl(QUrl(url))

    def add_tags_dialog(self):
        dialog = QInputDialog(self)
        dialog.setWindowTitle(tr("file_info.add_tags_title"))
        dialog.setLabelText(tr("file_info.add_tags_prompt"))
        dialog.setOkButtonText(tr("common.ok"))
        dialog.setCancelButtonText(tr("common.cancel"))
        if dialog.exec() != QDialog.Accepted:
            return
        text = dialog.textValue()
        if not text.strip():
            return
        try:
            added, skipped = self.archive.add_tags(text)
        except ArchiveError as e:
            show_warning(self, tr("file_info.add_tags_failed"), str(e)); return
        except Exception as e:
            show_critical(self, tr("dialog.unexpected_error"), str(e)); return
        self._refresh()
        if not added and not skipped:
            return
        parts = []
        if added:
            parts.append(tr("file_info.tags_added", tags=", ".join(added)))
        if skipped:
            parts.append(tr("file_info.tags_skipped", tags=", ".join(skipped)))
        show_information(self, tr("file_info.tags_updated"), "\n".join(parts))
