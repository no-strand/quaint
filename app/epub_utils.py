"""Utilitários de HTML para a visualização de texto EPUB."""
import re

from bs4 import BeautifulSoup, NavigableString

# O QTextBrowser do Qt não respeita ``text-indent`` de forma consistente em
# todos os documentos HTML/EPUB.  Seis NBSP equivalem aproximadamente a 1,5 em
# nas fontes de leitura usuais e, por não serem espaços colapsáveis, garantem
# um recuo visual real da primeira linha.
PARAGRAPH_INDENT_PREFIX = "\u00a0" * 6
_INDENT_MARKER = "data-image-viewer-indent"


def force_paragraph_indentation(html_fragment, indent="1.45em"):
    """Força recuo VISÍVEL da primeira linha em todo elemento ``<p>``.

    Mantém ``text-indent`` como regra CSS e também insere espaços não
    separáveis no começo de cada parágrafo textual. Isso evita depender apenas
    do suporte parcial a CSS do ``QTextBrowser`` do Qt.
    """
    html_fragment = html_fragment or ""
    if "<p" not in html_fragment.lower():
        return html_fragment

    soup = BeautifulSoup(html_fragment, "html.parser")
    for paragraph in soup.find_all("p"):
        style = paragraph.get("style", "")
        style = re.sub(
            r"(?i)(?:^|;)\s*text-indent\s*:[^;]*;?",
            ";",
            style,
        ).strip(" ;")
        if style:
            style += "; "
        paragraph["style"] = f"{style}text-indent: {indent} !important;"

        # Insere o recuo diretamente no conteúdo para que ele apareça mesmo
        # quando o mecanismo HTML do Qt ignora a propriedade text-indent.
        # O marcador impede duplicação caso o fragmento seja processado outra
        # vez antes de ser exibido.
        if paragraph.get(_INDENT_MARKER) != "1":
            paragraph.insert(0, NavigableString(PARAGRAPH_INDENT_PREFIX))
            paragraph[_INDENT_MARKER] = "1"

    return str(soup)
