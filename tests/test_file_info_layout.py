from pathlib import Path


def test_file_information_details_use_non_scrolling_grid_columns():
    source = Path('app/file_info_dialog.py').read_text(encoding='utf-8')
    assert 'QGridLayout' in source
    assert 'def _fill_grid' in source
    assert 'column = idx // rows' in source
    assert 'row = idx % rows' in source
    assert 'QListWidget' not in source
    assert 'QScrollArea' not in source
