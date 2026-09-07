"""Janela de Sumário: lista os arquivos suportados da pasta do
arquivo atualmente aberto, ordenados por sequência de numeração e ordem
alfabética, exibindo a capa de cada um em miniatura e permitindo clicar em
um item para carregá-lo."""
from PySide6.QtCore import Qt, QSize, QTimer, Signal, QObject, QRunnable, QThreadPool
from PySide6.QtGui import QIcon, QImage, QPixmap, QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel, QAbstractItemView, QStyle
)

from app.archive import ComicArchive, natural_key, SUPPORTED_FILE_EXTS
from app.compressed_collection import CompressedComicCollection, is_container_path
from app.i18n import tr

COMIC_EXTS = tuple(SUPPORTED_FILE_EXTS)

# Tamanho da miniatura (capa) e da célula da grade exibidas para cada arquivo.
THUMB_SIZE = QSize(130, 175)
GRID_SIZE = QSize(146, 206)

# Número fixo de colunas — a janela é dimensionada para caber exatamente
# essa quantidade de miniaturas por linha, sem espaço sobrando.
COLUMNS = 4


class _CoverSignals(QObject):
    done = Signal(int, QImage)
    failed = Signal(int, str)


class _CoverLoadTask(QRunnable):
    """Abre um arquivo de HQ em segundo plano só para extrair a 1ª página
    (capa) e gerar uma miniatura, sem travar a interface."""

    def __init__(self, index, path=None, max_dim=220, collection=None, member_index=None):
        super().__init__()
        self.index = index
        self.path = path
        self.max_dim = max_dim
        self.collection = collection
        self.member_index = member_index
        self.signals = _CoverSignals()

    def run(self):
        try:
            owned_collection = None
            if self.collection is not None:
                # Em coleções mistas, imagens avulsas são lidas diretamente
                # do contêiner para a capa; não ficam extraídas só porque o
                # usuário rolou pelo Sumário.
                img = self.collection.load_member_cover(
                    self.member_index, self.max_dim
                )
            elif is_container_path(self.path):
                owned_collection = CompressedComicCollection(self.path)
                try:
                    img = owned_collection.load_member_cover(0, self.max_dim)
                finally:
                    owned_collection.close()
            else:
                archive = ComicArchive(self.path)
                try:
                    img = archive.load_image(0, self.max_dim)
                finally:
                    archive.close()
            if img.mode != "RGB":
                img = img.convert("RGB")
            w, h = img.size
            raw = img.tobytes()
            qimg = QImage(raw, w, h, w * 3, QImage.Format_RGB888).copy()
            self.signals.done.emit(self.index, qimg)
        except Exception as e:  # noqa: BLE001
            self.signals.failed.emit(self.index, str(e))


