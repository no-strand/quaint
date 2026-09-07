"""Ferramenta de recorte simples: selecionar, copiar ou salvar uma região."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRect, QPoint, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QVBoxLayout,
)

from app.i18n import tr
from app.message_boxes import warning as show_warning


class CropLabel(QLabel):
    """Exibe a imagem e mantém uma seleção retangular persistente.

    A seleção é pintada pelo próprio widget em vez de usar QRubberBand. Isso
    evita que o retângulo desapareça ao soltar o mouse ou quando outro controle
    da janela recebe foco.
    """

    selectionChanged = Signal(QRect)

    def __init__(self, source_image, parent=None):
        super().__init__(parent)
        if isinstance(source_image, QPixmap):
            source_image = source_image.toImage()
        # QImage é implicitamente compartilhado; preserve a imagem integral
        # sem fazer uma segunda cópia profunda de todos os pixels.
        self.source_image = QImage(source_image)
        self.display_image = self.source_image
        self.display_pixmap = QPixmap()
        self._origin = QPoint()
        self._selection = QRect()
        self._dragging = False
        self.setAlignment(Qt.AlignCenter)
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._fit_for_dialog()

    def _fit_for_dialog(self):
        max_w, max_h = 980, 680
        image = self.source_image
        if image.width() > max_w or image.height() > max_h:
            self.display_image = image.scaled(
                max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        else:
            self.display_image = image
        # Só a cópia pequena usada para exibição vira QPixmap. A imagem
        # original permanece QImage, evitando duplicar toda a memória da foto.
        self.display_pixmap = QPixmap.fromImage(self.display_image)
        self.setPixmap(self.display_pixmap)
        self.resize(self.display_pixmap.size())
        self.setMinimumSize(self.display_pixmap.size())

    def _bounded_point(self, point: QPoint) -> QPoint:
        rect = self.rect()
        return QPoint(
            max(rect.left(), min(point.x(), rect.right())),
            max(rect.top(), min(point.y(), rect.bottom())),
        )

    def _set_selection(self, rect: QRect):
        rect = rect.normalized().intersected(self.rect())
        if rect.width() < 2 or rect.height() < 2:
            rect = QRect()
        if rect == self._selection:
            self.update()
            return
        self._selection = rect
        self.selectionChanged.emit(QRect(self._selection))
        self.update()

    def clear_selection(self):
        self._dragging = False
        self._origin = QPoint()
        self._set_selection(QRect())

    def has_selection(self) -> bool:
        return self._selection.width() >= 2 and self._selection.height() >= 2

    def selection_rect(self) -> QRect:
        return QRect(self._selection)

    def source_selection_rect(self) -> QRect:
        rect = self._selection
        if rect.width() < 2 or rect.height() < 2:
            return QRect()
        sx = self.source_image.width() / max(self.display_image.width(), 1)
        sy = self.source_image.height() / max(self.display_image.height(), 1)
        return QRect(
            int(round(rect.x() * sx)),
            int(round(rect.y() * sy)),
            max(1, int(round(rect.width() * sx))),
            max(1, int(round(rect.height() * sy))),
        ).intersected(self.source_image.rect())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        self._dragging = True
        self._origin = self._bounded_point(event.position().toPoint())
        # Um novo clique inicia uma nova seleção de forma inequívoca.
        self._selection = QRect(self._origin, self._origin)
        self.selectionChanged.emit(QRect())
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            point = self._bounded_point(event.position().toPoint())
            self._set_selection(QRect(self._origin, point))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._dragging:
            point = self._bounded_point(event.position().toPoint())
            self._dragging = False
            self._set_selection(QRect(self._origin, point))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.has_selection():
            return

        rect = self._selection.normalized().intersected(self.rect())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        # Escurece apenas a área fora da seleção, mantendo o conteúdo escolhido
        # totalmente visível e o retângulo persistente após soltar o mouse.
        shade = QColor(0, 0, 0, 105)
        full = self.rect()
        for outside in (
            QRect(full.left(), full.top(), full.width(), max(0, rect.top() - full.top())),
            QRect(full.left(), rect.bottom() + 1, full.width(), max(0, full.bottom() - rect.bottom())),
            QRect(full.left(), rect.top(), max(0, rect.left() - full.left()), rect.height()),
            QRect(rect.right() + 1, rect.top(), max(0, full.right() - rect.right()), rect.height()),
        ):
            if outside.width() > 0 and outside.height() > 0:
                painter.fillRect(outside, shade)

        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.drawRect(rect.adjusted(1, 1, -1, -1))
        painter.setPen(QPen(QColor(0, 0, 0, 190), 1, Qt.DashLine))
        painter.drawRect(rect.adjusted(3, 3, -3, -3))

        # Pequenos cantos tornam visualmente óbvio que a área continua ativa.
        handle = 7
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255))
        for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            painter.drawRect(
                point.x() - handle // 2,
                point.y() - handle // 2,
                handle,
                handle,
            )
        painter.end()

    def cropped_image(self):
        source_rect = self.source_selection_rect()
        if source_rect.width() < 2 or source_rect.height() < 2:
            return QImage()
        return self.source_image.copy(source_rect)

    def cropped_pixmap(self):
        """Compatibilidade com chamadas antigas/testes externos."""
        image = self.cropped_image()
        return QPixmap() if image.isNull() else QPixmap.fromImage(image)


class CropDialog(QDialog):
    def __init__(self, image, parent=None, initial_dir=""):
        super().__init__(parent)
        self.setWindowTitle(tr("crop.title"))
        self.resize(1050, 800)
        self.initial_dir = initial_dir

        outer = QVBoxLayout(self)
        hint = QLabel(tr("crop.hint"))
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.selection_status = QLabel(tr("crop.no_selection"))
        self.selection_status.setWordWrap(True)
        outer.addWidget(self.selection_status)

        scroll = QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(Qt.AlignCenter)
        self.crop_label = CropLabel(image)
        self.crop_label.selectionChanged.connect(self._selection_changed)
        scroll.setWidget(self.crop_label)
        outer.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        self.clear_button = QPushButton(tr("crop.clear"))
        self.clear_button.clicked.connect(self.crop_label.clear_selection)
        self.clear_button.setEnabled(False)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        self.copy_button = QPushButton(tr("crop.copy"))
        self.copy_button.clicked.connect(self.copy_crop)
        self.copy_button.setEnabled(False)
        buttons.addWidget(self.copy_button)
        self.save_button = QPushButton(tr("crop.save"))
        self.save_button.clicked.connect(self.save_crop)
        self.save_button.setEnabled(False)
        buttons.addWidget(self.save_button)
        close_button = QPushButton(tr("common.close"))
        close_button.clicked.connect(self.close)
        buttons.addWidget(close_button)
        outer.addLayout(buttons)

    def _selection_changed(self, _rect: QRect):
        source_rect = self.crop_label.source_selection_rect()
        valid = source_rect.width() >= 2 and source_rect.height() >= 2
        self.copy_button.setEnabled(valid)
        self.save_button.setEnabled(valid)
        self.clear_button.setEnabled(valid)
        if valid:
            self.selection_status.setText(
                tr("crop.selection_size", width=source_rect.width(), height=source_rect.height())
            )
        else:
            self.selection_status.setText(tr("crop.no_selection"))

    def _selected(self):
        image = self.crop_label.cropped_image()
        if image.isNull():
            show_warning(self, tr("crop.title"), tr("crop.select_first"))
            return None
        return image

    def copy_crop(self):
        image = self._selected()
        if image is None:
            return
        QApplication.clipboard().setImage(image)
        self.selection_status.setText(tr("crop.copied"))

    def save_crop(self):
        image = self._selected()
        if image is None:
            return
        initial = str(Path(self.initial_dir or ".") / tr("crop.default_name"))
        path, _ = QFileDialog.getSaveFileName(
            self, tr("crop.save"), initial,
            "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;BMP (*.bmp)"
        )
        if not path:
            return
        target = Path(path)
        if not target.suffix:
            target = target.with_suffix(".png")
        fmt = target.suffix.lstrip(".").upper()
        if fmt == "JPG":
            fmt = "JPEG"
        if not image.save(str(target), fmt):
            show_warning(self, tr("crop.title"), tr("crop.save_failed"))
            return
        self.selection_status.setText(tr("crop.saved", path=str(target)))
