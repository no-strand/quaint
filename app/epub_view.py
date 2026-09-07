"""Visualização refluível para capítulos textuais de EPUB."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextBrowser, QFrame

from app.epub_utils import force_paragraph_indentation


class EpubTextView(QTextBrowser):
    """Leitor de texto simples, confortável e independente do CSS do EPUB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("epubTextView")
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setReadOnly(True)
        # QTextBrowser pode aceitar drops por conta própria; desabilitamos
        # isso para que arquivos soltos sobre uma página EPUB cheguem à janela
        # principal e sejam abertos pelo mesmo fluxo dos demais modos.
        self.setAcceptDrops(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._background = "#ffffff"
        self._html = ""
        self._font_family = "Georgia"
        self._font_size = 17
        self._text_width = 820

    def set_epub_settings(self, font_family, font_size, text_width):
        self._font_family = str(font_family or "Georgia")
        self._font_size = int(font_size or 17)
        self._text_width = int(text_width or 820)
        if self._html:
            self._render()

    def set_background_color(self, color):
        self._background = color
        if self._html:
            self._render()
        else:
            self.setStyleSheet(f"background: {color}; border: none;")

    def show_chapter(self, html_fragment):
        self._html = force_paragraph_indentation(html_fragment or "")
        self._render()
        self.verticalScrollBar().setValue(0)

    def _render(self):
        family = self._font_family.replace("'", "\\'")
        dark = self._background.lower() not in ("#ffffff", "white", "#fff")
        text_color = "#ececf1" if dark else "#202024"
        secondary = "#b8b8c2" if dark else "#575762"
        link = "#7aa2f7" if dark else "#315ebd"
        width = max(520, min(self._text_width, 1400))

        # Valores tipográficos deliberadamente fixos: indentação e espaçamento
        # vêm com um padrão confortável, como solicitado, sem exigir ajuste.
        css = f"""
        <style>
          html, body {{ background: {self._background}; color: {text_color}; }}
          body {{
            font-family: '{family}';
            font-size: {self._font_size}pt;
            line-height: 1.55;
            margin: 0;
            padding: 0;
          }}
          .reader {{
            max-width: {width}px;
            margin: 0 auto;
            padding: 44px 54px 70px 54px;
          }}
          p {{
            margin: 0 0 0.78em 0;
            text-indent: 1.45em !important;
            text-align: justify;
          }}
          h1, h2, h3, h4, h5, h6 {{
            text-indent: 0;
            line-height: 1.22;
            margin: 1.15em 0 0.65em 0;
          }}
          h1:first-child, h2:first-child, h3:first-child {{ margin-top: 0; }}
          blockquote {{
            margin: 1em 1.7em;
            color: {secondary};
          }}
          img, svg {{
            max-width: 100%;
            height: auto;
            margin: 1em auto;
          }}
          a {{ color: {link}; }}
          pre, code {{ white-space: pre-wrap; }}
        </style>
        """
        self.setStyleSheet(f"QTextBrowser {{ background: {self._background}; color: {text_color}; border: none; }}")
        self.setHtml(f"<html><head>{css}</head><body><div class='reader'>{self._html}</div></body></html>")
