import io
import tempfile
import zipfile
import tarfile
from pathlib import Path

from PIL import Image
import fitz

from app.archive import ComicArchive, ArchiveError
from app.compressed_collection import CompressedComicCollection, is_container_path


def image_bytes(fmt="PNG", color=(200, 100, 50), size=(64, 96)):
    bio = io.BytesIO()
    Image.new("RGB", size, color).save(bio, format=fmt)
    return bio.getvalue()


def make_cbz(path, names):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(name, image_bytes())


def make_epub(path, text=False, image_only_pages=1):
    container = '''<?xml version="1.0"?>
    <container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
      <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
    </container>'''
    manifest_items = []
    spine = []
    docs = {}
    for i in range(image_only_pages):
        img = f"images/p{i+1}.png"
        xhtml = f"p{i+1}.xhtml"
        manifest_items.append(f'<item id="p{i+1}" href="{xhtml}" media-type="application/xhtml+xml"/>')
        manifest_items.append(f'<item id="img{i+1}" href="{img}" media-type="image/png"/>')
        spine.append(f'<itemref idref="p{i+1}"/>')
        docs[xhtml] = f'<html xmlns="http://www.w3.org/1999/xhtml"><body><img src="{img}"/></body></html>'
    if text:
        manifest_items.append('<item id="chap" href="chapter.xhtml" media-type="application/xhtml+xml"/>')
        manifest_items.append('<item id="ill" href="images/ill.png" media-type="image/png"/>')
        spine.append('<itemref idref="chap"/>')
        docs["chapter.xhtml"] = '<html><body><h1>Capítulo</h1><p>Este é um parágrafo de texto suficientemente longo para leitura.</p><img src="images/ill.png"/></body></html>'
    opf = f'''<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id"><manifest>{''.join(manifest_items)}</manifest><spine toc="ncx">{''.join(spine)}</spine></package>'''
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        for i in range(image_only_pages):
            z.writestr(f"OEBPS/images/p{i+1}.png", image_bytes())
        if text:
            z.writestr("OEBPS/images/ill.png", image_bytes())
        for name, data in docs.items():
            z.writestr("OEBPS/" + name, data)


