"""Leitor paginado para capítulos textuais de EPUB.

O capítulo é diagramado em páginas de tamanho lógico fixo e desenhado como um
PDF/livro. A fonte continua configurável e uma mudança tipográfica repagina o
capítulo automaticamente.
"""
from math import ceil
from bisect import bisect_right
from collections import OrderedDict

from PIL import Image
from PySide6.QtCore import Qt, QSize, QSizeF, QRectF, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QLinearGradient,
    QBrush,
    QPalette,
    QPixmap,
    QImage,
    QTextDocument,
    QTextOption,
    QAbstractTextDocumentLayout,
)
from PySide6.QtWidgets import QBoxLayout, QFrame, QHBoxLayout, QScrollArea, QSizePolicy, QWidget

from app.epub_utils import force_paragraph_indentation
from app.views import _BookGutter


EPUB_THEME_LIGHT = "light"
EPUB_THEME_DARK = "dark"
EPUB_THEME_SEPIA = "sepia"
EPUB_THEMES = (EPUB_THEME_LIGHT, EPUB_THEME_DARK, EPUB_THEME_SEPIA)

# Tamanho lógico imutável da folha. A janela somente escala o desenho inteiro,
# tal como uma página raster/PDF. Assim a quantidade de texto depende da fonte,
# e não do tamanho momentâneo da janela.
PAGE_WIDTH = 720
PAGE_HEIGHT = 960
PAGE_MARGIN_LEFT = 66
PAGE_MARGIN_RIGHT = 62
PAGE_MARGIN_TOP = 68
PAGE_MARGIN_BOTTOM = 70
CONTENT_WIDTH = PAGE_WIDTH - PAGE_MARGIN_LEFT - PAGE_MARGIN_RIGHT
CONTENT_HEIGHT = PAGE_HEIGHT - PAGE_MARGIN_TOP - PAGE_MARGIN_BOTTOM
PAGE_RATIO = PAGE_WIDTH / PAGE_HEIGHT

_THEME_PALETTE = {
    EPUB_THEME_LIGHT: {
        "paper": "#ffffff",
        "text": "#202024",
        "secondary": "#5f5f68",
        "link": "#315ebd",
        "border": "#a7a8ad",
    },
    EPUB_THEME_DARK: {
        "paper": "#18191d",
        "text": "#ececf1",
        "secondary": "#b5b6bf",
        "link": "#8fb1ff",
        "border": "#34363d",
    },
    EPUB_THEME_SEPIA: {
        "paper": "#f3e7cf",
        "text": "#3c3125",
        "secondary": "#705f4a",
        "link": "#72512d",
        "border": "#ae9b78",
    },
}


def epub_paper_color(theme):
    """Return the logical paper color used by paginated EPUB pages."""
    theme = theme if theme in EPUB_THEMES else EPUB_THEME_LIGHT
    return _THEME_PALETTE[theme]["paper"]


def _epub_document_css(font_family, font_size, theme):
    palette = _THEME_PALETTE[theme if theme in EPUB_THEMES else EPUB_THEME_LIGHT]
    family = str(font_family or "Georgia").replace("'", "\\'")
    return f"""
    <style>
      html, body {{
        background: transparent;
        color: {palette['text']};
        direction: ltr;
      }}
      body {{
        font-family: '{family}';
        font-size: {int(font_size)}pt;
        line-height: 1.48;
        margin: 0;
        padding: 0;
      }}
      p {{
        margin: 0 0 0.72em 0;
        text-indent: 1.42em;
        text-align: justify;
      }}
      h1, h2, h3, h4, h5, h6 {{
        text-indent: 0;
        line-height: 1.22;
        margin: 0.85em 0 0.55em 0;
        page-break-after: avoid;
      }}
      h1:first-child, h2:first-child, h3:first-child {{ margin-top: 0; }}
      blockquote {{ margin: 0.8em 1.35em; color: {palette['secondary']}; }}
      img, svg {{ max-width: 100%; height: auto; margin: 0.65em auto; }}
      a {{ color: {palette['link']}; }}
      pre, code {{ white-space: pre-wrap; }}
    </style>
    """


