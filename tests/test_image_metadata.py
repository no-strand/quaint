from pathlib import Path
from PIL import Image
from app.archive import ComicArchive
from app.image_metadata import extract_image_metadata


def test_extracts_dimensions_dpi_and_exif(tmp_path):
    path = tmp_path / 'photo.jpg'
    img = Image.new('RGB', (320, 240), 'red')
    exif = Image.Exif()
    exif[271] = 'OpenAI Camera'
    exif[272] = 'Model X'
    exif[36867] = '2026:09:07 10:00:00'
    img.save(path, exif=exif, dpi=(96, 96))
    archive = ComicArchive(path)
    data = dict(extract_image_metadata(archive, 0))
    archive.close()
    assert data['image_meta.dimensions'] == '320 × 240 px'
    assert 'image_meta.dpi' in data
    assert data['image_meta.camera_make'] == 'OpenAI Camera'
    assert data['image_meta.camera_model'] == 'Model X'
    assert data['image_meta.date'] == '2026:09:07 10:00:00'
