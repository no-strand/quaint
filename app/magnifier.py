"""Lente de aumento circular e leve para as visualizações de imagem."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget


class MagnifierOverlay(QWidget):
    """Overlay circular que amplia pixels já disponíveis na visualização.

    O overlay nunca lê o arquivo de origem nem solicita uma nova decodificação.
    O *sampler* recebe a posição no viewport e retorna ``(pixmap, ponto)`` no
    sistema de coordenadas do pixmap que já está em memória.
    """

    DIAMETER = 360
    ZOOM = 3.25
    UPDATE_INTERVAL_MS = 20  # polling barato do cursor; pintura só quando necessário.
    BORDER_WIDTH = 4

    def __init__(self, viewport: QWidget, sampler, parent=None):
        # O viewport precisa ser o pai visual para o círculo poder cobrir toda
        # a área de leitura sem criar uma janela nativa separada.
        super().__init__(viewport)
        self._viewport = viewport
        self._sampler = sampler
        self._enabled = False
        self._inside = False
        self._cursor_pos = QPoint(-10000, -10000)

        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

        self._timer = QTimer(self)
        self._timer.setInterval(self.UPDATE_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    def set_target(self, viewport: QWidget, sampler):
        """Move o mesmo overlay para outro modo de leitura."""
        if viewport is self._viewport and sampler == self._sampler:
            self.setGeometry(viewport.rect())
            return
        was_enabled = self._enabled
        self._timer.stop()
        self.hide()
        self._viewport = viewport
        self._sampler = sampler
        self.setParent(viewport)
        self.setGeometry(viewport.rect())
        self.raise_()
        if was_enabled:
            self.show()
            self._timer.start()
            self._tick()

    def set_enabled(self, enabled: bool):
        enabled = bool(enabled)
        self._enabled = enabled
        if not enabled:
            self._timer.stop()
            self._inside = False
            self.hide()
            return
        if self._viewport is None:
            return
        self.setGeometry(self._viewport.rect())
        self.show()
        self.raise_()
        if not self._timer.isActive():
            self._timer.start()
        self._tick()

    def is_enabled(self) -> bool:
        return self._enabled

    def _lens_update_rect(self, pos: QPoint) -> QRect:
        radius = self.DIAMETER // 2 + self.BORDER_WIDTH + 4
        return QRect(pos.x() - radius, pos.y() - radius, radius * 2, radius * 2)

    def request_refresh(self):
        """Repinta somente a lente atual (novo frame, scroll ou resize)."""
        if self._enabled and self._inside:
            self.update(self._lens_update_rect(self._cursor_pos))

    def _tick(self):
        if not self._enabled or self._viewport is None or not self._viewport.isVisible():
            return

        geometry_changed = self.geometry() != self._viewport.rect()
        if geometry_changed:
            self.setGeometry(self._viewport.rect())

        old_pos = QPoint(self._cursor_pos)
        old_inside = self._inside
        local = self._viewport.mapFromGlobal(QCursor.pos())
        self._inside = self._viewport.rect().contains(local)
        self._cursor_pos = local
        moved = old_pos != local or old_inside != self._inside

        # O timer apenas acompanha a posição global do cursor. A pintura ocorre
        # quando ele realmente se move; frames animados e scroll chamam
        # request_refresh() explicitamente, evitando redesenhar 50x/s numa
        # página estática com o mouse parado.
        if moved or geometry_changed:
            if old_inside:
                self.update(self._lens_update_rect(old_pos))
            if self._inside:
                self.update(self._lens_update_rect(local))

    def paintEvent(self, event):  # noqa: N802
        if not self._enabled or not self._inside or self._sampler is None:
            return

        sample = self._sampler(self._cursor_pos)
        if not sample:
            return
        pixmap, source_point = sample
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return

        diameter = float(self.DIAMETER)
        radius = diameter / 2.0
        lens_rect = QRectF(
            self._cursor_pos.x() - radius,
            self._cursor_pos.y() - radius,
            diameter,
            diameter,
        )

        source_w = min(diameter / self.ZOOM, float(pixmap.width()))
        source_h = min(diameter / self.ZOOM, float(pixmap.height()))
        half_w, half_h = source_w / 2.0, source_h / 2.0

        # Mantém a amostra dentro do pixmap. Nas bordas da imagem o círculo
        # continua cheio em vez de mostrar uma faixa transparente.
        if pixmap.width() <= source_w:
            center_x = pixmap.width() / 2.0
        else:
            center_x = min(max(source_point.x(), half_w), pixmap.width() - half_w)
        if pixmap.height() <= source_h:
            center_y = pixmap.height() / 2.0
        else:
            center_y = min(max(source_point.y(), half_h), pixmap.height() - half_h)
        source_rect = QRectF(
            center_x - half_w,
            center_y - half_h,
            source_w,
            source_h,
        )

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        path = QPainterPath()
        path.addEllipse(lens_rect)
        painter.setClipPath(path)
        painter.fillRect(lens_rect, QColor(18, 18, 22, 245))
        painter.drawPixmap(lens_rect, pixmap, source_rect)
        painter.setClipping(False)

        # Borda dupla discreta para manter a lente visível tanto sobre páginas
        # brancas quanto sobre páginas escuras.
        painter.setPen(QPen(QColor(0, 0, 0, 190), self.BORDER_WIDTH + 2))
        painter.drawEllipse(lens_rect)
        painter.setPen(QPen(QColor(245, 245, 245, 235), self.BORDER_WIDTH))
        painter.drawEllipse(lens_rect.adjusted(1.0, 1.0, -1.0, -1.0))
        painter.end()