class SummaryDialog(QDialog):
    """Mostra a lista de arquivos da pasta atual em ordem natural (número +
    alfabética), com miniaturas das capas, e emite file_selected(caminho)
    quando o usuário clica em um item para carregá-lo."""

    file_selected = Signal(str)
    member_selected = Signal(int)

    def __init__(self, folder=None, current_name=None, parent=None,
                 collection=None, current_member_index=None):
        super().__init__(parent)
        self.setWindowTitle(tr("summary.title"))
        self.setModal(False)

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.pool.setExpiryTimeout(30_000)
        self._tasks = []
        self._cover_requested = set()
        self._cover_timer = QTimer(self)
        self._cover_timer.setSingleShot(True)
        self._cover_timer.setInterval(40)
        self._cover_timer.timeout.connect(self._request_visible_covers)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)

        self.collection = collection
        self.current_member_index = current_member_index
        self._empty_message_key = None

        info_text = str(collection.path) if collection is not None else str(folder)
        self.info_label = QLabel(info_text)
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color: #9d9dae;")
        outer.addWidget(self.info_label)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("summaryPanel")
        self.list_widget.setViewMode(QListWidget.IconMode)
        self.list_widget.setIconSize(THUMB_SIZE)
        self.list_widget.setGridSize(GRID_SIZE)
        self.list_widget.setResizeMode(QListWidget.Adjust)
        self.list_widget.setMovement(QListWidget.Static)
        self.list_widget.setWordWrap(False)
        self.list_widget.setSpacing(0)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_widget.verticalScrollBar().valueChanged.connect(lambda _v: self._cover_timer.start())
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemActivated.connect(self._on_item_clicked)
        outer.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self.btn_close = QPushButton(tr("common.close"))
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_close)
        outer.addLayout(btn_row)

        if self.collection is not None:
            self._populate_collection(self.collection, self.current_member_index)
        else:
            self._populate(folder, current_name)

        width = self._ideal_width(outer)
        self.resize(width, 640)

    def retranslate_ui(self):
        self.setWindowTitle(tr("summary.title"))
        if hasattr(self, "btn_close"):
            self.btn_close.setText(tr("common.close"))
        if self._empty_message_key and self.list_widget.count() == 1:
            self.list_widget.item(0).setText(tr(self._empty_message_key))

    def _ideal_width(self, outer_layout):
        """Calcula a largura necessária para caber exatamente COLUMNS
        miniaturas por linha, incluindo a barra de rolagem, para não sobrar
        (nem faltar) espaço horizontal."""
        scrollbar = self.style().pixelMetric(QStyle.PM_ScrollBarExtent)
        frame = self.list_widget.frameWidth() * 2
        margins = outer_layout.contentsMargins()
        return (
            COLUMNS * GRID_SIZE.width()
            + scrollbar + frame
            + margins.left() + margins.right()
            + 4  # pequena folga de segurança contra arredondamento
        )

    def showEvent(self, event):
        super().showEvent(event)
        # A grade do QListView só calcula corretamente as posições depois
        # que a janela tem sua geometria final aplicada pelo gerenciador de
        # janelas; forçar um relayout logo após aparecer evita que as
        # miniaturas fiquem espalhadas com espaços em branco até o usuário
        # redimensionar a janela manualmente.
        QTimer.singleShot(0, self.list_widget.doItemsLayout)
        QTimer.singleShot(0, self._request_visible_covers)

    def _elide_to_thumb(self, text):
        """Reduz (elide) o nome do arquivo para caber na largura da
        miniatura, mostrando '...' no meio quando necessário."""
        metrics = QFontMetrics(self.list_widget.font())
        # um pouco menos que a largura do ícone, para sobrar respiro nas bordas
        max_width = max(THUMB_SIZE.width() - 10, 20)
        return metrics.elidedText(text, Qt.ElideMiddle, max_width)

    def _populate(self, folder, current_name):
        self._empty_message_key = None
        self.list_widget.clear()
        self._tasks = []
        self._cover_requested.clear()
        try:
            files = sorted(
                (f for f in folder.iterdir()
                 if f.is_file() and (
                     f.suffix.lower() in COMIC_EXTS or is_container_path(f)
                 )),
                key=lambda f: natural_key(f.name),
            )
        except OSError:
            files = []

        if not files:
            self._empty_message_key = "summary.no_files"
            placeholder = QListWidgetItem(tr(self._empty_message_key))
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            self.list_widget.addItem(placeholder)
            return

        current_row = None
        for f in files:
            is_current = current_name is not None and f.name == current_name
            label = f"▶ {f.name}" if is_current else f.name

            item = QListWidgetItem(self._elide_to_thumb(label))
            # Tamanho fixo definido de antemão (não depende de o ícone já
            # estar carregado), para que a grade não precise ser
            # recalculada mais tarde quando as capas chegarem.
            item.setSizeHint(GRID_SIZE)
            item.setToolTip(f.name)
            item.setData(Qt.UserRole, str(f))
            item.setTextAlignment(Qt.AlignHCenter)
            if is_current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

            self.list_widget.addItem(item)
            row = self.list_widget.count() - 1
            if is_current:
                current_row = row


        if current_row is not None:
            current_item = self.list_widget.item(current_row)
            self.list_widget.setCurrentRow(current_row)
            self.list_widget.scrollToItem(current_item)


    def _populate_collection(self, collection, current_member_index):
        """Lista CBZ/CBR e imagens internas como itens independentes."""
        self._empty_message_key = None
        self.list_widget.clear()
        self._tasks = []
        self._cover_requested.clear()

        if collection.count() <= 0:
            self._empty_message_key = "summary.no_collection_items"
            placeholder = QListWidgetItem(tr(self._empty_message_key))
            placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsSelectable & ~Qt.ItemIsEnabled)
            self.list_widget.addItem(placeholder)
            return

        current_row = None
        for index in range(collection.count()):
            name = collection.member_name(index)
            display_name = collection.member_display_name(index)
            is_current = index == current_member_index
            label = f"▶ {display_name}" if is_current else display_name

            item = QListWidgetItem(self._elide_to_thumb(label))
            item.setSizeHint(GRID_SIZE)
            item.setToolTip(name)
            item.setData(Qt.UserRole, name)
            item.setData(Qt.UserRole + 1, index)
            item.setTextAlignment(Qt.AlignHCenter)
            if is_current:
                font = item.font()
                font.setBold(True)
                item.setFont(font)

            self.list_widget.addItem(item)
            row = self.list_widget.count() - 1
            if is_current:
                current_row = row

        if current_row is not None:
            current_item = self.list_widget.item(current_row)
            self.list_widget.setCurrentRow(current_row)
            self.list_widget.scrollToItem(current_item)

    def _visible_cover_rows(self):
        count = self.list_widget.count()
        if count <= 0:
            return []
        vp = self.list_widget.viewport()
        cols = max(1, vp.width() // max(1, GRID_SIZE.width()))
        top = max(0, self.list_widget.verticalScrollBar().value())
        first_row = max(0, top // GRID_SIZE.height() - 1)
        last_row = (top + max(1, vp.height())) // GRID_SIZE.height() + 2
        start = first_row * cols
        end = min(count, (last_row + 1) * cols)
        return range(start, end)

    def _request_visible_covers(self):
        if not self.isVisible():
            return
        for row in self._visible_cover_rows():
            if row in self._cover_requested:
                continue
            item = self.list_widget.item(row)
            if item is None:
                continue
            path = item.data(Qt.UserRole)
            if not path:
                continue
            self._cover_requested.add(row)
            if self.collection is not None:
                member_index = item.data(Qt.UserRole + 1)
                self._request_cover(row, None, member_index=member_index)
            else:
                self._request_cover(row, path)

    def _request_cover(self, index, path, member_index=None):
        task = _CoverLoadTask(
            index, str(path) if path is not None else None,
            collection=self.collection if member_index is not None else None,
            member_index=member_index,
        )
        task.signals.done.connect(self._on_cover_ready)
        self._tasks.append(task)
        self.pool.start(task)

    def _on_cover_ready(self, index, qimg):
        if 0 <= index < self.list_widget.count() and not qimg.isNull():
            self.list_widget.item(index).setIcon(QIcon(QPixmap.fromImage(qimg)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._cover_timer.start()

    def _on_item_clicked(self, item):
        path = item.data(Qt.UserRole)
        if not path:
            return
        member_index = item.data(Qt.UserRole + 1)
        if self.collection is not None and member_index is not None:
            self.member_selected.emit(int(member_index))
        else:
            self.file_selected.emit(path)
        self.close()
