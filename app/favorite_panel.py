"""Painel visual dos itens favoritos do Quaint.

Favoritos representam o item aberto inteiro (arquivo, imagem avulsa ou pasta),
diferentemente de Bookmarks, que representam páginas dentro do item atual.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QSize, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QLabel, QScrollArea, QStyle, QToolButton,
    QVBoxLayout, QWidget,
)

from app.archive import ArchiveError, ComicArchive, STANDALONE_IMAGE_EXTS
from app.compressed_collection import (
    CompressedComicCollection, DirectoryComicCollection, is_container_path,
)
from app.i18n import tr

CARD_WIDTH = 194
CARD_HEIGHT = 286
THUMB_WIDTH = 176
THUMB_HEIGHT = 218


def _preview_cache_dir():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    path = base / "Quaint" / "favorite_thumbs"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def _preview_cache_path(path, max_dim):
    try:
        p = Path(path)
        st = p.stat()
        token = f"{p.resolve()}|{st.st_mtime_ns}|{st.st_size}|{int(max_dim)}"
    except OSError:
        return None
    digest = hashlib.sha1(token.encode("utf-8", "surrogatepass")).hexdigest()
    root = _preview_cache_dir()
    return (root / f"{digest}.png") if root is not None else None


def _trim_preview_cache(root, keep=180):
    try:
        files = [p for p in root.iterdir() if p.suffix.lower() == ".png"]
        if len(files) <= keep + 24:
            return
        files.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for old in files[keep:]:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _first_image_from_archive(archive, max_dim):
    for index in range(archive.count()):
        if not archive.is_text_page(index):
            return archive.load_image(index, max_dim=max_dim)
    return None


def favorite_preview_png(path, max_dim=360):
    """Gera uma prévia PNG pequena para um caminho favorito.

    A função é independente da interface e pode rodar em uma thread. Nunca
    mantém arquivos/coleções abertos após terminar a miniatura.
    """
    p = Path(path)
    if not p.exists():
        return None

    cache_path = _preview_cache_path(p, max_dim)
    if cache_path is not None and cache_path.exists():
        try:
            data = cache_path.read_bytes()
            if data:
                return data
        except OSError:
            pass

    img = None
    archive = None
    collection = None
    try:
        if p.is_dir():
            # Pastas comuns com imagens são rápidas pelo ComicArchive. Se a
            # pasta contiver apenas CBZ/CBR/compactados, cai para a coleção.
            try:
                archive = ComicArchive(p)
                img = _first_image_from_archive(archive, max_dim)
            except Exception:
                if archive is not None:
                    archive.close()
                    archive = None
                collection = DirectoryComicCollection(p)
                if collection.count():
                    img = collection.load_member_cover(0, max_dim=max_dim)
        elif is_container_path(p):
            collection = CompressedComicCollection(p)
            if collection.count():
                img = collection.load_member_cover(0, max_dim=max_dim)
        else:
            archive = ComicArchive(p)
            img = _first_image_from_archive(archive, max_dim)

        if img is None:
            return None
        img = img.convert("RGBA") if "A" in img.getbands() else img.convert("RGB")
        img.thumbnail((int(max_dim), int(max_dim)), Image.Resampling.LANCZOS, reducing_gap=3.0)
        out = io.BytesIO()
        # Miniaturas priorizam latência; compress_level baixo economiza CPU e
        # continua pequeno o bastante para um cache de previews.
        img.save(out, format="PNG", compress_level=3)
        data = out.getvalue()
        if cache_path is not None:
            try:
                fd, temp_name = tempfile.mkstemp(prefix="fav_", suffix=".tmp", dir=str(cache_path.parent))
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                os.replace(temp_name, cache_path)
                _trim_preview_cache(cache_path.parent)
            except OSError:
                try:
                    os.unlink(temp_name)
                except (OSError, UnboundLocalError):
                    pass
        return data
    except (ArchiveError, OSError, ValueError, RuntimeError):
        return None
    finally:
        if archive is not None:
            try:
                archive.close()
            except Exception:
                pass
        if collection is not None:
            try:
                collection.close()
            except Exception:
                pass


class _PreviewSignals(QObject):
    ready = Signal(str, object)


class _PreviewTask(QRunnable):
    def __init__(self, path):
        super().__init__()
        self.path = str(path)
        self.signals = _PreviewSignals()

    def run(self):
        self.signals.ready.emit(self.path, favorite_preview_png(self.path))


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt API
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _FavoriteCard(QFrame):
    activated = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.setObjectName("favoriteCard")
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(5)

        self.image_holder = QFrame()
        self.image_holder.setObjectName("favoriteImageHolder")
        self.image_holder.setFixedSize(THUMB_WIDTH, THUMB_HEIGHT)

        self.thumb = _ClickableLabel(self.image_holder)
        self.thumb.setGeometry(0, 0, THUMB_WIDTH, THUMB_HEIGHT)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setText(tr("favorite.loading"))
        self.thumb.setToolTip(tr("favorite.open_tooltip"))
        self.thumb.clicked.connect(lambda: self.activated.emit(self.path))

        self.remove_button = QToolButton(self.image_holder)
        self.remove_button.setObjectName("favoriteRemoveButton")
        self.remove_button.setIcon(self.style().standardIcon(QStyle.SP_TitleBarCloseButton))
        self.remove_button.setIconSize(QSize(13, 13))
        self.remove_button.setFixedSize(28, 28)
        self.remove_button.move(THUMB_WIDTH - 34, 6)
        self.remove_button.setToolTip(tr("favorite.remove"))
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self.path))
        self.remove_button.raise_()

        p = Path(self.path)
        self.name_label = QLabel(p.name or str(p))
        self.name_label.setObjectName("favoriteNameLabel")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setToolTip(self.path)

        self.kind_label = QLabel(self._kind_text())
        self.kind_label.setObjectName("favoriteKindLabel")
        self.kind_label.setAlignment(Qt.AlignCenter)

        root.addWidget(self.image_holder, 0, Qt.AlignHCenter)
        root.addWidget(self.name_label)
        root.addWidget(self.kind_label)

    def _kind_text(self):
        p = Path(self.path)
        if p.is_dir():
            return tr("favorite.folder")
        if p.suffix.lower() in STANDALONE_IMAGE_EXTS:
            return tr("favorite.image")
        return tr("favorite.file")

    def set_thumbnail_bytes(self, data):
        if not data:
            self.thumb.setPixmap(QPixmap())
            self.thumb.setText(tr("favorite.no_preview"))
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "PNG"):
            self.thumb.setText(tr("favorite.no_preview"))
            return
        scaled = pixmap.scaled(
            THUMB_WIDTH - 8, THUMB_HEIGHT - 8,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        self.thumb.setText("")
        self.thumb.setPixmap(scaled)

    def retranslate_ui(self):
        self.remove_button.setToolTip(tr("favorite.remove"))
        self.thumb.setToolTip(tr("favorite.open_tooltip"))
        self.kind_label.setText(self._kind_text())
        if self.thumb.pixmap() is None or self.thumb.pixmap().isNull():
            self.thumb.setText(tr("favorite.no_preview"))


class FavoritePanel(QScrollArea):
    path_selected = Signal(str)
    remove_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("favoritePanel")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)

        self._content = QWidget()
        self._content.setObjectName("favoriteContent")
        self._grid = QGridLayout(self._content)
        self._grid.setContentsMargins(8, 8, 8, 8)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        self.setWidget(self._content)

        self._empty = QLabel(tr("favorite.empty"), self._content)
        self._empty.setAlignment(Qt.AlignCenter)
        self._grid.addWidget(self._empty, 0, 0)

        self._paths = []
        self._cards = {}
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(2)
        self._thread_pool.setExpiryTimeout(10_000)
        self._tasks = set()

    def set_paths(self, paths):
        self._paths = [str(p) for p in (paths or [])]
        self._rebuild()

    def _clear_grid(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._empty:
                widget.deleteLater()
        self._cards.clear()

    def _rebuild(self):
        self._clear_grid()
        if not self._paths:
            self._empty.setText(tr("favorite.empty"))
            self._empty.show()
            self._grid.addWidget(self._empty, 0, 0)
            return
        self._empty.hide()
        cols = self._column_count()
        for pos, path in enumerate(self._paths):
            card = _FavoriteCard(path, self._content)
            card.activated.connect(self.path_selected)
            card.remove_requested.connect(self.remove_requested)
            self._cards[path] = card
            self._grid.addWidget(card, pos // cols, pos % cols)
            self._request_thumbnail(path)

    def _request_thumbnail(self, path):
        task = _PreviewTask(path)
        self._tasks.add(task)

        def ready(item_path, data, task_ref=task):
            self._tasks.discard(task_ref)
            card = self._cards.get(str(item_path))
            if card is not None:
                card.set_thumbnail_bytes(data)

        task.signals.ready.connect(ready)
        self._thread_pool.start(task)

    def _column_count(self):
        width = max(CARD_WIDTH, self.viewport().width() - 12)
        return max(1, width // (CARD_WIDTH + 8))

    def _reflow(self):
        if not self._cards:
            return
        cards = [self._cards[p] for p in self._paths if p in self._cards]
        while self._grid.count():
            self._grid.takeAt(0)
        cols = self._column_count()
        for pos, card in enumerate(cards):
            self._grid.addWidget(card, pos // cols, pos % cols)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._reflow()

    def retranslate_ui(self):
        self._empty.setText(tr("favorite.empty"))
        for card in self._cards.values():
            card.retranslate_ui()