def _configure_epub_document(document, html_fragment, font_family, font_size, theme):
    """Aplica exatamente a mesma diagramação usada pelo leitor principal."""
    font_size = max(10, min(36, int(font_size or 17)))
    theme = theme if theme in EPUB_THEMES else EPUB_THEME_LIGHT
    fragment = force_paragraph_indentation(html_fragment or "")

    font = QFont(str(font_family or "Georgia"))
    font.setPointSizeF(float(font_size))
    document.setDocumentMargin(0)
    document.setDefaultFont(font)
    document.setPageSize(QSizeF(CONTENT_WIDTH, CONTENT_HEIGHT))
    option = QTextOption(document.defaultTextOption())
    option.setTextDirection(Qt.LeftToRight)
    option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option)
    css = _epub_document_css(font.family(), font_size, theme)
    document.setHtml(f"<html><head>{css}</head><body><div dir='ltr'>{fragment}</div></body></html>")
    document.setPageSize(QSizeF(CONTENT_WIDTH, CONTENT_HEIGHT))

    count = int(document.pageCount())
    if count <= 0:
        height = max(1.0, float(document.size().height()))
        count = max(1, int(ceil(height / CONTENT_HEIGHT)))
    return max(1, count)


def _paint_document_page(painter, document, page_index, theme, width, height, *, draw_shadow=False):
    """Pinta uma página lógica inteira; usado pelo leitor e pelas miniaturas."""
    palette = _THEME_PALETTE[theme if theme in EPUB_THEMES else EPUB_THEME_LIGHT]
    scale = min(float(width) / PAGE_WIDTH, float(height) / PAGE_HEIGHT)
    draw_w = PAGE_WIDTH * scale
    draw_h = PAGE_HEIGHT * scale
    offset_x = (float(width) - draw_w) / 2.0
    offset_y = (float(height) - draw_h) / 2.0

    if draw_shadow:
        shadow = QRectF(offset_x + max(1.0, 5 * scale), offset_y + max(1.0, 6 * scale), draw_w, draw_h)
        painter.fillRect(shadow, QColor(0, 0, 0, 52))

    painter.save()
    painter.translate(offset_x, offset_y)
    painter.scale(scale, scale)
    page_rect = QRectF(0, 0, PAGE_WIDTH, PAGE_HEIGHT)
    painter.fillRect(page_rect, QColor(palette["paper"]))
    painter.setPen(QPen(QColor(palette["border"]), 1.0))
    painter.drawRect(page_rect.adjusted(0.5, 0.5, -0.5, -0.5))

    painter.save()
    painter.translate(PAGE_MARGIN_LEFT, PAGE_MARGIN_TOP)
    painter.setClipRect(QRectF(0, 0, CONTENT_WIDTH, CONTENT_HEIGHT))
    painter.translate(0, -int(page_index) * CONTENT_HEIGHT)
    context = QAbstractTextDocumentLayout.PaintContext()
    context.clip = QRectF(0, int(page_index) * CONTENT_HEIGHT, CONTENT_WIDTH, CONTENT_HEIGHT)
    document.documentLayout().draw(painter, context)
    painter.restore()

    page_font = QFont()
    page_font.setPointSizeF(8.5)
    painter.setFont(page_font)
    painter.setPen(QColor(palette["secondary"]))
    painter.drawText(
        QRectF(0, PAGE_HEIGHT - 48, PAGE_WIDTH, 24),
        Qt.AlignHCenter | Qt.AlignVCenter,
        str(int(page_index) + 1),
    )
    painter.restore()


def _qimage_to_pil(image):
    converted = image.convertToFormat(QImage.Format_RGBA8888)
    raw = bytes(converted.bits())
    return Image.frombytes("RGBA", (converted.width(), converted.height()), raw)


