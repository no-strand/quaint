"""Os três modos de leitura: página única, contínua (webtoon) e dupla página."""
from PySide6.QtCore import Qt, QSize, QTimer, Signal, QPointF, QRectF, QEvent
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPixmap, QPalette, QBrush
from app.i18n import tr

from PySide6.QtWidgets import (
    QScrollArea, QLabel, QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy, QScroller
)

LAYOUT_CENTERED = "centered"
LAYOUT_FULL_WIDTH = "full_width"


def _checker_brush(tile_size=16):
    tile = QPixmap(tile_size, tile_size)
    tile.fill(QColor("#eeeeee"))
    painter = QPainter(tile)
    half = tile_size // 2
    painter.fillRect(0, 0, half, half, QColor("#cfcfcf"))
    painter.fillRect(half, half, half, half, QColor("#cfcfcf"))
    painter.end()
    return QBrush(tile)


def _apply_widget_background(widget, color, checker=False):
    widget.setStyleSheet("border: none;")
    palette = widget.palette()
    palette.setBrush(QPalette.Window, _checker_brush() if checker else QBrush(QColor(color)))
    widget.setPalette(palette)
    widget.setAutoFillBackground(True)



class _ZoomPanMixin:
    """Zoom manual e panorâmica reutilizados pelos modos baseados em QScrollArea."""

    MIN_ZOOM = 10
    MAX_ZOOM = 800
    ZOOM_STEP = 25

    def _init_zoom_pan(self):
        self._manual_zoom_percent = None
        self._pan_active = False
        self._pan_last_pos = None
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WA_AcceptTouchEvents, True)
        try:
            QScroller.grabGesture(self.viewport(), QScroller.TouchGesture)
        except Exception:
            pass

    def zoom_percent(self):
        return self._manual_zoom_percent

    def set_zoom_percent(self, percent, anchor=None):
        try:
            percent = int(round(float(percent)))
        except (TypeError, ValueError):
            return
        percent = max(self.MIN_ZOOM, min(self.MAX_ZOOM, percent))
        old_w = self._zoom_reference_width()
        hbar = self.horizontalScrollBar()
        vbar = self.verticalScrollBar()
        if anchor is None:
            anchor = self.viewport().rect().center()
        old_h = hbar.value()
        old_v = vbar.value()
        self._manual_zoom_percent = percent
        self._apply_manual_zoom_state()
        self._rescale_for_zoom()
        new_w = self._zoom_reference_width()
        ratio = (float(new_w) / float(old_w)) if old_w and new_w else 1.0

        def restore_anchor():
            hbar.setValue(int((old_h + anchor.x()) * ratio - anchor.x()))
            vbar.setValue(int((old_v + anchor.y()) * ratio - anchor.y()))

        QTimer.singleShot(0, restore_anchor)

    def zoom_in(self, anchor=None):
        current = self._manual_zoom_percent or self._effective_zoom_percent()
        self.set_zoom_percent(current + self.ZOOM_STEP, anchor)

    def zoom_out(self, anchor=None):
        current = self._manual_zoom_percent or self._effective_zoom_percent()
        self.set_zoom_percent(current - self.ZOOM_STEP, anchor)

    def reset_manual_zoom(self):
        self._manual_zoom_percent = None
        self._apply_manual_zoom_state()
        self._rescale_for_zoom()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in(event.position().toPoint())
            elif event.angleDelta().y() < 0:
                self.zoom_out(event.position().toPoint())
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event):
        if self._manual_zoom_percent is not None and event.button() == Qt.LeftButton:
            self._pan_active = True
            self._pan_last_pos = event.position().toPoint()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._pan_active and self._pan_last_pos is not None:
            pos = event.position().toPoint()
            delta = pos - self._pan_last_pos
            self._pan_last_pos = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pan_active and event.button() == Qt.LeftButton:
            self._pan_active = False
            self._pan_last_pos = None
            self.viewport().unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _effective_zoom_percent(self):
        return 100

    def _zoom_reference_width(self):
        return max(1, self.widget().width() if self.widget() is not None else self.viewport().width())

    def _apply_manual_zoom_state(self):
        self.setWidgetResizable(self._manual_zoom_percent is None)

    def _rescale_for_zoom(self):
        raise NotImplementedError


