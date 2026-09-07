from pathlib import Path


def test_shortcuts_dialog_has_roomy_cells_and_editors():
    src = Path("app/shortcuts_dialog.py").read_text(encoding="utf-8")
    assert "padding: 7px 12px;" in src
    assert "setDefaultSectionSize(42)" in src
    assert "setColumnWidth(1, 180)" in src
    assert "edit.setMinimumHeight(32)" in src
    assert "edit.setMinimumWidth(150)" in src
