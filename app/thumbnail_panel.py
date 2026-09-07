"""Painel de miniaturas virtualizado para coleções de qualquer tamanho."""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QAbstractListModel, QModelIndex, QItemSelectionModel, Qt, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import QAbstractItemView, QListView

from app.pixmap_provider import PixmapProvider
from app.i18n import tr


THUMB_ICON_SIZE = QSize(216, 296)
THUMB_GRID_SIZE = QSize(236, 334)
_ICON_CACHE_LIMIT = 64


class _ThumbnailModel(QAbstractListModel):
    """Modelo virtual: nenhuma QListWidgetItem é criada por página.

    Com 100 mil páginas, o custo inicial continua praticamente constante; o
    QListView solicita texto/ícone apenas para as linhas que precisa desenhar.
    """

    def __init__(self, archive, parent=None):
        super().__init__(parent)
        self.archive = archive
        self._icons = OrderedDict()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802 - API Qt
        if parent.isValid() or self.archive is None:
            return 0
        return self.archive.count()

    def _is_text(self, row):
        return bool(
            hasattr(self.archive, "is_text_page")
            and self.archive.is_text_page(row)
        )

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= self.rowCount():
            return None
        if role == Qt.DisplayRole:
            return tr("thumbnail.text_suffix", number=row + 1) if self._is_text(row) else str(row + 1)
        if role == Qt.DecorationRole:
            icon = self._icons.get(row)
            if icon is not None:
                self._icons.move_to_end(row)
            return icon
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignHCenter)
        if role == Qt.SizeHintRole:
            return THUMB_GRID_SIZE
        if role == Qt.UserRole:
            return self._is_text(row)
        return None

    def set_icon(self, row, pixmap):
        if row < 0 or row >= self.rowCount():
            return
        self._icons.pop(row, None)
        self._icons[row] = QIcon(pixmap)
        changed = [row]
        while len(self._icons) > _ICON_CACHE_LIMIT:
            old_row, _old_icon = self._icons.popitem(last=False)
            changed.append(old_row)
        for target in changed:
            idx = self.index(target, 0)
            self.dataChanged.emit(idx, idx, [Qt.DecorationRole])

    def clear_icons(self):
        if not self._icons:
            return
        rows = list(self._icons)
        self._icons.clear()
        # Atualizações agrupadas quando possível.
        lo, hi = min(rows), max(rows)
        self.dataChanged.emit(self.index(lo, 0), self.index(hi, 0), [Qt.DecorationRole])


