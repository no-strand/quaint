"""Comparação lado a lado de duas imagens com zoom e rolagem sincronizados."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QScrollArea, QSlider, QVBoxLayout,
)

from app.i18n import tr


class ComparePane(QScrollArea):
    zoom_requested = Signal(int)

    def __init__(self, pixmap: QPixmap, title: str, parent=None):
        super().__init__(parent)
        self._source = pixmap
        self._zoom = 100
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        # Mantém apenas o QPixmap original. O QLabel escala durante a pintura,
        # em vez de alocar um novo QPixmap gigante a cada mudança de zoom.
        self.label.setPixmap(self._source)
        self.label.setScaledContents(True)
        self.setWidget(self.label)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignCenter)
        self.set_zoom(100)

    def set_zoom(self, percent):
        self._zoom = max(10, min(400, int(percent)))
        factor = self._zoom / 100.0
        self.label.resize(
            max(1, int(self._source.width() * factor)),
            max(1, int(self._source.height() * factor)),
        )

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            delta = 25 if event.angleDelta().y() > 0 else -25
            self.zoom_requested.emit(max(10, min(400, self._zoom + delta)))
            event.accept()
            return
        super().wheelEvent(event)


class CompareDialog(QDialog):
    def __init__(self, left_pixmap, right_pixmap, left_name, right_name, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("compare.title"))
        self.resize(1250, 820)
        self._syncing = False

        outer = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel(tr("compare.zoom")))
        self.zoom_slider = QSlider(Qt.Horizontal)
        self.zoom_slider.setRange(10, 400)
        self.zoom_slider.setValue(100)
        controls.addWidget(self.zoom_slider, 1)
        self.zoom_label = QLabel("100%")
        controls.addWidget(self.zoom_label)
        self.sync_box = QCheckBox(tr("compare.sync_scroll"))
        self.sync_box.setChecked(True)
        controls.addWidget(self.sync_box)
        outer.addLayout(controls)

        panes = QHBoxLayout()
        left_wrap = QVBoxLayout()
        right_wrap = QVBoxLayout()
        self.left = ComparePane(left_pixmap, left_name)
        self.right = ComparePane(right_pixmap, right_name)
        left_wrap.addWidget(self.left.title_label)
        left_wrap.addWidget(self.left, 1)
        right_wrap.addWidget(self.right.title_label)
        right_wrap.addWidget(self.right, 1)
        panes.addLayout(left_wrap, 1)
        panes.addLayout(right_wrap, 1)
        outer.addLayout(panes, 1)

        self.zoom_slider.valueChanged.connect(self._set_zoom)
        self.left.zoom_requested.connect(self.zoom_slider.setValue)
        self.right.zoom_requested.connect(self.zoom_slider.setValue)
        self._connect_scroll_sync(self.left, self.right)
        self._connect_scroll_sync(self.right, self.left)

    def _set_zoom(self, value):
        self.zoom_label.setText(f"{int(value)}%")
        self.left.set_zoom(value)
        self.right.set_zoom(value)

    @staticmethod
    def _scroll_ratio(bar):
        maximum = bar.maximum()
        return 0.0 if maximum <= 0 else bar.value() / maximum

    def _connect_scroll_sync(self, source, target):
        def sync_horizontal(_value):
            if self._syncing or not self.sync_box.isChecked():
                return
            self._syncing = True
            try:
                ratio = self._scroll_ratio(source.horizontalScrollBar())
                target.horizontalScrollBar().setValue(int(ratio * target.horizontalScrollBar().maximum()))
            finally:
                self._syncing = False

        def sync_vertical(_value):
            if self._syncing or not self.sync_box.isChecked():
                return
            self._syncing = True
            try:
                ratio = self._scroll_ratio(source.verticalScrollBar())
                target.verticalScrollBar().setValue(int(ratio * target.verticalScrollBar().maximum()))
            finally:
                self._syncing = False

        source.horizontalScrollBar().valueChanged.connect(sync_horizontal)
        source.verticalScrollBar().valueChanged.connect(sync_vertical)
