"""Badge leve de formato para cartões de miniatura.

A etiqueta é pintada pelo delegate em vez de criar um QLabel por item. Isso
mantém o custo praticamente constante em listas virtualizadas/grandes.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QStyledItemDelegate


FORMAT_ROLE = int(Qt.UserRole) + 20
_COMPOUND_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz")


def format_label_from_name(name, fallback=""):
    """Retorna uma extensão curta, sem ponto, adequada para o badge."""
    value = str(name or "").strip()
    lower = value.casefold()
    for suffix in _COMPOUND_SUFFIXES:
        if lower.endswith(suffix):
            return suffix[1:]
    suffix = Path(value).suffix.lower().lstrip(".") if value else ""
    if suffix:
        return suffix
    return str(fallback or "").strip().lower().lstrip(".")


class FormatBadgeDelegate(QStyledItemDelegate):
    """Pinta o formato no canto inferior direito da área da miniatura."""

    def __init__(self, icon_size, parent=None):
        super().__init__(parent)
        self._icon_size = icon_size

    def paint(self, painter, option, index):  # noqa: N802 - API Qt
        super().paint(painter, option, index)
        label = str(index.data(FORMAT_ROLE) or "").strip()
        if not label:
            return

        painter.save()
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            font = option.font
            if font.pointSizeF() > 0:
                font.setPointSizeF(max(7.0, font.pointSizeF() - 2.0))
            font.setBold(True)
            painter.setFont(font)
            metrics = painter.fontMetrics()

            pad_x = 6
            pad_y = 2
            badge_w = metrics.horizontalAdvance(label) + pad_x * 2
            badge_h = metrics.height() + pad_y * 2

            icon_w = min(self._icon_size.width(), option.rect.width())
            icon_h = min(self._icon_size.height(), option.rect.height())
            icon_left = option.rect.left() + max(0, (option.rect.width() - icon_w) // 2)
            icon_top = option.rect.top() + 3
            x = icon_left + icon_w - badge_w - 5
            y = icon_top + icon_h - badge_h - 5
            badge = QRect(int(x), int(y), int(badge_w), int(badge_h))

            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(10, 10, 14, 180))
            painter.drawRoundedRect(badge, 4, 4)
            painter.setPen(QColor(238, 238, 244, 230))
            painter.drawText(badge, int(Qt.AlignCenter), label)
        finally:
            painter.restore()
