"""Diálogo dos ajustes de exibição das páginas/imagens."""
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QSlider, QPushButton,
    QGroupBox, QScrollArea, QWidget, QFrame, QComboBox,
)

from app.image_effects import (
    ADJUSTMENT_KEYS,
    DEFAULT_ADJUSTMENTS,
    FILTERS,
    adjustments_dict,
    normalize_filter_name,
)
from app.i18n import tr

SLIDER_SCALE = 100

# Chaves visuais mantidas explicitamente aqui para facilitar auditoria de UI:
# tr("adjustments.gamma"), tr("adjustments.hue"), tr("adjustments.whites"),
# tr("adjustments.blacks"), tr("adjustments.sharpness"), tr("adjustments.auto_levels")


class _AdjustRow(QHBoxLayout):
    def __init__(
        self,
        title,
        min_value,
        max_value,
        initial,
        on_change,
        neutral_factor=False,
        display_mode=None,
    ):
        super().__init__()
        self.on_change = on_change
        self.neutral_factor = neutral_factor
        self.display_mode = display_mode

        self.label = QLabel(title)
        self.label.setMinimumWidth(122)
        self.label.setProperty("adjustmentLabel", True)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(int(round(min_value * SLIDER_SCALE)))
        self.slider.setMaximum(int(round(max_value * SLIDER_SCALE)))
        self.slider.setValue(int(round(initial * SLIDER_SCALE)))
        self.slider.setSingleStep(1)
        self.slider.setPageStep(5)

        self.value_label = QLabel()
        self.value_label.setMinimumWidth(68)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setProperty("adjustmentValue", True)
        self._update_label(initial)

        self.slider.valueChanged.connect(self._on_slider_changed)
        self.addWidget(self.label)
        self.addWidget(self.slider, 1)
        self.addWidget(self.value_label)

    def _format(self, value):
        if self.display_mode == "gamma":
            return f"{value:.2f}×"
        if self.display_mode == "degrees":
            degrees = int(round(value * 180.0))
            return f"{degrees:+d}°" if degrees else "0°"
        if self.display_mode == "percent":
            return f"{int(round(value * 100))}%"
        if self.display_mode == "byte":
            return str(int(round(max(0.0, min(1.0, value)) * 255.0)))
        if self.neutral_factor:
            return f"{int(round(value * 100))}%"
        return f"{int(round(value * 100)):+d}%" if value else "0%"

    def _update_label(self, value):
        self.value_label.setText(self._format(value))

    def _on_slider_changed(self, raw):
        value = raw / SLIDER_SCALE
        self._update_label(value)
        self.on_change(value)

    def set_value(self, value):
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(float(value) * SLIDER_SCALE)))
        self.slider.blockSignals(False)
        self._update_label(float(value))

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        self.label.setEnabled(enabled)
        self.slider.setEnabled(enabled)
        self.value_label.setEnabled(enabled)


