"""Painel visual de marcadores com miniaturas e remoção direta."""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr


CARD_WIDTH = 194
CARD_HEIGHT = 264
THUMB_WIDTH = 176
THUMB_HEIGHT = 218


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _BookmarkCard(QFrame):
    activated = Signal(int)
    remove_requested = Signal(int)

    def __init__(self, index: int, parent=None):
        super().__init__(parent)
        self.index = int(index)
        self.setObjectName("bookmarkCard")
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.image_holder = QFrame()
        self.image_holder.setObjectName("bookmarkImageHolder")
        self.image_holder.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)

        self.thumb = _ClickableLabel(self.image_holder)
        self.thumb.setGeometry(0, 0, THUMB_WIDTH, THUMB_HEIGHT)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setText(tr("bookmark.loading"))
        self.thumb.setToolTip(tr("bookmark.open_tooltip"))
        self.thumb.clicked.connect(lambda: self.activated.emit(self.index))

        self.remove_button = QToolButton(self.image_holder)
        self.remove_button.setObjectName("bookmarkRemoveButton")
        self.remove_button.setIcon(self.style().standardIcon(QStyle.SP_TitleBarCloseButton))
        self.remove_button.setIconSize(QSize(13, 13))
        self.remove_button.setFixedSize(28, 28)
        self.remove_button.move(THUMB_WIDTH - 34, 6)
        self.remove_button.setToolTip(tr("bookmark.remove"))
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.index))
        self.remove_button.raise_()

        self.page_label = QLabel(tr("bookmark.page", page=self.index + 1))
        self.page_label.setObjectName("bookmarkPageLabel")
        self.page_label.setAlignment(Qt.AlignCenter)

        root.addWidget(self.image_holder, 0, Qt.AlignHCenter)
        root.addWidget(self.page_label)

    def set_thumbnail(self, pixmap: QPixmap | None):
        if pixmap is None or pixmap.isNull():
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText(tr("bookmark.no_preview"))
            return
        scaled = pixmap.scaled(
            THUMB_WIDTH - 8,
            THUMB_HEIGHT - 8,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.thumb.setText("")
        self.thumb.setPixmap(scaled)

    def retranslate_ui(self):
        self.page_label.setText(tr("bookmark.page", page=self.index + 1))
        self.remove_button.setToolTip(tr("bookmark.remove"))
        self.thumb.setToolTip(tr("bookmark.open_tooltip"))
        if self.thumb.pixmap() is None or self.thumb.pixmap().isNull():
            self.thumb.setText(tr("bookmark.no_preview"))


class BookmarkPanel(QScrollArea):
    """Grade pequena de marcadores.

    Diferentemente do painel geral de miniaturas, o número de marcadores tende
    a ser pequeno. Por isso uma grade de cartões é mais simples e permite um
    botão de remoção sobre cada própria miniatura.
    """

    page_selected = Signal(int)
    remove_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bookmarkPanel")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)

        self._content = QWidget()
        self._content.setObjectName("bookmarkContent")
        self._grid = QGridLayout(self._content)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.setWidget(self._content)

        self._empty = QLabel(tr("bookmark.empty"), self._content)
        self._empty.setObjectName("bookmarkEmptyLabel")
        self._empty.setAlignment(Qt.AlignCenter)
        self._grid.addWidget(self._empty, 0, 0)

        self._provider = None
        self._archive = None
        self._indices = []
        self._cards: dict[int, _BookmarkCard] = {}

    def set_source(self, archive, provider, indices):
        old_provider = self._provider
        if old_provider is not None:
            try:
                old_provider.pixmap_ready.disconnect(self._on_pixmap_ready)
            except (RuntimeError, TypeError):
                pass

        self._archive = archive
        self._provider = provider
        self._indices = sorted({int(i) for i in (indices or []) if int(i) >= 0})
        if provider is not None:
            provider.pixmap_ready.connect(self._on_pixmap_ready)
        self._rebuild()

    def clear_source(self):
        self.set_source(None, None, [])

    def _clear_grid(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._empty:
                widget.deleteLater()
        self._cards.clear()

    def _rebuild(self):
        self._clear_grid()
        if not self._indices:
            self._empty.setText(tr("bookmark.empty"))
            self._empty.show()
            self._grid.addWidget(self._empty, 0, 0)
            return
        self._empty.hide()

        cols = self._column_count()
        for pos, index in enumerate(self._indices):
            card = _BookmarkCard(index, self._content)
            card.activated.connect(self.page_selected)
            card.remove_requested.connect(self.remove_requested)
            self._cards[index] = card
            self._grid.addWidget(card, pos // cols, pos % cols)
            self._request_thumbnail(index)

    def _column_count(self):
        width = max(CARD_WIDTH, self.viewport().width() - 12)
        return max(1, width // (CARD_WIDTH + 8))

    def _reflow(self):
        if not self._cards:
            return
        cards = [self._cards[i] for i in self._indices if i in self._cards]
        while self._grid.count():
            self._grid.takeAt(0)
        cols = self._column_count()
        for pos, card in enumerate(cards):
            self._grid.addWidget(card, pos // cols, pos % cols)

    def _request_thumbnail(self, index):
        if self._provider is None or self._archive is None:
            return
        try:
            if hasattr(self._archive, "is_text_page") and self._archive.is_text_page(index):
                self._cards[index].set_thumbnail(None)
                return
            pix = self._provider.get(index, priority=70)
            if pix is not None:
                self._cards[index].set_thumbnail(pix)
        except Exception:
            self._cards[index].set_thumbnail(None)

    def _on_pixmap_ready(self, index, pixmap):
        card = self._cards.get(int(index))
        if card is not None:
            card.set_thumbnail(pixmap)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._reflow()

    def retranslate_ui(self):
        self._empty.setText(tr("bookmark.empty"))
        for card in self._cards.values():
            card.retranslate_ui()
