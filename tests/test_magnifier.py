"""Regression checks for the circular magnifier feature."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    reader = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
    views = (ROOT / "app" / "views.py").read_text(encoding="utf-8")
    magnifier = (ROOT / "app" / "magnifier.py").read_text(encoding="utf-8")

    # Tools action, shortcut and dynamic localization.
    assert 'self.act_magnifier = QAction(tr("action.magnifier"), self)' in reader
    assert 'self.act_magnifier.setShortcut(QKeySequence("M"))' in reader
    assert 'self.menu_tools.addAction(self.act_magnifier)' in reader
    assert 'self.act_magnifier: "action.magnifier"' in reader

    # The overlay must reuse the current visual pixmap and never access an archive/provider.
    assert "class MagnifierOverlay" in magnifier
    assert "QCursor.pos()" in magnifier
    assert "drawPixmap" in magnifier
    assert "archive." not in magnifier
    assert "provider." not in magnifier
    assert "DIAMETER = 360" in magnifier
    assert "ZOOM = 3.25" in magnifier

    # All three image views expose a sampling hook. Continuous mode keeps its
    # O(1) child lookup rather than scanning a huge page list on every tick.
    assert views.count("def magnifier_sample(self, viewport_pos):") == 3
    assert "self.container.childAt(container_pos)" in views

    catalogs = {
        code: json.loads((ROOT / "locales" / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("en_US", "pt_BR", "es_ES")
    }
    assert catalogs["en_US"]["action.magnifier"] == "Magnifier"
    assert catalogs["pt_BR"]["action.magnifier"] == "Lente de aumento"
    assert catalogs["es_ES"]["action.magnifier"] == "Lupa"
    for data in catalogs.values():
        assert "status.magnifier_enabled" in data
        assert "status.magnifier_disabled" in data

    print("Lente de aumento circular + atalho M + i18n: OK")


if __name__ == "__main__":
    main()
