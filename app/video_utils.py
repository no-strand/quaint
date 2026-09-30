"""Small video helpers kept separate from QtMultimedia.

The extension set itself lives in :mod:`app.format_defs` so startup checks stay
cheap.  This module only contains GUI thumbnail drawing and is imported lazily
by panels that actually need it.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

_ICON_CACHE: dict[tuple[int, int], QPixmap] = {}


def video_placeholder_pixmap(size: QSize | tuple[int, int]) -> QPixmap:
    """Return a cached film/play placeholder without opening the video file."""
    if isinstance(size, QSize):
        width, height = max(1, size.width()), max(1, size.height())
    else:
        width, height = max(1, int(size[0])), max(1, int(size[1]))
    key = (width, height)
    cached = _ICON_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached

    pix = QPixmap(width, height)
    pix.fill(QColor("#17171d"))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing, True)

    margin = max(8.0, min(width, height) * 0.08)
    body = QRectF(margin, margin, width - 2 * margin, height - 2 * margin)
    painter.setPen(QPen(QColor("#5b5b68"), max(1.0, min(width, height) * 0.012)))
    painter.setBrush(QColor("#24242d"))
    painter.drawRoundedRect(body, 8.0, 8.0)

    # Film perforations make videos distinguishable from normal images even
    # when the play triangle is small in the index panel.
    hole_w = max(3.0, body.width() * 0.055)
    hole_h = max(4.0, body.height() * 0.055)
    step = max(hole_h * 1.75, 12.0)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor("#777786"))
    y = body.top() + hole_h
    while y + hole_h < body.bottom():
        painter.drawRoundedRect(QRectF(body.left() + 6, y, hole_w, hole_h), 1.5, 1.5)
        painter.drawRoundedRect(QRectF(body.right() - hole_w - 6, y, hole_w, hole_h), 1.5, 1.5)
        y += step

    center_x, center_y = body.center().x(), body.center().y()
    tri_h = min(body.width(), body.height()) * 0.28
    tri_w = tri_h * 0.78
    triangle = QPolygonF([
        QPointF(center_x - tri_w * 0.42, center_y - tri_h * 0.52),
        QPointF(center_x - tri_w * 0.42, center_y + tri_h * 0.52),
        QPointF(center_x + tri_w * 0.62, center_y),
    ])
    painter.setBrush(QColor("#e8e8ef"))
    painter.drawPolygon(triangle)
    painter.end()

    _ICON_CACHE[key] = pix
    return pix
