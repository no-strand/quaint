from pathlib import Path


def test_thumbnail_qss_targets_virtual_listview():
    root = Path(__file__).resolve().parents[1]
    qss = (root / 'resources' / 'style.qss').read_text(encoding='utf-8')
    assert 'QListView#thumbnailPanel {' in qss
    assert 'QListView#thumbnailPanel::item {' in qss
    assert 'QListView#thumbnailPanel::item:selected {' in qss
    assert 'background-color: #101014;' in qss


def test_thumbnail_panel_has_dark_palette_fallback():
    root = Path(__file__).resolve().parents[1]
    source = (root / 'app' / 'thumbnail_panel.py').read_text(encoding='utf-8')
    assert 'QPalette.Base' in source
    assert 'self.viewport().setPalette(palette)' in source
    assert 'self.viewport().setAutoFillBackground(True)' in source