class ThumbnailPanel(QListView):
    """Grade virtual + carregamento lazy apenas da área visível.

    O QListWidget anterior ainda criava um objeto Python/Qt por página. Isso é
    aceitável para 300 páginas, mas vira um gargalo sério com 50k/100k imagens.
    QListView+Batched+QAbstractListModel mantém somente os dados visíveis.
    """

    page_selected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setIconSize(THUMB_ICON_SIZE)
        self.setGridSize(THUMB_GRID_SIZE)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setWrapping(True)
        self.setWordWrap(False)
        self.setSpacing(0)
        self.setUniformItemSizes(True)
        # Qt distribui o cálculo do layout em eventos pequenos, em vez de
        # calcular dezenas de milhares de posições antes de mostrar a janela.
        self.setLayoutMode(QListView.Batched)
        self.setBatchSize(128)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setObjectName("thumbnailPanel")

        # Proteção adicional para Windows/estilos nativos: o QListView usa
        # um viewport interno, e alguns estilos do sistema podem pintar esse
        # viewport com QPalette.Base (branco) antes/depois de aplicar o QSS.
        # Manter a paleta Base/Window explicitamente escura evita regressões
        # mesmo quando o tema do sistema muda ou o QSS é recarregado.
        dark = QColor("#101014")
        text = QColor("#e6e6ec")
        palette = self.palette()
        palette.setColor(QPalette.Base, dark)
        palette.setColor(QPalette.Window, dark)
        palette.setColor(QPalette.Text, text)
        palette.setColor(QPalette.WindowText, text)
        self.setPalette(palette)
        self.viewport().setPalette(palette)
        self.viewport().setAutoFillBackground(True)

        self.clicked.connect(lambda idx: self.page_selected.emit(idx.row()))

        self._thumb_provider = None
        self._archive = None
        self._model = None
        self._built = False
        self._pending_current = None
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.setInterval(28)
        self._load_timer.timeout.connect(self._request_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._schedule_visible())

    def retranslate_ui(self):
        if self._model is not None and self._model.rowCount() > 0:
            self._model.dataChanged.emit(
                self._model.index(0, 0),
                self._model.index(self._model.rowCount() - 1, 0),
                [Qt.DisplayRole],
            )

    def prepare(self, archive):
        if self._thumb_provider is not None:
            self._thumb_provider.shutdown()
        self._thumb_provider = None
        self._archive = archive
        self._built = False
        self._pending_current = None
        self._load_timer.stop()
        if self._model is not None:
            old = self._model
            self.setModel(None)
            self._model = None
            old.deleteLater()

    def ensure_built(self):
        if not self._built and self._archive is not None:
            self.build()

    def build(self, archive=None):
        if archive is not None:
            self.prepare(archive)
        archive = self._archive
        if archive is None or self._built:
            return

        self._model = _ThumbnailModel(archive, self)
        self.setModel(self._model)
        self._thumb_provider = PixmapProvider(
            archive,
            cache_size=32,
            max_dim=340,
            workers=2,
            max_cache_bytes=16 * 1024 * 1024,
            display_cache_size=1,
        )
        self._thumb_provider.pixmap_ready.connect(self._on_thumb)
        self._built = True

        pending = self._pending_current
        self._pending_current = None
        if pending is not None:
            self.set_current(pending)
        QTimer.singleShot(0, self._request_visible)

    def clear_provider(self):
        if self._thumb_provider is not None:
            self._thumb_provider.shutdown()
            self._thumb_provider = None
        self._load_timer.stop()

    def _schedule_visible(self, immediate=False):
        if self._thumb_provider is None:
            return
        if immediate:
            QTimer.singleShot(0, self._request_visible)
        else:
            self._load_timer.start()

    def _visible_range(self):
        model = self._model
        count = model.rowCount() if model is not None else 0
        if count <= 0:
            return 0, 0
        viewport = self.viewport()
        cols = max(1, viewport.width() // max(1, THUMB_GRID_SIZE.width()))
        top = max(0, self.verticalScrollBar().value())
        height = max(1, viewport.height())
        row_h = THUMB_GRID_SIZE.height()
        first_row = max(0, top // row_h - 1)
        last_row = (top + height) // row_h + 2
        start = min(count, first_row * cols)
        end = min(count, (last_row + 1) * cols)
        return start, max(start, end)

    def _request_visible(self):
        if self._thumb_provider is None or self._model is None or not self.isVisible():
            return
        start, end = self._visible_range()
        center = (start + end) // 2
        order = sorted(range(start, end), key=lambda i: abs(i - center))
        for pos, row in enumerate(order):
            idx = self._model.index(row, 0)
            if bool(self._model.data(idx, Qt.UserRole)):
                continue
            self._thumb_provider.get(row, priority=max(5, 28 - pos))

    def _on_thumb(self, index, pix):
        if self._model is not None:
            self._model.set_icon(index, pix)

    def showEvent(self, event):
        super().showEvent(event)
        self.ensure_built()
        self._schedule_visible(immediate=True)

    def hideEvent(self, event):
        self._load_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_visible()

    def set_current(self, index):
        if not self._built or self._model is None:
            self._pending_current = int(index)
            return
        if 0 <= index < self._model.rowCount():
            model_index = self._model.index(int(index), 0)
            self.selectionModel().setCurrentIndex(
                model_index,
                QItemSelectionModel.ClearAndSelect,
            )
            self.scrollTo(model_index, QAbstractItemView.PositionAtCenter)
            self._schedule_visible()