def _pixmap_rect_in_label(label, pixmap):
    """Retorna o retângulo realmente ocupado pelo pixmap dentro de um QLabel."""
    if pixmap is None or pixmap.isNull():
        return QRectF()
    contents = label.contentsRect()
    width = float(pixmap.width())
    height = float(pixmap.height())
    alignment = label.alignment()

    if alignment & Qt.AlignRight:
        x = contents.right() - width + 1
    elif alignment & Qt.AlignHCenter:
        x = contents.x() + (contents.width() - width) / 2.0
    else:
        x = contents.x()

    if alignment & Qt.AlignBottom:
        y = contents.bottom() - height + 1
    elif alignment & Qt.AlignVCenter:
        y = contents.y() + (contents.height() - height) / 2.0
    else:
        y = contents.y()
    return QRectF(float(x), float(y), width, height)


def _sample_label_pixmap(label, viewport, viewport_pos, source_pixmap=None):
    """Mapeia o cursor do viewport para o pixmap de um QLabel sem I/O."""
    displayed = label.pixmap()
    if displayed is None or displayed.isNull():
        return None
    local = label.mapFromGlobal(viewport.mapToGlobal(viewport_pos))
    pix_rect = _pixmap_rect_in_label(label, displayed)
    point = QPointF(float(local.x()), float(local.y()))
    if not pix_rect.contains(point):
        return None

    source = source_pixmap if source_pixmap is not None and not source_pixmap.isNull() else displayed
    rel_x = (point.x() - pix_rect.left()) / max(pix_rect.width(), 1.0)
    rel_y = (point.y() - pix_rect.top()) / max(pix_rect.height(), 1.0)
    src_point = QPointF(
        min(max(rel_x, 0.0), 1.0) * source.width(),
        min(max(rel_y, 0.0), 1.0) * source.height(),
    )
    return source, src_point