class EpubThumbnailArchive:
    """Adapta um EPUB para miniaturas por página visual, como um PDF.

    Capítulos de texto são expandidos para as páginas fixas resultantes da
    diagramação atual. Páginas ilustradas de EPUB misto continuam sendo uma
    única miniatura cada. A tabela de páginas só é construída quando o painel
    de miniaturas é realmente aberto.
    """

    def __init__(self, archive, font_family="Georgia", font_size=17, theme=EPUB_THEME_LIGHT):
        self.archive = archive
        self.font_family = str(font_family or "Georgia")
        self.font_size = max(10, min(36, int(font_size or 17)))
        self.theme = theme if theme in EPUB_THEMES else EPUB_THEME_LIGHT
        self.kind = "epub_text_thumbnails"
        # Índice compacto por capítulo. A versão anterior materializava uma
        # tupla + entrada de dict para cada página visual; em EPUBs longos isso
        # duplicava memória apenas para mapear linha <-> capítulo/página.
        self._source_counts = None
        self._prefix_ends = None
        self._total_pages = 0
        # QTextDocument/QPainter usados para páginas de texto devem permanecer
        # na thread gráfica. O cache é pequeno e é acessado somente pelo event
        # loop principal através do PixmapProvider.
        self._document_cache = OrderedDict()

    def _count_cache(self):
        cache = getattr(self.archive, "_epub_thumbnail_page_count_cache", None)
        if not isinstance(cache, OrderedDict):
            cache = OrderedDict(cache or ())
            try:
                setattr(self.archive, "_epub_thumbnail_page_count_cache", cache)
            except Exception:
                pass
        return cache

    def _chapter_page_count(self, archive_index):
        key = (int(archive_index), self.font_family, self.font_size)
        cache = self._count_cache()
        cached = cache.get(key)
        if cached is not None:
            cache.move_to_end(key)
            return max(1, int(cached))
        document = QTextDocument()
        count = _configure_epub_document(
            document, self.archive.text_html(archive_index),
            self.font_family, self.font_size, self.theme,
        )
        cache[key] = int(count)
        cache.move_to_end(key)
        # Alterar fonte/tamanho várias vezes em um EPUB muito longo não deve
        # acumular indefinidamente uma combinação por capítulo/configuração.
        while len(cache) > 4096:
            cache.popitem(last=False)
        return int(count)

    def _ensure_map(self):
        if self._source_counts is not None:
            return
        counts = []
        prefix_ends = []
        total = 0
        for archive_index in range(self.archive.count()):
            count = (
                self._chapter_page_count(archive_index)
                if self.archive.is_text_page(archive_index) else 1
            )
            count = max(1, int(count))
            counts.append(count)
            total += count
            prefix_ends.append(total)
        self._source_counts = counts
        self._prefix_ends = prefix_ends
        self._total_pages = total

    def count(self):
        self._ensure_map()
        return int(self._total_pages)

    def is_text_page(self, _index):
        # Para o ThumbnailPanel todas as entradas são desenháveis. As páginas
        # textuais são rasterizadas sob demanda por load_image().
        return False

    def position(self, row):
        self._ensure_map()
        if self._total_pages <= 0:
            return 0, None, False
        row = max(0, min(int(row), self._total_pages - 1))
        archive_index = bisect_right(self._prefix_ends, row)
        start = 0 if archive_index == 0 else self._prefix_ends[archive_index - 1]
        is_text = bool(self.archive.is_text_page(archive_index))
        page_index = (row - start) if is_text else None
        return int(archive_index), page_index, is_text

    def page_count_for_source(self, archive_index):
        """Quantidade de páginas visuais de um item do EPUB.

        Capítulos textuais retornam sua paginação fixa atual; páginas de
        imagem representam sempre uma única página visual.
        """
        archive_index = int(archive_index)
        if self.archive.is_text_page(archive_index):
            return self._chapter_page_count(archive_index)
        return 1

    def row_for_source(self, archive_index, page_index=None):
        self._ensure_map()
        archive_index = max(0, min(int(archive_index), self.archive.count() - 1))
        start = 0 if archive_index == 0 else self._prefix_ends[archive_index - 1]
        if self.archive.is_text_page(archive_index):
            count = self._source_counts[archive_index]
            page_index = max(0, min(int(page_index or 0), count - 1))
            return int(start + page_index)
        return int(start)

    def requires_gui_thread_for(self, row):
        """Indica ao provider que páginas textuais precisam ser pintadas no GUI thread.

        QImage é thread-safe, mas QTextDocument/QAbstractTextDocumentLayout e o
        subsistema de fontes do Qt não são um bom alvo para vários QRunnable
        concorrentes no Windows. Páginas de imagem continuam livres para os
        workers normais.
        """
        try:
            return bool(self.position(row)[2])
        except Exception:
            return False

    def _main_document(self, archive_index):
        key = (int(archive_index), self.font_family, self.font_size, self.theme)
        document = self._document_cache.pop(key, None)
        if document is not None:
            self._document_cache[key] = document
            return document

        document = QTextDocument()
        _configure_epub_document(
            document, self.archive.text_html(archive_index),
            self.font_family, self.font_size, self.theme,
        )
        self._document_cache[key] = document
        # Miniaturas visíveis normalmente pertencem a poucos capítulos.
        # Manter três layouts evita repaginar ao voltar sem reter o livro
        # inteiro em objetos QTextDocument.
        while len(self._document_cache) > 3:
            self._document_cache.popitem(last=False)
        return document

    def load_image(self, row, max_dim=None):
        archive_index, page_index, is_text = self.position(row)
        if not is_text:
            return self.archive.load_image(archive_index, max_dim)

        max_dim = max(96, int(max_dim or 340))
        height = max_dim
        width = max(72, int(round(height * PAGE_RATIO)))
        image = QImage(width, height, QImage.Format_ARGB32)
        image.fill(QColor(_THEME_PALETTE[self.theme]["paper"]))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        document = self._main_document(archive_index)
        _paint_document_page(
            painter, document, page_index, self.theme, width, height, draw_shadow=False
        )
        painter.end()
        return _qimage_to_pil(image)


