import io
import zipfile
from pathlib import Path

from PIL import Image

from app.compressed_collection import (
    CollectionImageGroupArchive,
    CompressedComicCollection,
    ContainerImageArchive,
    DirectoryComicCollection,
)


def _png_bytes(color):
    bio = io.BytesIO()
    Image.new('RGB', (48, 64), color).save(bio, 'PNG')
    return bio.getvalue()


def _make_cbz(path: Path, count=3):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for i in range(count):
            z.writestr(f'{i+1:03d}.png', _png_bytes((20*i, 10, 30)))


def _make_image_zip(path: Path, count=4):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for i in range(count):
            z.writestr(f'img_{i+1:03d}.png', _png_bytes((10, 20*i, 30)))


def test_directory_mixed_loose_images_have_own_thumbnail_scope(tmp_path):
    (tmp_path / '001.png').write_bytes(_png_bytes((255, 0, 0)))
    _make_cbz(tmp_path / '002.cbz', 5)
    (tmp_path / '003.png').write_bytes(_png_bytes((0, 255, 0)))
    _make_image_zip(tmp_path / '004.zip', 4)

    collection = DirectoryComicCollection(tmp_path)
    assert [collection.member_kind(i) for i in range(collection.count())] == [
        'image', 'comic', 'image', 'archive'
    ]

    group = CollectionImageGroupArchive(collection)
    assert group.member_indices == [0, 2]
    assert group.count() == 2
    assert group.page_name(0) == '001.png'
    assert group.page_name(1) == '003.png'
    assert group.load_image(1, 100).size[1] <= 100

    comic = __import__('app.archive', fromlist=['ComicArchive']).ComicArchive(
        collection.member_path(1)
    )
    try:
        assert comic.count() == 5
    finally:
        comic.close()

    nested = ContainerImageArchive(collection.member_path(3))
    try:
        assert nested.count() == 4
        assert nested.page_name(0) == 'img_001.png'
    finally:
        nested.close()


def test_outer_zip_mixed_loose_images_scope_and_internal_archives(tmp_path):
    cbz_path = tmp_path / 'chapter.cbz'
    _make_cbz(cbz_path, 3)
    nested_zip = tmp_path / 'gallery.zip'
    _make_image_zip(nested_zip, 6)

    outer = tmp_path / 'mixed.zip'
    with zipfile.ZipFile(outer, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('001.png', _png_bytes((1, 2, 3)))
        z.write(cbz_path, '002.cbz')
        z.writestr('003.gif', _png_bytes((4, 5, 6)))
        z.write(nested_zip, '004.zip')

    collection = CompressedComicCollection(outer)
    try:
        assert [collection.member_kind(i) for i in range(collection.count())] == [
            'image', 'comic', 'image', 'archive'
        ]
        group = CollectionImageGroupArchive(collection)
        assert group.member_indices == [0, 2]
        assert group.count() == 2

        # CBZ continua expondo suas próprias páginas.
        comic = __import__('app.archive', fromlist=['ComicArchive']).ComicArchive(
            collection.member_path(1)
        )
        try:
            assert comic.count() == 3
        finally:
            comic.close()

        # ZIP interno expõe todas as imagens dele como páginas internas.
        nested = ContainerImageArchive(collection.member_path(3))
        try:
            assert nested.count() == 6
        finally:
            nested.close()
    finally:
        collection.close()