class SinglePageView(_ZoomPanMixin, QScrollArea):
    """Mostra uma página por vez, ajustada à janela (largura, altura ou página inteira)."""

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.setWidgetResizable(True)
        self._init_zoom_pan()
        self.setAlignment(Qt.AlignCenter)
        self.label = QLabel(tr("view.no_page"))
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setObjectName("pageLabel")
        self.setWidget(self.label)
        self.index = -1
        self.fit_mode = "page"  # 'width' | 'height' | 'page' | 'original'
        self.layout_mode = LAYOUT_CENTERED  # 'centered' | 'full_width'
        self._edge_to_edge = False
        self._orig_pixmap = None
        self._animated_frame = False
        self._resize_quality_timer = QTimer(self)
        self._resize_quality_timer.setSingleShot(True)
        self._resize_quality_timer.setInterval(85)
        self._resize_quality_timer.timeout.connect(self._rescale)
        self.provider.pixmap_ready.connect(self._on_pixmap)
        self.provider.animation_frame_ready.connect(self._on_animation_pixmap)
        self.provider.adjustments_changed.connect(self._on_adjustments_changed)

    def show_page(self, index):
        self.index = index
        self._orig_pixmap = None
        self._animated_frame = False
        pix = self.provider.get(index, priority=100)
        if pix is not None:
            self._set_pixmap(pix)
            self.provider.preload_around(index, radius=3, backward=1)
        else:
            self.label.setText(tr("view.loading_page"))

    def _on_pixmap(self, index, pix):
        if index == self.index:
            self._set_pixmap(pix, animated=False)
            # Só começa a antecipar vizinhas depois que a página pedida chegou.
            self.provider.preload_around(index, radius=3, backward=1)

    def _on_animation_pixmap(self, index, pix):
        if index == self.index:
            self._set_pixmap(pix, animated=True)

    def _on_adjustments_changed(self):
        if self.index >= 0:
            self.show_page(self.index)

    def _set_pixmap(self, pix, animated=False):
        self._orig_pixmap = pix
        self._animated_frame = bool(animated)
        self._rescale()

    def _rescale(self, fast=False):
        if self._orig_pixmap is None:
            return
        avail = self.viewport().size()
        pix = self._orig_pixmap
        # Quadros animados mudam dezenas de vezes por segundo; FastTransformation
        # evita gastar a thread da GUI com reamostragem bicúbica a cada frame.
        transform = Qt.FastTransformation if (self._animated_frame or fast) else Qt.SmoothTransformation
        if self._manual_zoom_percent is not None:
            factor = self._manual_zoom_percent / 100.0
            target = QSize(max(1, int(pix.width() * factor)), max(1, int(pix.height() * factor)))
            scaled = pix.scaled(target, Qt.KeepAspectRatio, transform)
        elif self.layout_mode == LAYOUT_FULL_WIDTH:
            # Ocupa 100% da largura disponível, independente do modo de ajuste.
            scaled = pix.scaledToWidth(max(avail.width(), 50), transform)
        elif self.fit_mode == "width":
            inset = 0 if self._edge_to_edge else 4
            scaled = pix.scaledToWidth(max(avail.width() - inset, 50), transform)
        elif self.fit_mode == "height":
            inset = 0 if self._edge_to_edge else 4
            scaled = pix.scaledToHeight(max(avail.height() - inset, 50), transform)
        elif self.fit_mode == "page":
            scaled = pix.scaled(avail, Qt.KeepAspectRatio, transform)
        else:
            scaled = pix
        self.label.setPixmap(scaled)
        self.label.resize(scaled.size())

    def _rescale_for_zoom(self):
        self._rescale()

    def _zoom_reference_width(self):
        pix = self.label.pixmap()
        return pix.width() if pix is not None and not pix.isNull() else super()._zoom_reference_width()

    def _effective_zoom_percent(self):
        if self._orig_pixmap is None or self._orig_pixmap.isNull():
            return 100
        shown = self.label.pixmap()
        if shown is None or shown.isNull():
            return 100
        return max(1, int(round(shown.width() * 100.0 / max(self._orig_pixmap.width(), 1))))

    def magnifier_sample(self, viewport_pos):
        return _sample_label_pixmap(
            self.label, self.viewport(), viewport_pos, self._orig_pixmap
        )

    def retranslate_ui(self):
        if self._orig_pixmap is None:
            self.label.setText(
                tr("view.loading_page") if self.index >= 0 else tr("view.no_page")
            )

    def set_fit_mode(self, mode):
        self.fit_mode = mode
        self.reset_manual_zoom()

    def set_layout_mode(self, mode):
        self.layout_mode = mode
        self.setAlignment(Qt.AlignHCenter if mode == LAYOUT_FULL_WIDTH else Qt.AlignCenter)
        self._rescale()

    def set_background_color(self, color, checker=False):
        """Define cor sólida ou padrão quadriculado da área de leitura."""
        _apply_widget_background(self, color, checker)
        _apply_widget_background(self.viewport(), color, checker)

    def set_edge_to_edge(self, enabled):
        self._edge_to_edge = bool(enabled)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self._rescale()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Durante resize contínuo usa filtro barato; quando o usuário para por
        # alguns ms, refaz uma única vez com qualidade Smooth.
        self._rescale(fast=True)
        self._resize_quality_timer.start()


class _BookGutter(QWidget):
    """Fina linha entre as duas páginas do modo duplo — no máximo 1px de
    separação, para que as páginas fiquem coladas no centro. O efeito de
    sombra da encadernação vem principalmente do sombreamento translúcido
    aplicado à borda interna de cada página (perto da lombada), não de um
    bloco largo aqui."""

    WIDTH = 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(self.WIDTH)
        self.enabled = True
        self._bg_color = QColor("#101014")
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self.update()

    def set_background_color(self, color):
        self._bg_color = QColor(color)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._bg_color)
        if self.enabled:
            # Linha fina e translúcida (canal alfa, não cor sólida opaca)
            # marcando a dobra central do livro.
            painter.fillRect(self.rect(), QColor(0, 0, 0, 130))
        painter.end()


