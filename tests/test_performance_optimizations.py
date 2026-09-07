import io
import os
import tempfile
import time
import zipfile
from pathlib import Path

import fitz
from PIL import Image

from app.archive import ComicArchive
from app.compressed_collection import CompressedComicCollection


def make_jpeg(size=(1800, 2600), color=(120, 80, 160)):
    bio = io.BytesIO()
    Image.new("RGB", size, color).save(bio, "JPEG", quality=88)
    return bio.getvalue()


def make_cbz(path, pages=3):
    payload = make_jpeg()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(pages):
            z.writestr(f"{i+1:03d}.jpg", payload)


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        os.environ["LOCALAPPDATA"] = str(root / "cache-root")
        cbz = root / "book.cbz"
        make_cbz(cbz)

        archive = ComicArchive(cbz)
        small = archive.load_image(0, 360)
        assert max(small.size) <= 360

        first = archive.read_bytes(1)
        second = archive.read_bytes(1)
        assert first is second
        info = archive.raw_cache_info()
        assert info["entries"] >= 1 and info["bytes"] > 0
        assert archive._zip_pool is not None
        archive.close()
        print("CBZ: decode reduzido + cache bruto + pool ZIP: OK")

        # Pasta grande: nomes são strings leves e índice persistente é gravado
        # fora da thread principal para reaberturas rápidas.
        folder = root / "huge-folder"
        folder.mkdir()
        for i in range(2000):
            (folder / f"{i+1:06d}.jpg").write_bytes(b"x")
        a = ComicArchive(folder)
        assert a.count() == 2000
        assert isinstance(a.pages[0], str)
        a.close()
        time.sleep(1.15)
        b = ComicArchive(folder)
        assert b.count() == 2000 and b.pages[0] == "000001.jpg"
        b.close()
        print("Pastas grandes: scandir + índice reutilizável: OK")

        pdf = root / "book.pdf"
        doc = fitz.open()
        page = doc.new_page(width=800, height=1200)
        page.insert_text((50, 80), "Performance")
        doc.save(pdf)
        doc.close()
        archive = ComicArchive(pdf)
        small_pdf = archive.load_image(0, 420)
        assert max(small_pdf.size) <= 420
        assert isinstance(archive.pages, range)
        archive.close()
        print("PDF: rasterização alvo + range leve: OK")

        c1 = root / "c1.cbz"; c2 = root / "c2.cbz"
        make_cbz(c1, 1); make_cbz(c2, 1)
        outer = root / "collection.zip"
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(c1, "serie/c1.cbz")
            z.write(c2, "serie/c2.cbz")
        collection = CompressedComicCollection(outer)
        assert collection.materialized_count() == 0
        assert collection.member_path(0).is_file()
        assert collection.materialized_count() == 1
        collection.close()
        print("Coleções: extração sob demanda: OK")

        # Coleção com muitas imagens: indexação não extrai nada e gerar capas
        # visíveis também não deixa milhares de temporários para ZIP.
        images_outer = root / "many-images.zip"
        thumb = make_jpeg(size=(120, 180))
        with zipfile.ZipFile(images_outer, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(500):
                z.writestr(f"imgs/{i+1:05d}.jpg", thumb)
        collection = CompressedComicCollection(images_outer)
        assert collection.count() == 500
        assert collection.materialized_count() == 0
        for i in (0, 250, 499):
            cover = collection.load_member_cover(i, 80)
            assert max(cover.size) <= 80
        assert collection.materialized_count() == 0
        # Ao navegar, só a imagem ativa precisa permanecer extraída.
        first = collection.member_path(0)
        collection.set_active_member(0)
        assert first.exists()
        second = collection.member_path(1)
        collection.set_active_member(1)
        collection.discard_member(0)
        assert second.exists() and not first.exists()
        collection.close()
        print("Coleções com muitas imagens: lazy + temporário limitado: OK")

    project = Path(__file__).resolve().parents[1]
    provider = (project / "app" / "pixmap_provider.py").read_text(encoding="utf-8")
    thumbs = (project / "app" / "thumbnail_panel.py").read_text(encoding="utf-8")
    views = (project / "app" / "views.py").read_text(encoding="utf-8")
    archive_src = (project / "app" / "archive.py").read_text(encoding="utf-8")
    summary = (project / "app" / "summary_dialog.py").read_text(encoding="utf-8")

    assert "self.archive.load_image(self.index, self.max_dim)" in provider
    assert "self.prefetch_pool = QThreadPool(self)" in provider
    assert "foreground=True" in provider
    assert "tryTake(task)" in provider
    assert "max_cache_bytes=96 * 1024 * 1024" in provider
    assert "def preload_around" in provider

    assert "class _ThumbnailModel(QAbstractListModel)" in thumbs
    assert "QListView.Batched" in thumbs
    assert "def _request_visible(self):" in thumbs
    assert "workers=2" in thumbs
    assert "QListWidgetItem(" not in thumbs

    assert "VIRTUAL_THRESHOLD = 240" in views
    assert "WINDOW_PAGES = 36" in views
    assert "def _build_window" in views

    assert "with os.scandir(p) as entries" in archive_src
    assert "load_index(p, \"dir\")" in archive_src
    assert "_ZipReaderPool" in archive_src
    read_body = archive_src.split("def read_bytes(self, index: int)", 1)[1].split("def page_name", 1)[0]
    assert "with zipfile.ZipFile(self.path" not in read_body
    assert "self._zip_pool.read(entry)" in read_body

    reader = (project / "app" / "reader_window.py").read_text(encoding="utf-8")
    assert "self.thumb_panel.prepare(scope)" in reader
    assert "if view.total != self.archive.count():" in reader

    assert "setMaxThreadCount(2)" in summary
    assert "def _request_visible_covers" in summary
    assert "load_member_cover" in summary

    collection_src = (project / "app" / "compressed_collection.py").read_text(encoding="utf-8")
    assert "IMAGE_MEMBER_EXTS" in collection_src
    assert "_ZipReaderPool" in collection_src
    assert "def discard_member" in collection_src
    print("Otimizações extremas de grandes coleções: OK")


if __name__ == "__main__":
    main()
