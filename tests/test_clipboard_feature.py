from pathlib import Path


def test_clipboard_actions_and_temp_open_are_present():
    source = Path('app/reader_window.py').read_text(encoding='utf-8')
    assert 'def copy_current_image' in source
    assert 'def paste_from_clipboard' in source
    assert 'def copy_current_path' in source
    assert '_open_regular_archive(temp_path, add_recent=False)' in source