class DoublePageView(_ZoomPanMixin, QScrollArea):
    """Mostra duas páginas lado a lado, como um livro aberto. Suporta LTR e RTL."""

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.setWidgetResizable(True)
        self._init_zoom_pan()
        self.setAlignment(Qt.AlignCenter)
        container = QWidget()
        self.hbox = QHBoxLayout(container)
        self.hbox.setSpacing(0)
        self.hbox.setContentsMargins(8, 8, 8, 8)
        self.label_a = QLabel()
        self.label_b = QLabel()
        # As páginas ficam coladas na linha central: a página da esquerda
        # alinhada à direita da sua metade, a da direita alinhada à
        # esquerda da sua metade — em vez de centralizadas cada uma no
        # próprio espaço (o que deixaria um vão vazio entre elas quando a
        # imagem não preenche toda a largura disponível).
        self.label_a.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.label_b.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for lbl in (self.label_a, self.label_b):
            lbl.setObjectName("pageLabel")
        self.gutter = _BookGutter()
        self.hbox.addWidget(self.label_a, 1)
        self.hbox.addWidget(self.gutter, 0)
        self.hbox.addWidget(self.label_b, 1)
        self.setWidget(container)

        self.direction = "ltr"  # 'ltr' ou 'rtl' (mangá)
        self.layout_mode = LAYOUT_CENTERED
        self._edge_to_edge = False
        self.left_index = None
        self.right_index = None
        self._pix_cache = {}
        self._source_pix_a = None
        self._source_pix_b = None
        self._prefetch_anchor = None
        self._resize_quality_timer = QTimer(self)
        self._resize_quality_timer.setSingleShot(True)
        self._resize_quality_timer.setInterval(85)
        self._resize_quality_timer.timeout.connect(self._rescale_visible_smooth)
        self.provider.pixmap_ready.connect(self._on_pixmap)
        self.provider.animation_frame_ready.connect(self._on_animation_pixmap)
        self.provider.adjustments_changed.connect(self._on_adjustments_changed)

    def set_direction(self, d):
        self.direction = d

    def set_shadow_enabled(self, enabled):
        self.gutter.set_enabled(enabled)
        # Reaplica as páginas para atualizar (ou remover) o sombreamento
        # sutil na borda interna, perto da lombada.
        if self.left_index is not None or self.right_index is not None:
            self._load(self.label_a, self.left_index, "right")
            self._load(self.label_b, self.right_index, "left")

    def set_background_color(self, color, checker=False):
        """Define cor sólida ou padrão quadriculado da área de leitura."""
        _apply_widget_background(self, color, checker)
        _apply_widget_background(self.viewport(), color, checker)
        _apply_widget_background(self.widget(), color, checker)
        self.gutter.set_background_color(color)

    def set_edge_to_edge(self, enabled):
        self._edge_to_edge = bool(enabled)
        side = 0 if (self._edge_to_edge or self.layout_mode == LAYOUT_FULL_WIDTH) else 8
        vertical = 0 if self._edge_to_edge else 8
        self.hbox.setContentsMargins(side, vertical, side, vertical)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self._rescale_for_zoom()

    def set_layout_mode(self, mode):
        self.layout_mode = mode
        self._manual_zoom_percent = None
        self._apply_manual_zoom_state()
        side = 0 if (self._edge_to_edge or mode == LAYOUT_FULL_WIDTH) else 8
        vertical = 0 if self._edge_to_edge else 8
        self.hbox.setContentsMargins(side, vertical, side, vertical)
        if self.left_index is not None or self.right_index is not None:
            self._load(self.label_a, self.left_index, "right")
            self._load(self.label_b, self.right_index, "left")

    def show_spread(self, anchor_index, total):
        """Calcula e exibe o par de páginas que contém anchor_index."""
        if anchor_index <= 0:
            a, b = 0, (1 if total > 1 else None)
        else:
            a = anchor_index - 1 if anchor_index % 2 == 0 else anchor_index
            b = a + 1 if a + 1 < total else None

        left, right = (b, a) if self.direction == "rtl" else (a, b)
        self.left_index, self.right_index = left, right
        self._prefetch_anchor = int(anchor_index)
        ready_a = self._load(self.label_a, left, "right")
        ready_b = self._load(self.label_b, right, "left")
        if ready_a or ready_b:
            self._start_spread_prefetch()

    def spread_start_index(self):
        """Menor índice do par exibido atualmente (para navegação)."""
        vals = [i for i in (self.left_index, self.right_index) if i is not None]
        return min(vals) if vals else 0

    def spread_end_index(self):
        vals = [i for i in (self.left_index, self.right_index) if i is not None]
        return max(vals) if vals else 0

    def _load(self, label, index, edge):
        if index is None:
            label.clear()
            label.setText("")
            if label is self.label_a:
                self._source_pix_a = None
            elif label is self.label_b:
                self._source_pix_b = None
            return False
        pix = self.provider.get(index, priority=100)
        if pix is not None:
            self._apply(label, pix, edge)
            return True
        label.setText(tr("view.loading"))
        return False

    def _start_spread_prefetch(self):
        anchor = self._prefetch_anchor
        if anchor is None:
            return
        self._prefetch_anchor = None
        self.provider.preload_around(anchor, radius=4, backward=1)

    def _on_pixmap(self, index, pix):
        if index == self.left_index:
            self._apply(self.label_a, pix, "right", animated=False)
            self._start_spread_prefetch()
        elif index == self.right_index:
            self._apply(self.label_b, pix, "left", animated=False)
            self._start_spread_prefetch()

    def _on_animation_pixmap(self, index, pix):
        if index == self.left_index:
            self._apply(self.label_a, pix, "right", animated=True)
        elif index == self.right_index:
            self._apply(self.label_b, pix, "left", animated=True)

    def _on_adjustments_changed(self):
        if self.left_index is not None:
            self._load(self.label_a, self.left_index, "right")
        if self.right_index is not None:
            self._load(self.label_b, self.right_index, "left")

    def _apply(self, label, pix, edge, animated=False, fast=False):
        if label is self.label_a:
            self._source_pix_a = pix
        elif label is self.label_b:
            self._source_pix_b = pix
        margin = 0 if (self._edge_to_edge or self.layout_mode == LAYOUT_FULL_WIDTH) else 20
        avail_h = max(self.viewport().height() - margin, 100)
        avail_w = max(self.viewport().width() // 2 - margin - self.gutter.width() // 2, 100)
        transform = Qt.FastTransformation if (animated or fast) else Qt.SmoothTransformation
        if self._manual_zoom_percent is not None:
            factor = self._manual_zoom_percent / 100.0
            target = QSize(max(1, int(pix.width() * factor)), max(1, int(pix.height() * factor)))
            scaled = pix.scaled(target, Qt.KeepAspectRatio, transform)
        elif self.layout_mode == LAYOUT_FULL_WIDTH:
            scaled = pix.scaledToWidth(avail_w, transform)
        else:
            scaled = pix.scaled(avail_w, avail_h, Qt.KeepAspectRatio, transform)
        if self.gutter.enabled:
            scaled = self._shade_spine_edge(scaled, edge)
        label.setPixmap(scaled)
        if self._manual_zoom_percent is not None:
            self._resize_manual_container()

    def _resize_manual_container(self):
        if self._manual_zoom_percent is None:
            return
        sizes = []
        for label in (self.label_a, self.label_b):
            pix = label.pixmap()
            if pix is not None and not pix.isNull():
                sizes.append(pix.size())
        if not sizes:
            return
        extra = 0 if self._edge_to_edge else 16
        width = sum(size.width() for size in sizes) + self.gutter.width() + extra
        height = max(size.height() for size in sizes) + extra
        self.widget().resize(max(width, self.viewport().width()), max(height, self.viewport().height()))

    def _rescale_for_zoom(self):
        if self.left_index is not None:
            self._load(self.label_a, self.left_index, "right")
        if self.right_index is not None:
            self._load(self.label_b, self.right_index, "left")
        if self._manual_zoom_percent is None:
            self.widget().adjustSize()

    def _zoom_reference_width(self):
        widths = []
        for label in (self.label_a, self.label_b):
            pix = label.pixmap()
            if pix is not None and not pix.isNull():
                widths.append(pix.width())
        return max(1, sum(widths) + (self.gutter.width() if widths else 0))

    def _effective_zoom_percent(self):
        pairs = ((self.label_a, self._source_pix_a), (self.label_b, self._source_pix_b))
        for label, source in pairs:
            shown = label.pixmap()
            if source is not None and not source.isNull() and shown is not None and not shown.isNull():
                return max(1, int(round(shown.width() * 100.0 / max(source.width(), 1))))
        return 100

    def _shade_spine_edge(self, pixmap, edge, width_px=42, max_alpha=70):
        """Aplica um sombreamento sutil e translúcido na borda da página que
        fica junto à lombada, para reforçar o efeito de encadernação."""
        if pixmap.isNull():
            return pixmap
        w = min(width_px, max(pixmap.width() // 3, 1))
        shaded = QPixmap(pixmap)
        painter = QPainter(shaded)
        grad = QLinearGradient()
        if edge == "right":
            grad.setStart(shaded.width() - w, 0)
            grad.setFinalStop(shaded.width(), 0)
            x = shaded.width() - w
        else:
            grad.setStart(w, 0)
            grad.setFinalStop(0, 0)
            x = 0
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(1.0, QColor(0, 0, 0, max_alpha))
        painter.fillRect(x, 0, w, shaded.height(), grad)
        painter.end()
        return shaded

    def magnifier_sample(self, viewport_pos):
        for label, source in (
            (self.label_a, self._source_pix_a),
            (self.label_b, self._source_pix_b),
        ):
            sample = _sample_label_pixmap(label, self.viewport(), viewport_pos, source)
            if sample is not None:
                return sample
        return None

    def retranslate_ui(self):
        for label, index in ((self.label_a, self.left_index), (self.label_b, self.right_index)):
            if index is not None and label.pixmap() is None:
                label.setText(tr("view.loading"))

    def _rescale_visible(self, fast=False):
        if self._source_pix_a is not None and not self._source_pix_a.isNull():
            self._apply(self.label_a, self._source_pix_a, "right", fast=fast)
        if self._source_pix_b is not None and not self._source_pix_b.isNull():
            self._apply(self.label_b, self._source_pix_b, "left", fast=fast)

    def _rescale_visible_smooth(self):
        self._rescale_visible(fast=False)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Não volta ao provider/cache a cada pixel de resize: reutiliza as duas
        # fontes já visíveis e só faz SmoothTransformation ao estabilizar.
        self._rescale_visible(fast=True)
        self._resize_quality_timer.start()


class ContinuousView(_ZoomPanMixin, QScrollArea):
    """Rolagem contínua vertical com virtualização para coleções gigantes.

    Até ``VIRTUAL_THRESHOLD`` páginas mantém o comportamento tradicional. Acima
    disso, apenas ``WINDOW_PAGES`` QLabel existem ao mesmo tempo. A janela
    lógica desliza conforme o usuário se aproxima das bordas, mantendo consumo
    de memória/UI praticamente constante mesmo com 100 mil imagens.
    """

    page_changed = Signal(int)
    # Criar centenas de QLabel de uma vez é caro no Windows. A virtualização
    # passa a entrar cedo; 36 páginas reais em torno da posição atual são
    # suficientes para scroll suave sem manter centenas/milhares de widgets.
    VIRTUAL_THRESHOLD = 240
    WINDOW_PAGES = 36
    WINDOW_SHIFT = 14

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.setWidgetResizable(True)
        self._init_zoom_pan()
        self.container = QWidget()
        self.vbox = QVBoxLayout(self.container)
        self.vbox.setSpacing(10)
        self.vbox.setContentsMargins(0, 10, 0, 10)
        self.vbox.setAlignment(Qt.AlignHCenter)
        self.setWidget(self.container)

        self.labels = []
        self.total = 0
        self.layout_mode = LAYOUT_CENTERED
        self._edge_to_edge = False
        self._virtual = False
        self._base_index = 0
        self._shifting = False
        self._pending_scroll_index = None
        self.provider.pixmap_ready.connect(self._on_pixmap)
        self.provider.animation_frame_ready.connect(self._on_animation_pixmap)
        self.provider.adjustments_changed.connect(self._on_adjustments_changed)
        self.verticalScrollBar().valueChanged.connect(self._debounce_update)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(35)
        self._timer.timeout.connect(self._update_visible)
        self._resize_quality_timer = QTimer(self)
        self._resize_quality_timer.setSingleShot(True)
        self._resize_quality_timer.setInterval(100)
        self._resize_quality_timer.timeout.connect(self._rescale_for_zoom)

    def _clear_labels(self):
        for lbl in self.labels:
            self.vbox.removeWidget(lbl)
            lbl.setParent(None)
            lbl.deleteLater()
        self.labels = []

    def _make_label(self, logical_index):
        lbl = QLabel(tr("view.page_number", number=logical_index + 1))
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setObjectName("pageLabel")
        lbl.setMinimumHeight(260)
        lbl.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        lbl.setProperty("pageIndex", int(logical_index))
        return lbl

    def build(self, total):
        self._clear_labels()
        self.total = max(0, int(total))
        self._virtual = self.total > self.VIRTUAL_THRESHOLD
        self._base_index = 0
        if self._virtual:
            self._build_window(0)
        else:
            for i in range(self.total):
                lbl = self._make_label(i)
                self.vbox.addWidget(lbl)
                self.labels.append(lbl)
        QTimer.singleShot(0, self._update_visible)

    def _window_contains(self, logical_index):
        return (
            self._base_index <= logical_index
            < self._base_index + len(self.labels)
        )

    def _local_row(self, logical_index):
        row = int(logical_index) - self._base_index
        return row if 0 <= row < len(self.labels) else None

    def _build_window(self, anchor_index, preserve_index=None):
        if self.total <= 0:
            return
        anchor_index = max(0, min(int(anchor_index), self.total - 1))
        max_start = max(0, self.total - self.WINDOW_PAGES)
        start = max(0, min(anchor_index - self.WINDOW_PAGES // 3, max_start))
        end = min(self.total, start + self.WINDOW_PAGES)

        self._shifting = True
        self._clear_labels()
        self._base_index = start
        for logical in range(start, end):
            lbl = self._make_label(logical)
            self.vbox.addWidget(lbl)
            self.labels.append(lbl)
        self._shifting = False

        target = anchor_index if preserve_index is None else int(preserve_index)
        self._pending_scroll_index = target
        QTimer.singleShot(0, self._finish_window_shift)

    def _finish_window_shift(self):
        target = self._pending_scroll_index
        self._pending_scroll_index = None
        if target is not None:
            row = self._local_row(target)
            if row is not None:
                self.ensureWidgetVisible(self.labels[row], 0, max(20, self.viewport().height() // 3))
        self._update_visible()

    def retranslate_ui(self):
        for local, lbl in enumerate(self.labels):
            if lbl.pixmap() is None:
                logical = self._base_index + local
                lbl.setText(tr("view.page_number", number=logical + 1))

    def set_layout_mode(self, mode):
        self.layout_mode = mode
        for local, lbl in enumerate(self.labels):
            if lbl.pixmap() is not None and not lbl.pixmap().isNull():
                logical = self._base_index + local
                pix = self.provider.get(logical)
                if pix is not None:
                    self._apply(logical, pix)

    def set_edge_to_edge(self, enabled):
        self._edge_to_edge = bool(enabled)
        gap = 0 if self._edge_to_edge else 10
        self.vbox.setSpacing(gap)
        self.vbox.setContentsMargins(0, gap, 0, gap)
        self.setContentsMargins(0, 0, 0, 0)
        self.setViewportMargins(0, 0, 0, 0)
        self._rescale_for_zoom()

    def set_background_color(self, color, checker=False):
        _apply_widget_background(self, color, checker)
        _apply_widget_background(self.viewport(), color, checker)
        _apply_widget_background(self.container, color, checker)

    def _debounce_update(self, _v):
        if not self._shifting:
            self._timer.start()

    def _target_width(self):
        inset = 0 if self._edge_to_edge else 24
        avail_w = max(self.viewport().width() - inset, 100)
        if self.layout_mode == LAYOUT_CENTERED:
            avail_w = min(avail_w, 1000)
        if self._manual_zoom_percent is not None:
            avail_w = int(avail_w * self._manual_zoom_percent / 100.0)
        return max(50, avail_w)

    def _apply_manual_zoom_state(self):
        # O container vertical precisa continuar acompanhando a largura do viewport.
        self.setWidgetResizable(True)

    def _rescale_for_zoom(self):
        for local, lbl in enumerate(self.labels):
            pix = lbl.pixmap()
            if pix is None or pix.isNull():
                continue
            logical = self._base_index + local
            source = self.provider.get(logical)
            if source is not None:
                self._apply(logical, source)
        self._timer.start()

    def _effective_zoom_percent(self):
        return 100

    def _maybe_shift_virtual_window(self, best_index):
        if not self._virtual or not self.labels or self._shifting:
            return False
        bar = self.verticalScrollBar()
        value, maximum = bar.value(), bar.maximum()
        edge = max(self.viewport().height(), 320)
        window_end = self._base_index + len(self.labels)

        if value >= max(0, maximum - edge) and window_end < self.total:
            anchor = min(self.total - 1, best_index + self.WINDOW_SHIFT)
            self._build_window(anchor, preserve_index=best_index)
            return True
        if value <= edge and self._base_index > 0:
            anchor = max(0, best_index - self.WINDOW_SHIFT)
            self._build_window(anchor, preserve_index=best_index)
            return True
        return False

    def _update_visible(self):
        if not self.labels:
            return
        vp_h = self.viewport().height()
        top = self.verticalScrollBar().value()
        buffer = vp_h
        center = top + vp_h // 2
        best_i = self._base_index
        best_d = float("inf")
        visible_indices = []

        for local, lbl in enumerate(self.labels):
            logical = self._base_index + local
            y = lbl.y()
            h = lbl.height()
            if y + h >= top - buffer and y <= top + vp_h + buffer:
                visible_indices.append(logical)
                if lbl.pixmap() is None or lbl.pixmap().isNull():
                    priority = max(30, 90 - abs((y + h // 2) - center) // 20)
                    pix = self.provider.get(logical, priority=priority)
                    if pix is not None:
                        self._apply(logical, pix)
            d = abs((y + h // 2) - center)
            if d < best_d:
                best_d, best_i = d, logical

        # Preload explícito segue a janela visível, não o tamanho total.
        if visible_indices:
            self.provider.preload_indices(visible_indices[:12], base_priority=35)
        self.page_changed.emit(best_i)
        self._maybe_shift_virtual_window(best_i)

    def _on_pixmap(self, index, pix):
        if self._window_contains(index):
            self._apply(index, pix, animated=False)

    def _on_animation_pixmap(self, index, pix):
        if self._window_contains(index):
            self._apply(index, pix, animated=True)

    def _on_adjustments_changed(self):
        for lbl in self.labels:
            lbl.clear()
        self._update_visible()

    def _apply(self, index, pix, animated=False):
        row = self._local_row(index)
        if row is None:
            return
        avail_w = self._target_width()
        transform = Qt.FastTransformation if animated else Qt.SmoothTransformation
        scaled = pix.scaledToWidth(avail_w, transform)
        lbl = self.labels[row]
        lbl.setPixmap(scaled)
        lbl.setFixedHeight(scaled.height())

    def magnifier_sample(self, viewport_pos):
        # childAt() mantém a consulta O(1) mesmo quando a coleção possui
        # dezenas de milhares de páginas. A lente usa o pixmap já escalado
        # do QLabel, portanto não prende páginas extras no cache de memória.
        container_pos = self.container.mapFromGlobal(
            self.viewport().mapToGlobal(viewport_pos)
        )
        child = self.container.childAt(container_pos)
        label = child if isinstance(child, QLabel) else None
        if label is None:
            return None
        return _sample_label_pixmap(label, self.viewport(), viewport_pos)

    def scroll_to(self, index):
        if self.total <= 0:
            return
        index = max(0, min(int(index), self.total - 1))
        if self._virtual and not self._window_contains(index):
            self._build_window(index, preserve_index=index)
        else:
            row = self._local_row(index)
            if row is not None:
                self.ensureWidgetVisible(self.labels[row], 0, 0)
                self.provider.get(index, priority=100)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._timer.start()
        self._resize_quality_timer.start()

