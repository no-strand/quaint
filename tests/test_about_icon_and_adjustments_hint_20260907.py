from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
MESSAGES = (ROOT / "app" / "message_boxes.py").read_text(encoding="utf-8")
ADJUSTMENTS = (ROOT / "app" / "adjustments_dialog.py").read_text(encoding="utf-8")


def test_about_uses_quaint_resource_icon():
    assert 'icon_path = os.path.join(str(res_dir), "icon.ico")' in MESSAGES
    assert 'box.setIconPixmap(icon.pixmap(48, 48))' in MESSAGES


def test_adjustments_non_destructive_hint_is_removed():
    assert 'adjustments.hint' not in ADJUSTMENTS
    for filename in ("en_US.json", "pt_BR.json", "es_ES.json"):
        data = json.loads((ROOT / "locales" / filename).read_text(encoding="utf-8"))
        assert "adjustments.hint" not in data
