from pathlib import Path


def test_file_operations_are_safe_and_use_trash():
    source = Path('app/reader_window.py').read_text(encoding='utf-8')
    for name in ('reveal_current_file', 'rename_current_file', 'move_current_file', 'copy_current_file_to', 'delete_current_file'):
        assert f'def {name}' in source
    assert 'send2trash(str(source))' in source
    assert 'Path(name).suffix.lower() != source.suffix.lower()' in source