class AdjustmentsDialog(QDialog):
    """Ajustes não destrutivos aplicados em tempo real às páginas de imagem."""

    adjustments_changed = Signal(*([float] * len(ADJUSTMENT_KEYS)))
    filter_changed = Signal(str)

    def __init__(self, initial, parent=None, filter_name="none"):
        super().__init__(parent)
        self.setWindowTitle(tr("adjustments.title"))
        self.setModal(False)
        self.resize(600, 760)
        self.setMinimumWidth(530)
        self.setObjectName("adjustmentsDialog")

        self.setStyleSheet("""
            QDialog#adjustmentsDialog { background-color: #1c1c24; color: #f2f2f6; }
            QDialog#adjustmentsDialog QScrollArea { background-color: #1c1c24; border: none; }
            QDialog#adjustmentsDialog QScrollArea > QWidget > QWidget,
            QDialog#adjustmentsDialog QWidget#adjustmentsContent { background-color: #1c1c24; }
            QDialog#adjustmentsDialog QGroupBox {
                background-color: #202029; color: #f2f2f6;
                border: 1px solid #3a3a47; border-radius: 8px;
                margin-top: 10px; padding-top: 12px; font-weight: 600;
            }
            QDialog#adjustmentsDialog QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #f2f2f6;
            }
            QDialog#adjustmentsDialog QLabel { color: #f2f2f6; background-color: transparent; }
            QDialog#adjustmentsDialog QLabel[adjustmentValue="true"] { color: #d8d8e2; font-weight: 600; }
            QDialog#adjustmentsDialog QPushButton {
                color: #f2f2f6; background-color: #2b2b35; border: 1px solid #3a3a47;
                border-radius: 5px; padding: 6px 10px;
            }
            QDialog#adjustmentsDialog QPushButton:hover { background-color: #383845; }
            QDialog#adjustmentsDialog QPushButton:checked { background-color: #394c6d; border-color: #5d82bd; }
            QDialog#adjustmentsDialog QComboBox {
                color: #f2f2f6; background-color: #2b2b35; border: 1px solid #3a3a47;
                border-radius: 5px; padding: 5px 8px;
            }
            QDialog#adjustmentsDialog QComboBox QAbstractItemView {
                color: #f2f2f6; background-color: #25252e; selection-background-color: #394c6d;
            }
        """)

        self._current = adjustments_dict(initial)
        self._filter_name = normalize_filter_name(filter_name)
        self._rows = {}
        self._toggle_buttons = {}
        self._toggle_labels = {}

        self._emit_timer = QTimer(self)
        self._emit_timer.setSingleShot(True)
        self._emit_timer.setInterval(45)
        self._emit_timer.timeout.connect(self._emit_current_values)

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setObjectName("adjustmentsScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("adjustmentsContent")
        content_layout = QVBoxLayout(content)

        # ------------------------------------------------------------- Luz
        self.box_light, light = self._make_group("adjustments.group_light")
        self.row_brightness = self._add_row(light, "adjustments.brightness", "brightness", 0.50, 1.50, neutral_factor=True)
        self.row_exposure = self._add_row(light, "adjustments.exposure", "exposure", -1.00, 1.00)
        self.row_contrast = self._add_row(light, "adjustments.contrast", "contrast", 0.50, 1.50, neutral_factor=True)
        self.row_gamma = self._add_row(light, "adjustments.gamma", "gamma", 0.20, 3.00, display_mode="gamma")
        self.row_highlights = self._add_row(light, "adjustments.highlights", "highlights", -1.00, 1.00)
        self.row_shadows = self._add_row(light, "adjustments.shadows", "shadows", -1.00, 1.00)
        self.row_whites = self._add_row(light, "adjustments.whites", "whites", -1.00, 1.00)
        self.row_blacks = self._add_row(light, "adjustments.blacks", "blacks", -1.00, 1.00)
        self._add_toggle(light, "auto_levels", "adjustments.auto_levels")
        # Mantém os nomes históricos para integrações/testes existentes.
        self.auto_levels_label = self._toggle_labels["auto_levels"]
        self.btn_auto_levels = self._toggle_buttons["auto_levels"]
        self.btn_auto_levels.setCheckable(True)
        self._add_toggle(light, "auto_contrast", "adjustments.auto_contrast")

        # ------------------------------------------------------------- Cor
        self.box_color, color = self._make_group("adjustments.group_color")
        self.row_saturation = self._add_row(color, "adjustments.saturation", "saturation", 0.00, 2.00, neutral_factor=True)
        self.row_vibrance = self._add_row(color, "adjustments.vibrance", "vibrance", -1.00, 1.00)
        self.row_hue = self._add_row(color, "adjustments.hue", "hue", -1.00, 1.00, display_mode="degrees")
        self.row_warmth = self._add_row(color, "adjustments.warmth", "warmth", -1.00, 1.00)
        self.row_tint = self._add_row(color, "adjustments.tint", "tint", -1.00, 1.00)
        self.row_cb_shadows = self._add_row(color, "adjustments.color_balance_shadows", "color_balance_shadows", -1.00, 1.00)
        self.row_cb_midtones = self._add_row(color, "adjustments.color_balance_midtones", "color_balance_midtones", -1.00, 1.00)
        self.row_cb_highlights = self._add_row(color, "adjustments.color_balance_highlights", "color_balance_highlights", -1.00, 1.00)
        self._add_toggle(color, "auto_white_balance", "adjustments.auto_white_balance")
        self._add_toggle(color, "auto_enhance", "adjustments.auto_enhance")

        # ---------------------------------------------------------- Detalhes
        self.box_details, details = self._make_group("adjustments.group_details")
        self.row_sharpness = self._add_row(details, "adjustments.sharpness", "sharpness", 0.00, 2.00, display_mode="percent")
        self.row_clarity = self._add_row(details, "adjustments.clarity", "clarity", -1.00, 1.00)
        self.row_dehaze = self._add_row(details, "adjustments.dehaze", "dehaze", -1.00, 1.00)
        self.row_noise = self._add_row(details, "adjustments.noise_reduction", "noise_reduction", 0.00, 1.00, display_mode="percent")
        self.row_blur = self._add_row(details, "adjustments.blur", "blur", 0.00, 1.00, display_mode="percent")

        # ------------------------------------------------------------ Efeitos
        self.box_effects, effects = self._make_group("adjustments.group_effects")
        self.row_vignette = self._add_row(effects, "adjustments.vignette", "vignette", 0.00, 1.00, display_mode="percent")
        self.row_grain = self._add_row(effects, "adjustments.grain", "grain", 0.00, 1.00, display_mode="percent")
        self.row_fade = self._add_row(effects, "adjustments.fade", "fade", 0.00, 1.00, display_mode="percent")

        self.filter_box = QGroupBox(tr("adjustments.filter_group"))
        filter_layout = QHBoxLayout(self.filter_box)
        self.filter_label = QLabel(tr("adjustments.filter"))
        self.filter_combo = QComboBox()
        self._populate_filters()
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_layout.addWidget(self.filter_label)
        filter_layout.addWidget(self.filter_combo, 1)

        # --------------------------------------------------------------- Scan
        self.box_scan, scan = self._make_group("adjustments.group_scan")
        self.row_whiten_background = self._add_row(scan, "adjustments.whiten_background", "whiten_background", 0.00, 1.00, display_mode="percent")
        self.row_darken_lines = self._add_row(scan, "adjustments.darken_lines", "darken_lines", 0.00, 1.00, display_mode="percent")
        self.row_moire = self._add_row(scan, "adjustments.moire_reduction", "moire_reduction", 0.00, 1.00, display_mode="percent")
        self._add_toggle(scan, "scan_cleanup", "adjustments.scan_cleanup")
        self._add_toggle(scan, "threshold_enabled", "adjustments.threshold")
        self.row_threshold = self._add_row(scan, "adjustments.threshold_level", "threshold_level", 0.00, 1.00, display_mode="byte")
        self.row_threshold.set_enabled(self._current["threshold_enabled"] >= 0.5)

        # ------------------------------------------------------------ Avançado
        self.box_advanced, advanced = self._make_group("adjustments.group_advanced")
        self.row_curve_shadows = self._add_row(advanced, "adjustments.curve_shadows", "curve_shadows", -1.00, 1.00)
        self.row_curve_midtones = self._add_row(advanced, "adjustments.curve_midtones", "curve_midtones", -1.00, 1.00)
        self.row_curve_highlights = self._add_row(advanced, "adjustments.curve_highlights", "curve_highlights", -1.00, 1.00)

        for widget in (
            self.box_light, self.box_color, self.box_details, self.box_effects,
            self.filter_box, self.box_scan, self.box_advanced,
        ):
            content_layout.addWidget(widget)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        self.btn_reset = QPushButton(tr("adjustments.reset"))
        self.btn_reset.clicked.connect(self.reset_defaults)
        self.btn_close = QPushButton(tr("common.close"))
        self.btn_close.clicked.connect(self.close)
        btn_row.addWidget(self.btn_reset)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_close)
        outer.addLayout(btn_row)

    def _make_group(self, title_key):
        box = QGroupBox(tr(title_key))
        box.setProperty("titleKey", title_key)
        layout = QVBoxLayout(box)
        return box, layout

    def _add_row(self, layout, text_key, key, min_value, max_value, neutral_factor=False, display_mode=None):
        row = _AdjustRow(
            tr(text_key), min_value, max_value, self._current[key],
            lambda value, k=key: self._queue_change(k, value),
            neutral_factor=neutral_factor, display_mode=display_mode,
        )
        row.text_key = text_key
        layout.addLayout(row)
        self._rows[key] = row
        return row

    def _add_toggle(self, layout, key, text_key):
        line = QHBoxLayout()
        label = QLabel(tr(text_key))
        label.setMinimumWidth(122)
        button = QPushButton()
        button.setCheckable(True)
        button.setChecked(self._current[key] >= 0.5)
        button.toggled.connect(lambda checked, k=key: self._on_toggle(k, checked))
        line.addWidget(label)
        line.addStretch(1)
        line.addWidget(button)
        layout.addLayout(line)
        self._toggle_labels[key] = label
        self._toggle_buttons[key] = button
        label.setProperty("textKey", text_key)
        self._refresh_toggle_button(key)
        return button

    def _queue_change(self, key, value):
        self._current[key] = float(value)
        self._emit_timer.start()

    def _emit_current_values(self):
        self.adjustments_changed.emit(*(self._current[k] for k in ADJUSTMENT_KEYS))

    def _on_toggle(self, key, checked):
        self._current[key] = 1.0 if checked else 0.0
        self._refresh_toggle_button(key)
        if key == "threshold_enabled":
            self.row_threshold.set_enabled(bool(checked))
        self._emit_timer.stop()
        self._emit_current_values()

    def _on_auto_levels_toggled(self, checked):
        # Mantido como método público/compatível com a implementação anterior.
        self._on_toggle("auto_levels", checked)

    def _refresh_toggle_button(self, key):
        button = self._toggle_buttons.get(key)
        if button is None:
            return
        button.setText(tr("adjustments.enabled") if button.isChecked() else tr("adjustments.disabled"))

    def _refresh_auto_levels_button(self):
        self._refresh_toggle_button("auto_levels")

    def _populate_filters(self):
        current = getattr(self, "_filter_name", "none")
        combo = getattr(self, "filter_combo", None)
        if combo is None:
            return
        combo.blockSignals(True)
        combo.clear()
        for name in FILTERS:
            combo.addItem(tr(f"filter_name.{name}"), name)
        idx = combo.findData(current)
        combo.setCurrentIndex(max(0, idx))
        combo.blockSignals(False)

    def _on_filter_changed(self, _index):
        name = normalize_filter_name(self.filter_combo.currentData())
        self._filter_name = name
        self.filter_changed.emit(name)

    def set_filter(self, name):
        self._filter_name = normalize_filter_name(name)
        self._populate_filters()

    def set_values(self, values):
        self._current = adjustments_dict(values)
        for key, row in self._rows.items():
            row.set_value(self._current[key])
        for key, button in self._toggle_buttons.items():
            button.blockSignals(True)
            button.setChecked(self._current[key] >= 0.5)
            button.blockSignals(False)
            self._refresh_toggle_button(key)
        self.row_threshold.set_enabled(self._current["threshold_enabled"] >= 0.5)

    def reset_defaults(self):
        self._emit_timer.stop()
        self.set_values(DEFAULT_ADJUSTMENTS)
        self.set_filter("none")
        self.filter_changed.emit("none")
        self._emit_current_values()

    def retranslate_ui(self):
        self.setWindowTitle(tr("adjustments.title"))
        for box in (
            self.box_light, self.box_color, self.box_details,
            self.box_effects, self.box_scan, self.box_advanced,
        ):
            box.setTitle(tr(box.property("titleKey")))
        self.filter_box.setTitle(tr("adjustments.filter_group"))
        self.filter_label.setText(tr("adjustments.filter"))
        self._populate_filters()
        for row in self._rows.values():
            row.label.setText(tr(row.text_key))
        for key, label in self._toggle_labels.items():
            label.setText(tr(label.property("textKey")))
            self._refresh_toggle_button(key)
        self.btn_reset.setText(tr("adjustments.reset"))
        self.btn_close.setText(tr("common.close"))
