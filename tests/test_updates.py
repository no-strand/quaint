from pathlib import Path

from app.epub_utils import force_paragraph_indentation, PARAGRAPH_INDENT_PREFIX
from app.save_helpers import build_save_targets
from app.version import APP_VERSION
from app.win_registration import FILE_TYPE_GROUPS


def main():
    assert APP_VERSION == "1.0.0"

    html = (
        '<p>Primeiro</p>'
        '<p style="text-indent:0; color:red">Segundo</p>'
        '<div><p style="font-weight:bold">Terceiro</p></div>'
    )
    out = force_paragraph_indentation(html)
    assert out.count("text-indent: 1.45em !important") == 3
    assert "text-indent:0" not in out.replace(" ", "")
    assert "color:red" in out.replace(" ", "")
    assert "font-weight:bold" in out.replace(" ", "")
    # O recuo precisa existir no conteúdo, não apenas no CSS, porque o
    # QTextBrowser pode ignorar text-indent.
    from bs4 import BeautifulSoup
    parsed = BeautifulSoup(out, "html.parser")
    paragraphs = parsed.find_all("p")
    assert len(paragraphs) == 3
    assert all(p.get_text().startswith(PARAGRAPH_INDENT_PREFIX) for p in paragraphs)
    # Reprocessar não pode duplicar o recuo.
    out2 = force_paragraph_indentation(out)
    parsed2 = BeautifulSoup(out2, "html.parser")
    assert all(p.get_text().startswith(PARAGRAPH_INDENT_PREFIX) for p in parsed2.find_all("p"))
    assert all(not p.get_text().startswith(PARAGRAPH_INDENT_PREFIX * 2) for p in parsed2.find_all("p"))

    targets = build_save_targets("/tmp/paginas.png", [3, 2], "PNG (*.png)")
    assert [(i, p.name) for i, p in targets] == [
        (2, "paginas_003.png"),
        (3, "paginas_004.png"),
    ]

    assert FILE_TYPE_GROUPS["comics"]["extensions"] == (".cbz", ".cbr")
    assert FILE_TYPE_GROUPS["epub"]["extensions"] == (".epub",)
    assert FILE_TYPE_GROUPS["pdf"]["extensions"] == (".pdf",)

    installer = Path(__file__).resolve().parents[1] / "build" / "installer.iss"
    text = installer.read_text(encoding="utf-8")
    assert '#define MyAppVersion "1.0.0"' in text
    assert 'Name: "associateepub"' in text
    assert 'Name: "associatepdf"' in text

    print("Atualizações 1.0.0: OK")


if __name__ == "__main__":
    main()
