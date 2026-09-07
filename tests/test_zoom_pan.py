import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from app.views import SinglePageView


class DummyProvider:
    def __init__(self):
        from PySide6.QtCore import QObject, Signal
        class Signals(QObject):
            pixmap_ready = Signal(int, QPixmap)
            animation_frame_ready = Signal(int, QPixmap)
            adjustments_changed = Signal()
        self.signals = Signals()
        self.pixmap_ready = self.signals.pixmap_ready
        self.animation_frame_ready = self.signals.animation_frame_ready
        self.adjustments_changed = self.signals.adjustments_changed
        self.pix = QPixmap(400, 200)
        self.pix.fill()
    def get(self, index, priority=0): return self.pix
    def preload_around(self, *a, **k): pass


def test_manual_zoom_and_reset():
    app = QApplication.instance() or QApplication([])
    view = SinglePageView(DummyProvider())
    view.resize(300, 200)
    view.show_page(0)
    view.set_zoom_percent(200)
    assert view.zoom_percent() == 200
    assert view.label.pixmap().width() == 800
    view.set_fit_mode("page")
    assert view.zoom_percent() is None
