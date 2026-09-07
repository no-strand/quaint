import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_color_picker_integrated_and_localized():
    reader = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
    picker = (ROOT / "app" / "color_picker.py").read_text(encoding="utf-8")

    assert 'self.act_color_picker = QAction(tr("action.color_picker"), self)' in reader
    assert 'self.act_color_picker.setShortcut(QKeySequence("P"))' in reader
    assert 'self.menu_tools.addAction(self.act_color_picker)' in reader
    assert 'ColorPickerOverlay(' in reader
    assert 'view.magnifier_sample' in reader
    assert 'pixelColor(x, y)' in picker
    assert 'QApplication.clipboard().setText(self._last_hex)' in picker

    # A seleção agora é explícita por clique e permanece fixa até outro clique.
    assert 'viewport.installEventFilter(self)' in picker
    assert 'QEvent.MouseButtonPress' in picker
    assert 'commit=True' in picker
    assert 'self._selected_payload = payload' in picker
    assert 'self.copy_button.setEnabled(False)' in picker
    assert 'self.copy_button.setText(tr("color_picker.copied_button"))' in picker
    assert 'self.feedback_label.show()' in picker

    for code in ("en_US", "pt_BR", "es_ES"):
        data = json.loads((ROOT / "locales" / f"{code}.json").read_text(encoding="utf-8"))
        assert "action.color_picker" in data
        assert "color_picker.selected_values" in data
        assert "color_picker.copy" in data
        assert "color_picker.copied_button" in data
        assert "color_picker.copied_inline" in data
        assert "status.color_picker_copied" in data