def run():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        cbz = root / "book.cbz"
        make_cbz(cbz, ["10.png", "2.png", "1.png"])
        a = ComicArchive(cbz)
        assert a.kind == "zip" and a.count() == 3
        assert a.page_name(0) == "1.png" and Image.open(io.BytesIO(a.read_bytes(0))).size == (64, 96)
        a.close()
        print("CBZ: OK")

        outer = root / "collection.zip"
        b1 = root / "cap1.cbz"; b2 = root / "cap2.cbz"
        make_cbz(b1, ["1.png", "2.png"]); make_cbz(b2, ["1.png"])
        with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(b2, "manga/cap2.cbz")
            z.write(b1, "manga/cap1.cbz")
        try:
            ComicArchive(outer)
        except ArchiveError:
            pass
        else:
            raise AssertionError("ZIP externo não deve mais ser achatado como um único quadrinho")

        collection = CompressedComicCollection(outer)
        assert collection.count() == 2
        assert collection.member_name(0) == "manga/cap1.cbz"
        assert collection.member_name(1) == "manga/cap2.cbz"
        first = ComicArchive(collection.member_path(0))
        second = ComicArchive(collection.member_path(1))
        assert first.count() == 2 and second.count() == 1
        Image.open(io.BytesIO(first.read_bytes(0))).verify()
        Image.open(io.BytesIO(second.read_bytes(0))).verify()
        first.close(); second.close(); collection.close()
        print("ZIP como pasta virtual (2 CBZ independentes): OK")

        # ZIP somente com imagens: deve abrir como coleção, sem exigir CBZ/CBR.
        images_zip = root / "images-only.zip"
        with zipfile.ZipFile(images_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("galeria/10.jpg", image_bytes("JPEG", color=(20, 40, 80)))
            z.writestr("galeria/2.png", image_bytes("PNG", color=(80, 40, 20)))
            z.writestr("galeria/1.webp", image_bytes("WEBP", color=(40, 80, 20)))
            z.writestr("notas/readme.txt", "ignorar")
        collection = CompressedComicCollection(images_zip)
        assert collection.count() == 3
        assert [collection.member_name(i) for i in range(3)] == [
            "galeria/1.webp", "galeria/2.png", "galeria/10.jpg"
        ]
        assert all(collection.member_kind(i) == "image" for i in range(3))
        assert collection.materialized_count() == 0
        # Capa de imagem deve ser lida lazy, sem deixar o arquivo extraído.
        cover = collection.load_member_cover(0, 80)
        assert max(cover.size) <= 80 and collection.materialized_count() == 0
        image_archive = ComicArchive(collection.member_path(1))
        assert image_archive.kind == "image" and image_archive.count() == 1
        Image.open(io.BytesIO(image_archive.read_bytes(0))).verify()
        image_archive.close()
        collection.close()
        print("ZIP somente imagens: OK")

        # Coleção mista: CBZ/CBR e imagens aparecem juntos no mesmo Sumário.
        mixed_zip = root / "mixed.zip"
        with zipfile.ZipFile(mixed_zip, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(b1, "serie/01.cbz")
            z.writestr("serie/02.jpg", image_bytes("JPEG"))
            z.write(b2, "serie/03.cbr")
            z.writestr("serie/04.gif", image_bytes("GIF"))
        collection = CompressedComicCollection(mixed_zip)
        assert collection.count() == 4
        assert [collection.member_kind(i) for i in range(4)] == [
            "comic", "image", "comic", "image"
        ]
        assert [collection.member_name(i) for i in range(4)] == [
            "serie/01.cbz", "serie/02.jpg", "serie/03.cbr", "serie/04.gif"
        ]
        collection.close()
        print("ZIP misto CBZ/CBR + imagens: OK")

        # Detecção dos formatos externos aceitos como coleção.
        for name in ("x.zip", "x.rar", "x.7z", "x.tar", "x.tgz", "x.tar.gz", "x.tar.bz2", "x.tar.xz"):
            assert is_container_path(name), name
        print("Detecção ZIP/RAR/7Z/TAR: OK")

        # 7Z é testado de verdade quando py7zr está disponível no ambiente.
        try:
            import py7zr
        except ImportError:
            print("7Z: teste funcional ignorado (py7zr não instalado neste ambiente)")
        else:
            outer_7z = root / "collection.7z"
            with py7zr.SevenZipFile(outer_7z, "w") as z:
                z.write(b2, arcname="manga/cap2.cbz")
                z.write(b1, arcname="manga/cap1.cbz")
            collection = CompressedComicCollection(outer_7z)
            assert collection.count() == 2
            first = ComicArchive(collection.member_path(0))
            second = ComicArchive(collection.member_path(1))
            assert first.count() == 2 and second.count() == 1
            first.close(); second.close(); collection.close()
            print("7Z como pasta virtual: OK")

        outer_tar = root / "collection.tar.gz"
        with tarfile.open(outer_tar, "w:gz") as t:
            t.add(b2, arcname="serie/cap2.cbz")
            t.add(b1, arcname="serie/cap1.cbz")
        collection = CompressedComicCollection(outer_tar)
        assert collection.count() == 2
        assert collection.member_name(0) == "serie/cap1.cbz"
        first = ComicArchive(collection.member_path(0))
        assert first.count() == 2
        first.close(); collection.close()
        print("TAR.GZ como pasta virtual: OK")

        tar_images = root / "images.tar.gz"
        sample_png = root / "sample.png"
        sample_png.write_bytes(image_bytes("PNG"))
        with tarfile.open(tar_images, "w:gz") as t:
            t.add(sample_png, arcname="fotos/001.png")
        collection = CompressedComicCollection(tar_images)
        assert collection.count() == 1 and collection.member_kind(0) == "image"
        assert collection.load_member_cover(0, 50).size[0] <= 50
        collection.close()
        print("TAR.GZ somente imagens: OK")

        pdf = root / "book.pdf"
        d = fitz.open()
        for _ in range(2):
            p = d.new_page(width=300, height=500)
            p.insert_text((40, 80), "Teste PDF")
        d.save(pdf); d.close()
        a = ComicArchive(pdf)
        assert a.kind == "pdf" and a.count() == 2
        im = Image.open(io.BytesIO(a.read_bytes(0)))
        assert im.width == 600 and im.height == 1000
        a.close()
        print("PDF: OK")

        epub_img = root / "manga.epub"
        make_epub(epub_img, text=False, image_only_pages=2)
        a = ComicArchive(epub_img)
        assert a.kind == "epub" and a.count() == 2 and not a.has_text_pages()
        assert not a.is_text_page(0)
        Image.open(io.BytesIO(a.read_bytes(1))).verify()
        a.close()
        print("EPUB somente imagens: OK")

        epub_text = root / "novel.epub"
        make_epub(epub_text, text=True, image_only_pages=1)
        a = ComicArchive(epub_text)
        assert a.count() == 2 and a.has_text_pages()
        assert not a.is_text_page(0) and a.is_text_page(1)
        html = a.text_html(1)
        assert "parágrafo" in html and "data:image/png;base64," in html
        a.close()
        print("EPUB misto/texto: OK")

        # Garante que capítulos textuais muito curtos não sejam descartados.
        short = root / "short.epub"
        container = '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>'
        opf = '<package><manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c"/></spine></package>'
        with zipfile.ZipFile(short, "w") as z:
            z.writestr("META-INF/container.xml", container)
            z.writestr("content.opf", opf)
            z.writestr("c.xhtml", "<html><body><p>Fim.</p></body></html>")
        a = ComicArchive(short)
        assert a.count() == 1 and a.is_text_page(0) and "Fim." in a.text_html(0)
        a.close()
        print("EPUB texto curto: OK")

        bad = root / "empty.zip"
        with zipfile.ZipFile(bad, "w") as z: z.writestr("readme.txt", "x")
        try:
            CompressedComicCollection(bad)
        except ArchiveError:
            pass
        else:
            raise AssertionError("Compactado sem CBZ/CBR nem imagens deveria falhar")
        print("Compactado sem mídia suportada: OK (erro esperado)")


if __name__ == "__main__":
    run()
