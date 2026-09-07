from PIL import Image
from app.file_watcher_utils import folder_snapshot


def test_folder_snapshot_tracks_supported_changes(tmp_path):
    image = tmp_path / 'a.png'
    Image.new('RGB', (2, 2), 'red').save(image)
    ignored = tmp_path / 'notes.xyz'
    ignored.write_text('x')
    first = folder_snapshot(tmp_path)
    assert 'a.png' in first
    assert 'notes.xyz' not in first
    Image.new('RGB', (3, 3), 'blue').save(image)
    second = folder_snapshot(tmp_path)
    assert second['a.png'] != first['a.png']
