from pathlib import Path
import json


def test_slideshow_has_explicit_stop_shortcut():
    source = Path('app/reader_window.py').read_text(encoding='utf-8')
    assert 'self.act_slideshow_stop.setShortcut(QKeySequence("Ctrl+F5"))' in source


def test_image_only_mode_hides_and_restores_main_chrome():
    source = Path('app/reader_window.py').read_text(encoding='utf-8')
    assert 'self.act_image_only.setShortcut(QKeySequence("Tab"))' in source
    assert 'def toggle_interface_hidden' in source
    assert 'self.menuBar().hide()' in source
    assert 'self.bottom_bar.hide()' in source
    assert 'self.statusBar().hide()' in source
    assert 'self.thumb_dock.hide()' in source
    assert 'self.menuBar().show()' in source
    assert 'self.bottom_bar.show()' in source
    assert 'self.statusBar().show()' in source


def test_image_only_mode_is_localized():
    for name in ('en_US.json', 'pt_BR.json', 'es_ES.json'):
        data = json.loads((Path('locales') / name).read_text(encoding='utf-8'))
        assert data['action.image_only_mode']
