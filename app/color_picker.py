"""Seletor de cores leve que reutiliza o pixmap já exibido pelo leitor."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from app.i18n import tr


def rgb_hex(red: int, green: int, blue: int) -> str:
    """Retorna uma cor RGB no formato hexadecimal canônico."""
    return f"#{int(red) & 0xFF:02X}{int(green) & 0xFF:02X}{int(blue) & 0xFF:02X}"


class ColorPickerOverlay(QObject):
    """Seleciona uma cor da imagem por clique e permite copiar seu HEX.

    ``sampler`` segue o mesmo contrato usado pela lente de aumento: recebe uma
    posição no viewport e devolve ``(QPixmap, QPointF)`` no espaço do pixmap.

    A cor só é *fixada* quando o usuário clica. Isso evita a ambiguidade de um
    painel que muda continuamente com o cursor enquanto oferece um botão de
    cópia para uma cor aparentemente não selecionada.
    """

    copied = Signal(str)
    COPY_FEEDBACK_MS = 1600

    def __init__(self, viewport: QWidget, sampler, parent=None):
        super().__init__(parent or viewport)
        self._viewport = None
        self._sampler = None
        self._enabled = False
        self._last_hex = ""
        self._selected_payload = None
        self._cached_pixmap_key = None
        self._cached_image = None

        self.panel = QFrame(viewport)
        self.panel.setObjectName("colorPickerPanel")
        self.panel.setFrameShape(QFrame.StyledPanel)
        self.panel.setStyleSheet(
            "QFrame#colorPickerPanel { background: rgba(24,24,28,232); "
            "border: 1px solid rgba(255,255,255,80); border-radius: 8px; } "
            "QLabel { color: white; }"
        )
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        self.help_label = QLabel()
        self.help_label.setWordWrap(True)
        layout.addWidget(self.help_label)

        self.swatch = QFrame()
        self.swatch.setFixedHeight(30)
        self.swatch.setStyleSheet(
            "QFrame { background: transparent; border: 1px solid rgba(255,255,255,110); "
            "border-radius: 4px; }"
        )
        layout.addWidget(self.swatch)

        self.info_label = QLabel()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.info_label.setFont(mono)
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.info_label)

        self.feedback_label = QLabel()
        self.feedback_label.setWordWrap(True)
        self.feedback_label.hide()
        layout.addWidget(self.feedback_label)

        self.copy_button = QPushButton()
        self.copy_button.clicked.connect(self.copy_hex)
        self.copy_button.setEnabled(False)
        layout.addWidget(self.copy_button)

        self.panel.setFixedWidth(260)
        self.panel.hide()

        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.timeout.connect(self._clear_copy_feedback)

        self.retranslate_ui()
        self.set_target(viewport, sampler)

    def retranslate_ui(self):
        self.help_label.setText(tr("color_picker.help"))
        self.copy_button.setText(tr("color_picker.copy"))
        self.copy_button.setToolTip(tr("color_picker.copy_tooltip"))
        if self._selected_payload is None:
            self.info_label.setText(tr("color_picker.click_to_select"))
        else:
            self._render_payload(self._selected_payload)
        if self.feedback_label.isVisible() and self._last_hex:
            self.feedback_label.setText(tr("color_picker.copied_inline", value=self._last_hex))


    def reset_selection(self):
        """Limpa a cor fixada quando o conteúdo exibido muda."""
        self._feedback_timer.stop()
        self._selected_payload = None
        self._last_hex = ""
        self._cached_pixmap_key = None
        self._cached_image = None
        self.copy_button.setEnabled(False)
        self.copy_button.setText(tr("color_picker.copy"))
        self.feedback_label.hide()
        self.info_label.setText(tr("color_picker.click_to_select"))
        self._set_swatch(None)
        self.panel.adjustSize()
        self.panel.setFixedWidth(260)

    def set_target(self, viewport: QWidget, sampler):
        if viewport is self._viewport and sampler == self._sampler:
            self._position_panel()
            return

        old_viewport = self._viewport
        if old_viewport is not None:
            try:
                old_viewport.removeEventFilter(self)
            except RuntimeError:
                pass

        self._viewport = viewport
        self._sampler = sampler
        self._cached_pixmap_key = None
        self._cached_image = None
        self._selected_payload = None
        self._last_hex = ""
        self.copy_button.setEnabled(False)
        self.feedback_label.hide()
        self.info_label.setText(tr("color_picker.click_to_select"))
        self._set_swatch(None)

        self.panel.setParent(viewport)
        if viewport is not None:
            viewport.installEventFilter(self)
        self._position_panel()
        if self._enabled:
            self.panel.show()
            self.panel.raise_()

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        if not self._enabled:
            self._feedback_timer.stop()
            self.panel.hide()
            return
        self._position_panel()
        self.panel.show()
        self.panel.raise_()

    def is_enabled(self) -> bool:
        return self._enabled

    def _position_panel(self):
        if self._viewport is None:
            return
        margin = 12
        self.panel.adjustSize()
        self.panel.setFixedWidth(260)
        x = margin
        y = margin
        if self._viewport.width() < self.panel.width() + margin * 2:
            x = max(0, (self._viewport.width() - self.panel.width()) // 2)
        self.panel.move(x, y)

    def eventFilter(self, watched, event):
        if watched is self._viewport and self._enabled:
            event_type = event.type()
            if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                # Cliques no próprio painel devem continuar funcionando como
                # controles normais e não selecionar uma cor por trás dele.
                global_pos = event.globalPosition().toPoint()
                local = self._viewport.mapFromGlobal(global_pos)
                if self.panel.isVisible() and self.panel.geometry().contains(local):
                    return False
                self._sample(event.position().toPoint(), commit=True)
                return True
            if event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                return True
        return super().eventFilter(watched, event)

    def _sample(self, viewport_pos: QPoint, commit: bool = False):
        if self._sampler is None:
            return None
        sample = self._sampler(viewport_pos)
        if not sample:
            return None
        pixmap, point = sample
        if not isinstance(pixmap, QPixmap) or pixmap.isNull():
            return None

        key = int(pixmap.cacheKey())
        if self._cached_pixmap_key != key or self._cached_image is None:
            self._cached_image = pixmap.toImage()
            self._cached_pixmap_key = key
        image = self._cached_image
        if image is None or image.isNull() or image.width() <= 0 or image.height() <= 0:
            return None

        x = max(0, min(int(point.x()), image.width() - 1))
        y = max(0, min(int(point.y()), image.height() - 1))
        color = image.pixelColor(x, y)
        payload = (x, y, color.red(), color.green(), color.blue())

        if commit:
            self._selected_payload = payload
            self._last_hex = rgb_hex(color.red(), color.green(), color.blue())
            self.copy_button.setEnabled(True)
            self._feedback_timer.stop()
            self.feedback_label.hide()
            self.copy_button.setText(tr("color_picker.copy"))
            self._set_swatch(self._last_hex)
            self._render_payload(payload)
            self.panel.adjustSize()
            self.panel.setFixedWidth(260)
            self.panel.raise_()
        return payload

    def _set_swatch(self, hex_value: str | None):
        if not hex_value:
            self.swatch.setStyleSheet(
                "QFrame { background: transparent; border: 1px solid rgba(255,255,255,110); "
                "border-radius: 4px; }"
            )
            return
        self.swatch.setStyleSheet(
            f"QFrame {{ background: {hex_value}; border: 1px solid rgba(255,255,255,160); "
            "border-radius: 4px; }}"
        )

    def _render_payload(self, payload):
        x, y, red, green, blue = payload
        hex_value = rgb_hex(red, green, blue)
        self.info_label.setText(
            tr(
                "color_picker.selected_values",
                x=x,
                y=y,
                red=red,
                green=green,
                blue=blue,
                hex=hex_value,
            )
        )

    def copy_hex(self):
        if not self._last_hex:
            return
        QApplication.clipboard().setText(self._last_hex)
        self.copy_button.setText(tr("color_picker.copied_button"))
        self.feedback_label.setText(tr("color_picker.copied_inline", value=self._last_hex))
        self.feedback_label.show()
        self.panel.adjustSize()
        self.panel.setFixedWidth(260)
        self.copied.emit(self._last_hex)
        self._feedback_timer.start(self.COPY_FEEDBACK_MS)

    def _clear_copy_feedback(self):
        self.copy_button.setText(tr("color_picker.copy"))
        self.feedback_label.hide()
        self.panel.adjustSize()
        self.panel.setFixedWidth(260)