def _checker_brush():
    """Mesmo padrão discreto usado pelo fundo geral do leitor de imagens."""
    tile = QPixmap(24, 24)
    tile.fill(QColor("#eeeeee"))
    painter = QPainter(tile)
    painter.fillRect(0, 0, 12, 12, QColor("#d8d8dc"))
    painter.fillRect(12, 12, 12, 12, QColor("#d8d8dc"))
    painter.end()
    return QBrush(tile)


def _apply_canvas_background(widget, color, checker=False):
    """Aplica somente o fundo externo, sem interferir no papel do EPUB."""
    widget.setStyleSheet("border: none;")
    palette = widget.palette()
    palette.setBrush(
        QPalette.Window,
        _checker_brush() if checker else QBrush(QColor(color)),
    )
    widget.setAutoFillBackground(True)
    widget.setPalette(palette)


class _TextPageWidget(QWidget):
    """Superfície que pinta uma única página do QTextDocument paginado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._document = None
        self._page_index = None
        self._theme = EPUB_THEME_LIGHT
        self._binding_shadow = False
        self._spine_edge = None
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setMinimumSize(180, 240)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def set_page(self, document, page_index, theme):
        self._document = document
        self._page_index = None if page_index is None else int(page_index)
        self._theme = theme if theme in EPUB_THEMES else EPUB_THEME_LIGHT
        self.setVisible(self._page_index is not None)
        self.update()

    def page_index(self):
        return self._page_index

    def set_binding_shadow(self, enabled, edge=None):
        self._binding_shadow = bool(enabled)
        self._spine_edge = edge if edge in ("left", "right") else None
        self.update()

    def paintEvent(self, event):  # noqa: N802
        if self._document is None or self._page_index is None:
            return

        palette = _THEME_PALETTE[self._theme]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        # Página inteira escalada uniformemente, sem alterar o layout do texto.
        scale = min(self.width() / PAGE_WIDTH, self.height() / PAGE_HEIGHT)
        draw_w = PAGE_WIDTH * scale
        draw_h = PAGE_HEIGHT * scale
        offset_x = (self.width() - draw_w) / 2.0
        offset_y = (self.height() - draw_h) / 2.0

        # Sombra simples, semelhante à apresentação de páginas de PDF.
        shadow = QRectF(offset_x + 5, offset_y + 6, draw_w, draw_h)
        painter.fillRect(shadow, QColor(0, 0, 0, 52))

        painter.save()
        painter.translate(offset_x, offset_y)
        painter.scale(scale, scale)
        page_rect = QRectF(0, 0, PAGE_WIDTH, PAGE_HEIGHT)
        painter.fillRect(page_rect, QColor(palette["paper"]))
        painter.setPen(QPen(QColor(palette["border"]), 1.0))
        painter.drawRect(page_rect.adjusted(0.5, 0.5, -0.5, -0.5))
        # No spread duplo, a borda interna não é desenhada pela folha: o
        # traço central vem exclusivamente do mesmo gutter de 1 px usado por
        # CBZ/PDF/imagens. Isso evita somar borda + espaço + borda.
        if self._spine_edge == "right":
            painter.fillRect(QRectF(PAGE_WIDTH - 1.5, 0, 1.5, PAGE_HEIGHT), QColor(palette["paper"]))
        elif self._spine_edge == "left":
            painter.fillRect(QRectF(0, 0, 1.5, PAGE_HEIGHT), QColor(palette["paper"]))

        # Conteúdo: o QTextDocument possui PAGE_SIZE == CONTENT_SIZE, portanto
        # sua paginação já garante que linhas não sejam simplesmente cortadas.
        painter.save()
        painter.translate(PAGE_MARGIN_LEFT, PAGE_MARGIN_TOP)
        painter.setClipRect(QRectF(0, 0, CONTENT_WIDTH, CONTENT_HEIGHT))
        painter.translate(0, -self._page_index * CONTENT_HEIGHT)
        context = QAbstractTextDocumentLayout.PaintContext()
        context.clip = QRectF(
            0,
            self._page_index * CONTENT_HEIGHT,
            CONTENT_WIDTH,
            CONTENT_HEIGHT,
        )
        self._document.documentLayout().draw(painter, context)
        painter.restore()

        # Número da página interna do capítulo, discreto como em um livro/PDF.
        page_font = QFont(self.font())
        page_font.setPointSizeF(8.5)
        painter.setFont(page_font)
        painter.setPen(QColor(palette["secondary"]))
        painter.drawText(
            QRectF(0, PAGE_HEIGHT - 48, PAGE_WIDTH, 24),
            Qt.AlignHCenter | Qt.AlignVCenter,
            str(self._page_index + 1),
        )

        # Sombra de encadernação: no modo de duas páginas a borda interna de
        # cada folha recebe o mesmo efeito gradual usado pelo leitor de PDF.
        if self._binding_shadow and self._spine_edge:
            # DoublePageView usa 42 px reais. Como esta folha é pintada em
            # coordenadas lógicas e depois escalada, convertemos 42 px para o
            # espaço lógico para manter a mesma largura visual.
            spine_w = min(42.0 / max(scale, 0.001), PAGE_WIDTH / 3.0)
            grad = QLinearGradient()
            if self._spine_edge == "right":
                grad.setStart(PAGE_WIDTH - spine_w, 0)
                grad.setFinalStop(PAGE_WIDTH, 0)
                x = PAGE_WIDTH - spine_w
            else:
                grad.setStart(spine_w, 0)
                grad.setFinalStop(0, 0)
                x = 0.0
            grad.setColorAt(0.0, QColor(0, 0, 0, 0))
            grad.setColorAt(1.0, QColor(0, 0, 0, 70))
            painter.fillRect(QRectF(x, 0, spine_w, PAGE_HEIGHT), grad)

        painter.restore()
        painter.end()


class EpubTextView(QScrollArea):
    """Visualização de EPUB textual em páginas fixas simples ou duplas.

    O documento é sempre diagramado da esquerda para a direita. Em modo duplo,
    as páginas [0,1], [2,3]... são exibidas como um livro ocidental normal.
    """

    pagination_changed = Signal(int, int, int)  # início visível, fim, total

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("epubTextView")
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignCenter)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptDrops(False)

        self._font_family = "Georgia"
        self._font_size = 17
        self._theme = EPUB_THEME_LIGHT
        self._mode = "single"
        self._background_color = "#ffffff"
        self._background_checker = False
        self._shadow_enabled = True
        self._html = ""
        self._page_count = 1
        self._spread_start = 0

        self._document = QTextDocument(self)
        self._document.setDocumentMargin(0)
        self._document.setPageSize(QSizeF(CONTENT_WIDTH, CONTENT_HEIGHT))
        option = QTextOption(self._document.defaultTextOption())
        option.setTextDirection(Qt.LeftToRight)
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self._document.setDefaultTextOption(option)

        self._container = QWidget()
        self._container.setObjectName("epubPageCanvas")
        self._layout = QHBoxLayout(self._container)
        self._layout.setContentsMargins(22, 18, 22, 18)
        self._layout.setSpacing(0)
        self._layout.setDirection(QBoxLayout.LeftToRight)
        self._layout.addStretch(1)
        self.left_page = _TextPageWidget(self._container)
        self.right_page = _TextPageWidget(self._container)
        # Usa literalmente o mesmo gutter do DoublePageView (CBZ/PDF/imagem):
        # uma linha central de 1 px, sem espaçamento adicional entre folhas.
        self.gutter = _BookGutter(self._container)
        self.gutter.setVisible(False)
        self._layout.addWidget(self.left_page, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._layout.addWidget(self.gutter, 0)
        self._layout.addWidget(self.right_page, 0, Qt.AlignLeft | Qt.AlignVCenter)
        self._layout.addStretch(1)
        # As páginas são superfícies de pintura, não controles interativos.
        # Deixar o container transparente ao mouse faz o clique direito chegar
        # diretamente ao viewport, onde o menu contextual do leitor é conectado.
        self._container.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setWidget(self._container)

        self._apply_theme_surface()
        self._update_page_sizes()

    # --------------------------------------------------------- configuração --
    def set_background_color(self, color, checker=False):
        """Define apenas a área ao redor das folhas.

        O tema claro/escuro/sépia continua pertencendo exclusivamente ao papel
        e ao texto do EPUB.
        """
        self._background_color = str(color or "#ffffff")
        self._background_checker = bool(checker)
        self._apply_theme_surface()

    def set_shadow_enabled(self, enabled):
        self._shadow_enabled = bool(enabled)
        self.gutter.set_enabled(self._shadow_enabled)
        self._refresh_binding_shadow()

    def set_epub_settings(self, font_family, font_size, theme=EPUB_THEME_LIGHT):
        old_page = self._spread_start
        self._font_family = str(font_family or "Georgia")
        self._font_size = max(10, min(36, int(font_size or 17)))
        self._theme = theme if theme in EPUB_THEMES else EPUB_THEME_LIGHT
        if self._html:
            self._render_document(preserve_page=old_page)
        else:
            self._apply_theme_surface()

    def set_theme(self, theme):
        if theme not in EPUB_THEMES or theme == self._theme:
            return
        self.set_epub_settings(self._font_family, self._font_size, theme)

    def theme(self):
        return self._theme

    def set_mode(self, mode):
        mode = "double" if mode == "double" else "single"
        if mode == self._mode:
            self._refresh_pages()
            return
        current = self._spread_start
        self._mode = mode
        if mode == "double":
            current = (current // 2) * 2
        self._spread_start = max(0, min(current, max(0, self._page_count - 1)))
        self._update_page_sizes()
        self._refresh_pages()

    def mode(self):
        return self._mode

    # -------------------------------------------------------------- conteúdo --
    def show_chapter(self, html_fragment, reset=True):
        html_fragment = html_fragment or ""
        same = html_fragment == self._html
        self._html = html_fragment
        preserve = self._spread_start if same and not reset else 0
        self._render_document(preserve_page=preserve)

    def _render_document(self, preserve_page=0):
        # Uma única rotina de configuração é compartilhada com as miniaturas.
        # Isso evita duas árvores de CSS/layout divergentes e trabalho duplicado
        # ao evoluir a paginação do EPUB.
        self._page_count = _configure_epub_document(
            self._document,
            self._html,
            self._font_family,
            self._font_size,
            self._theme,
        )
        preserve_page = max(0, min(int(preserve_page), self._page_count - 1))
        if self._mode == "double":
            preserve_page = (preserve_page // 2) * 2
        self._spread_start = preserve_page
        self._apply_theme_surface()
        self._refresh_pages()

    # ------------------------------------------------------------- navegação --
    def page_count(self):
        return self._page_count

    def spread_start_index(self):
        return self._spread_start

    def spread_end_index(self):
        step = 1 if self._mode == "double" else 0
        return min(self._page_count - 1, self._spread_start + step)

    def can_next(self):
        return self.spread_end_index() < self._page_count - 1

    def can_prev(self):
        return self._spread_start > 0

    def next_pages(self):
        if not self.can_next():
            return False
        self._spread_start = min(
            self._page_count - 1,
            self._spread_start + (2 if self._mode == "double" else 1),
        )
        if self._mode == "double":
            self._spread_start = (self._spread_start // 2) * 2
        self._refresh_pages()
        return True

    def prev_pages(self):
        if not self.can_prev():
            return False
        self._spread_start = max(0, self._spread_start - (2 if self._mode == "double" else 1))
        if self._mode == "double":
            self._spread_start = (self._spread_start // 2) * 2
        self._refresh_pages()
        return True

    def go_first(self):
        self._spread_start = 0
        self._refresh_pages()

    def go_last(self):
        if self._mode == "double":
            self._spread_start = ((self._page_count - 1) // 2) * 2
        else:
            self._spread_start = self._page_count - 1
        self._refresh_pages()

    def go_to_page(self, page_index):
        """Abre uma página interna específica do capítulo atual."""
        page_index = max(0, min(int(page_index), self._page_count - 1))
        if self._mode == "double":
            page_index = (page_index // 2) * 2
        self._spread_start = page_index
        self._refresh_pages()

    # -------------------------------------------------------------- pintura --
    def _refresh_pages(self):
        left = self._spread_start
        right = left + 1 if self._mode == "double" and left + 1 < self._page_count else None
        self.left_page.set_page(self._document, left, self._theme)
        self.right_page.set_page(self._document, right, self._theme)
        self.right_page.setVisible(self._mode == "double" and right is not None)
        self._refresh_binding_shadow()
        self._layout.setSpacing(0 if self._mode == "double" else 18)
        self._update_page_sizes()
        self.pagination_changed.emit(left, right if right is not None else left, self._page_count)

    def _apply_theme_surface(self):
        # O tema do EPUB não pinta mais o fundo externo. Essa superfície segue
        # exclusivamente Exibir > Fundo da página (claro/escuro/quadriculado).
        _apply_canvas_background(self, self._background_color, self._background_checker)
        _apply_canvas_background(self.viewport(), self._background_color, self._background_checker)
        _apply_canvas_background(self._container, self._background_color, self._background_checker)
        self.gutter.set_background_color(self._background_color)
        self.left_page.update()
        self.right_page.update()

    def _refresh_binding_shadow(self):
        double_mode = self._mode == "double"
        self.gutter.setVisible(double_mode)
        self.gutter.set_enabled(self._shadow_enabled)
        # A aresta interna existe mesmo quando a sombra é desligada, pois ela
        # também informa à página qual borda deve ser omitida para que somente
        # o gutter compartilhado forme o traço central.
        self.left_page.set_binding_shadow(
            double_mode and self._shadow_enabled,
            "right" if double_mode else None,
        )
        right_present = double_mode and self.right_page.page_index() is not None
        self.right_page.set_binding_shadow(
            right_present and self._shadow_enabled,
            "left" if right_present else None,
        )

    def _update_page_sizes(self):
        vw = max(260, self.viewport().width())
        vh = max(300, self.viewport().height())
        horizontal_padding = 54
        vertical_padding = 42
        gap = self.gutter.width() if self._mode == "double" else 0
        page_slots = 2 if self._mode == "double" else 1
        avail_w_each = max(180, (vw - horizontal_padding - gap) / page_slots)
        avail_h = max(240, vh - vertical_padding)
        target_h = min(avail_h, avail_w_each / PAGE_RATIO)
        target_w = target_h * PAGE_RATIO
        size = QSize(max(180, int(target_w)), max(240, int(target_h)))
        self.left_page.setFixedSize(size)
        self.right_page.setFixedSize(size)
        self._container.setMinimumSize(vw, vh)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_page_sizes()
