import os
import sys
import tempfile
import random
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer, QEvent, QFileSystemWatcher
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence, QImage
from PySide6.QtWidgets import (
    QMainWindow, QFileDialog, QLabel, QSlider, QDialog,
    QWidget, QHBoxLayout, QVBoxLayout, QStackedWidget, QDockWidget,
    QToolButton, QStatusBar, QApplication, QInputDialog
)

from app.format_defs import natural_key, SUPPORTED_FILE_EXTS, STANDALONE_IMAGE_EXTS, is_container_path
from app.windows_chrome import apply_edge_chrome
from app.settings import Settings
from app.version import APP_VERSION
from app.i18n import tr, set_locale, current_locale, available_locales
from app.file_watcher_utils import file_signature
from app.message_boxes import information as show_information, warning as show_warning, critical as show_critical, about as show_about_box, ask_yes_no

LAYOUT_CENTERED = "centered"
LAYOUT_FULL_WIDTH = "full_width"

MODE_SINGLE = "single"
MODE_CONTINUOUS = "continuous"
MODE_DOUBLE = "double"

DIR_LTR = "ltr"
DIR_RTL = "rtl"

BG_LIGHT = "light"
BG_DARK = "dark"
BG_CHECKER = "checker"
BG_COLORS = {BG_LIGHT: "#ffffff", BG_DARK: "#101014", BG_CHECKER: "#eeeeee"}

def file_filter():
    return ";;".join((
        tr("filter.supported"),
        tr("filter.images"),
        tr("filter.comics"),
        tr("filter.pdf"),
        tr("filter.epub"),
        tr("filter.containers"),
        tr("filter.all"),
    ))



class MainWindow(QMainWindow):
    def __init__(self, icon_path=None, res_dir=None, settings=None):
        super().__init__()
        # Permite soltar, a partir do Explorer/gerenciador de arquivos, qualquer
        # formato que o Quaint já sabe abrir pelo menu Arquivo.
        self.setAcceptDrops(True)
        self.setWindowTitle(tr("app.title"))
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.res_dir = res_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resources")
        self.icons_dir = os.path.join(self.res_dir, "icons")

        self.settings = settings if settings is not None else Settings()
        # O locale já é definido em main.py antes da criação da janela.
        # Evitar uma segunda leitura/parse dos arquivos de idioma no hot path.
        self.setWindowTitle(tr("app.title"))
        self.archive = None
        self.provider = None
        self.collection = None
        self.collection_index = None
        # Quando o item atual é uma imagem avulsa de uma coleção mista, o
        # painel de miniaturas mostra todas as imagens avulsas da coleção.
        # O mapa converte linha da miniatura -> índice real do membro.
        self._thumbnail_member_map = None
        self._thumbnail_member_row = None
        self._thumbnail_scope_archive = None
        self._progress_key = None
        self.current_index = 0
        self.mode = self.settings.get("mode", MODE_SINGLE)
        self.direction = self.settings.get("direction", DIR_LTR)
        self.fit_mode = self.settings.get("fit_mode", "page")
        self.layout_mode = self.settings.get("layout_mode", LAYOUT_CENTERED)
        self.double_shadow = self.settings.get_bool("double_shadow", True)
        self.page_background = self.settings.get("page_background", BG_LIGHT)
        if self.page_background not in BG_COLORS:
            self.page_background = BG_LIGHT
        self._adjustments_dialog = None
        self._summary_dialog = None
        self._epub_settings_dialog = None
        self._crop_dialog = None
        self._compare_dialog = None
        self._magnifier = None
        self._color_picker = None
        # Mantido somente em memória: ao fechar o programa, a pasta lembrada
        # pelo diálogo Salvar como é descartada automaticamente.
        self._session_save_dir = None
        self._clipboard_temp_files = []
        # Progresso é coalescido por alguns milissegundos para não escrever
        # QSettings/Registro do Windows em toda mudança rápida de página.
        self._pending_progress = None
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(700)

        # No modo contínuo, esperar a rolagem estabilizar evita iniciar um
        # decoder (ou processo FFmpeg) diferente a cada poucos pixels.
        self._animation_sync_timer = QTimer(self)
        self._animation_sync_timer.setSingleShot(True)
        self._animation_sync_timer.setInterval(180)
        self._animation_sync_timer.timeout.connect(self._sync_active_animations)
        self._progress_timer.timeout.connect(self._flush_progress)

        self._slideshow_timer = QTimer(self)
        self._slideshow_timer.timeout.connect(self._slideshow_tick)
        self._slideshow_interval_ms = int(self.settings.get("slideshow_interval_ms", 5000) or 5000)
        if self._slideshow_interval_ms not in (1000, 2000, 5000, 10000):
            self._slideshow_interval_ms = 5000
        self._slideshow_shuffle = self.settings.get_bool("slideshow_shuffle", False)
        self._slideshow_repeat = self.settings.get_bool("slideshow_repeat", True)

        # View -> Image-only mode: keep the previous state of optional UI
        # elements so pressing the shortcut again restores exactly what was
        # visible before hiding the interface.
        self._interface_hidden = False
        self._interface_prev_thumbs = False
        self._interface_prev_magnifier = False
        self._interface_prev_color_picker = False
        self._interface_prev_bookmarks = False
        self._interface_prev_favorites = False
        # Estado nativo da janela antes de entrar no modo somente imagem.
        # É usado para remover a barra de título sem transformar o modo em
        # tela cheia obrigatória e restaurar exatamente normal/maximizado/fullscreen.
        self._interface_prev_window_flags = None
        self._interface_prev_window_state = None
        self._interface_prev_window_geometry = None
        self._interface_entered_during_fullscreen = False
        self._fullscreen_prev_window_flags = None
        self._fullscreen_prev_window_state = None
        self._fullscreen_prev_window_geometry = None
        self._opened_root_path = None
        self._pending_open_root_path = None
        self._cursor_hide_timer = QTimer(self)
        self._cursor_hide_timer.setSingleShot(True)
        self._cursor_hide_timer.setInterval(1800)
        self._cursor_hide_timer.timeout.connect(self._hide_cursor_if_image_only)
        self._animation_speed = float(self.settings.get("animation_speed", 1.0) or 1.0)
        if self._animation_speed not in (0.5, 1.0, 2.0):
            self._animation_speed = 1.0

        # QFileSystemWatcher abre handles nativos. Ele só é necessário depois
        # que algum conteúdo real foi aberto, portanto fica lazy.
        self._fs_watcher = None
        self._fs_refresh_timer = None
        self._watched_folder = None
        self._watched_primary_signature = None
        self._watched_primary_file = None
        self._watch_folder_reload = False
        self._fs_changed_paths = set()
        # Navegar entre imagens/arquivos irmãos não deve revarrer a mesma pasta
        # a cada clique. O mtime do diretório invalida a lista quando entradas
        # são adicionadas/removidas/renomeadas.
        self._sibling_cache_folder = None
        self._sibling_cache_signature = None
        self._sibling_cache_names = None

        self._build_actions()
        self._build_ui()
        self._build_menu()
        self._restore_state()
        self._update_ui_enabled(False)
        self._apply_page_background()
        try:
            self.grabGesture(Qt.PinchGesture)
            self.grabGesture(Qt.SwipeGesture)
        except Exception:
            pass
        # O filtro global de eventos recebia todo mouse/key do aplicativo mesmo
        # quando Color Picker e modo Somente imagem estavam desligados. Ele é
        # instalado dinamicamente apenas enquanto um desses recursos precisa.
        self._global_event_filter_installed = False
        # Deixa o primeiro frame da janela ser pintado antes de reabrir uma
        # sessão pesada (PDF/CBZ/pasta grande). Isso melhora muito o tempo
        # percebido de inicialização sem alterar o conteúdo restaurado.
        QTimer.singleShot(90, self._maybe_restore_session)
        self._queue_native_window_edge_sync()

    # ---------------------------------------------------------------- UI --
    def _build_ui(self):
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.stack = QStackedWidget()
        self.provider_placeholder = None

        self.empty_label = QLabel(
            ""
        )
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setObjectName("emptyLabel")
        self.stack.addWidget(self.empty_label)

        outer.addWidget(self.stack, 1)

        # Barra inferior: navegação (botões com símbolos) + progresso
        bottom = QWidget()
        bottom.setObjectName("bottomBar")
        self.bottom_bar = bottom
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(10, 6, 10, 6)
        bl.setSpacing(4)

        self.btn_first = self._make_tool_button(self.act_first, "nav_first.png", "\u23EE")
        self.btn_prev = self._make_tool_button(self.act_prev, "nav_prev.png", "\u25C0")
        self.btn_next = self._make_tool_button(self.act_next, "nav_next.png", "\u25B6")
        self.btn_last = self._make_tool_button(self.act_last, "nav_last.png", "\u23ED")

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(0)
        self.slider.sliderMoved.connect(self._on_slider_moved)

        self.page_label = QLabel(tr("page.counter", current=0, total=0))
        self.page_label.setMinimumWidth(80)
        self.page_label.setAlignment(Qt.AlignCenter)

        bl.addWidget(self.btn_first)
        bl.addWidget(self.btn_prev)
        bl.addWidget(self.slider, 1)
        bl.addWidget(self.btn_next)
        bl.addWidget(self.btn_last)
        bl.addWidget(self.page_label)

        outer.addWidget(bottom)
        self.setCentralWidget(central)

        # Docks são baratos, mas seus painéis não: miniaturas/favoritos podem
        # criar providers, threads e decodificações. Instanciamos o conteúdo
        # somente na primeira vez em que o usuário realmente abre cada dock.
        self.thumb_panel = None
        self._thumbnail_scope_pending = None
        self._thumbnail_current_pending = 0
        self.thumb_dock = QDockWidget(tr("dock.thumbnails"), self)
        self.thumb_dock.setObjectName("thumbDock")
        self.thumb_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.RightDockWidgetArea, self.thumb_dock)
        self.thumb_dock.setVisible(False)
        self.thumb_dock.visibilityChanged.connect(self._on_thumb_visibility)

        self.bookmark_panel = None
        self.bookmark_dock = QDockWidget(tr("dock.bookmarks"), self)
        self.bookmark_dock.setObjectName("bookmarkDock")
        self.bookmark_dock.setMinimumWidth(222)
        self.bookmark_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.bookmark_dock)
        self.bookmark_dock.hide()
        self.bookmark_dock.visibilityChanged.connect(self.act_show_bookmarks.setChecked)

        self.favorite_panel = None
        self.favorite_dock = QDockWidget(tr("dock.favorites"), self)
        self.favorite_dock.setObjectName("favoriteDock")
        self.favorite_dock.setMinimumWidth(222)
        self.favorite_dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.favorite_dock)
        self.tabifyDockWidget(self.bookmark_dock, self.favorite_dock)
        self.favorite_dock.hide()
        self.favorite_dock.visibilityChanged.connect(self.act_show_favorites.setChecked)

        self.setStatusBar(QStatusBar())

    def _make_tool_button(self, action, icon_file, glyph, size=34):
        """Cria um botão de navegação vinculado a uma QAction, mantendo estado
        e atalho sincronizados. Usa os ícones PNG fornecidos; se o arquivo do
        ícone não for encontrado, cai de volta para um símbolo de texto."""
        btn = QToolButton()
        icon_path = os.path.join(self.icons_dir, icon_file) if icon_file else None
        if icon_path and os.path.exists(icon_path):
            btn.setIcon(QIcon(icon_path))
            btn.setIconSize(QSize(20, 20))
            btn.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            btn.setText(glyph)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            font = btn.font()
            font.setPointSize(max(font.pointSize() + 3, 13))
            btn.setFont(font)
        btn.setAutoRaise(True)
        btn.setFixedSize(size, size)
        def sync_button():
            btn.setEnabled(action.isEnabled())
            btn.setToolTip(
                f"{action.text()}  ({action.shortcut().toString()})"
                if not action.shortcut().isEmpty() else action.text()
            )
        sync_button()
        btn.clicked.connect(action.trigger)
        action.changed.connect(sync_button)
        return btn

    def _build_actions(self):
        # Ações do menu — sem ícones (apenas texto + atalho), a pedido do usuário.
        self.act_open_file = QAction(tr("action.open_file"), self)
        self.act_open_file.setShortcut(QKeySequence.Open)
        self.act_open_file.triggered.connect(self.open_file_dialog)

        self.act_open_folder = QAction(tr("action.open_folder"), self)
        self.act_open_folder.setShortcut(QKeySequence("Ctrl+Shift+O"))
        self.act_open_folder.triggered.connect(self.open_folder_dialog)

        self.act_reopen_last = QAction(tr("action.reopen_last"), self)
        self.act_reopen_last.setShortcut(QKeySequence("R"))
        self.act_reopen_last.triggered.connect(self.reopen_last_file)

        self.act_save_page = QAction(tr("action.save_page"), self)
        self.act_save_page.setShortcut(QKeySequence("Ctrl+S"))
        self.act_save_page.triggered.connect(self.save_current_page)

        self.act_save_changes = QAction(tr("action.save_changes"), self)
        self.act_save_changes.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.act_save_changes.triggered.connect(self.save_current_page_with_changes)

        self.act_batch_export = QAction(tr("action.batch_export"), self)
        self.act_batch_export.triggered.connect(self.open_batch_export)

        self.act_file_info = QAction(tr("action.file_info"), self)
        self.act_file_info.setShortcut(QKeySequence("Ctrl+I"))
        self.act_file_info.triggered.connect(self.show_file_info)

        self.act_reveal_file = QAction(tr("action.reveal_file"), self)
        self.act_reveal_file.triggered.connect(self.reveal_current_file)

        self.act_rename_file = QAction(tr("action.rename_file"), self)
        self.act_rename_file.triggered.connect(self.rename_current_file)

        self.act_move_file = QAction(tr("action.move_file"), self)
        self.act_move_file.triggered.connect(self.move_current_file)

        self.act_copy_file_to = QAction(tr("action.copy_file_to"), self)
        self.act_copy_file_to.triggered.connect(self.copy_current_file_to)

        self.act_delete_file = QAction(tr("action.delete_file"), self)
        self.act_delete_file.setShortcut(QKeySequence.Delete)
        self.act_delete_file.triggered.connect(self.delete_current_file)

        self.act_copy_image = QAction(tr("action.copy_image"), self)
        self.act_copy_image.setShortcut(QKeySequence.Copy)
        self.act_copy_image.triggered.connect(self.copy_current_image)

        self.act_paste_image = QAction(tr("action.paste_image"), self)
        self.act_paste_image.setShortcut(QKeySequence.Paste)
        self.act_paste_image.triggered.connect(self.paste_from_clipboard)

        self.act_copy_path = QAction(tr("action.copy_path"), self)
        self.act_copy_path.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self.act_copy_path.triggered.connect(self.copy_current_path)

        self.act_quit = QAction(tr("action.quit"), self)
        self.act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        self.act_quit.triggered.connect(self.close)

        self.act_prev = QAction(tr("action.prev_page"), self)
        self.act_prev.setShortcuts([
            QKeySequence(Qt.Key_Left),
            QKeySequence("<"),
            QKeySequence(Qt.Key_Backspace),
            QKeySequence(Qt.Key_PageUp),
        ])
        self.act_prev.triggered.connect(self.prev_page)

        self.act_next = QAction(tr("action.next_page"), self)
        self.act_next.setShortcuts([
            QKeySequence(Qt.Key_Right),
            QKeySequence(">"),
            QKeySequence(Qt.Key_Return),
            QKeySequence(Qt.Key_Enter),
            QKeySequence(Qt.Key_PageDown),
        ])
        self.act_next.triggered.connect(self.next_page)

        # Espaço é contextual: pausa/retoma uma animação visível; em uma
        # página estática mantém o comportamento histórico de avançar.
        self.act_space = QAction(self)
        self.act_space.setShortcut(QKeySequence(Qt.Key_Space))
        self.act_space.setShortcutContext(Qt.WindowShortcut)
        self.act_space.triggered.connect(self._handle_space)
        self.addAction(self.act_space)

        self.act_first = QAction(tr("action.first_page"), self)
        self.act_first.setShortcut(QKeySequence(Qt.Key_Home))
        self.act_first.triggered.connect(lambda: self.go_to_page(0))

        self.act_last = QAction(tr("action.last_page"), self)
        self.act_last.setShortcut(QKeySequence(Qt.Key_End))
        self.act_last.triggered.connect(lambda: self.go_to_page(self._page_count() - 1))

        # --- Exibir: tela cheia e miniaturas (agora só na guia Exibir, com atalho) ---
        self.act_fullscreen = QAction(tr("action.fullscreen"), self)
        self.act_fullscreen.setShortcut(QKeySequence(Qt.Key_F11))
        self.act_fullscreen.setCheckable(True)
        self.act_fullscreen.triggered.connect(self.toggle_fullscreen)

        self.act_image_only = QAction(tr("action.image_only_mode"), self)
        self.act_image_only.setShortcut(QKeySequence("Tab"))
        self.act_image_only.setShortcutContext(Qt.WindowShortcut)
        self.act_image_only.setCheckable(True)
        self.act_image_only.triggered.connect(self.toggle_interface_hidden)
        self.act_hide_cursor = QAction(tr("action.hide_cursor_image_only"), self)
        self.act_hide_cursor.setCheckable(True)
        self.act_hide_cursor.setChecked(self.settings.get_bool("hide_cursor_image_only", True))
        self.act_hide_cursor.triggered.connect(lambda checked: self.settings.set("hide_cursor_image_only", bool(checked)))
        # Keep the shortcut active even while the menu bar itself is hidden.
        self.addAction(self.act_image_only)

        self.act_thumbs = QAction(tr("action.show_thumbnails"), self)
        self.act_thumbs.setShortcut(QKeySequence("T"))
        self.act_thumbs.setCheckable(True)
        self.act_thumbs.triggered.connect(self._toggle_thumbs)

        # --- Exibir: sumário (lista de arquivos da pasta, clicável) ---
        self.act_summary = QAction(tr("action.summary"), self)
        self.act_summary.setShortcut(QKeySequence("I"))
        self.act_summary.triggered.connect(self.show_summary)

        self.act_toggle_bookmark = QAction(tr("action.toggle_bookmark"), self)
        self.act_toggle_bookmark.setShortcut(QKeySequence("B"))
        self.act_toggle_bookmark.triggered.connect(self.toggle_current_bookmark)

        self.act_show_bookmarks = QAction(tr("action.show_bookmarks"), self)
        self.act_show_bookmarks.setShortcut(QKeySequence("Ctrl+B"))
        self.act_show_bookmarks.setCheckable(True)
        self.act_show_bookmarks.triggered.connect(self._toggle_bookmarks_dock)

        self.act_toggle_favorite = QAction(tr("action.toggle_favorite"), self)
        self.act_toggle_favorite.setShortcut(QKeySequence("Ctrl+D"))
        self.act_toggle_favorite.triggered.connect(self.toggle_current_favorite)

        self.act_show_favorites = QAction(tr("action.show_favorites"), self)
        self.act_show_favorites.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.act_show_favorites.setCheckable(True)
        self.act_show_favorites.triggered.connect(self._toggle_favorites_dock)

        # --- Exibir: modos de leitura (grupo exclusivo) ---
        self.mode_group = QActionGroup(self)
        self.act_mode_single = QAction(tr("action.mode_single"), self)
        self.act_mode_single.setShortcut(QKeySequence("1"))
        self.act_mode_single.setCheckable(True)

        self.act_mode_continuous = QAction(tr("action.mode_continuous"), self)
        self.act_mode_continuous.setShortcut(QKeySequence("2"))
        self.act_mode_continuous.setCheckable(True)

        self.act_mode_double = QAction(tr("action.mode_double"), self)
        self.act_mode_double.setShortcut(QKeySequence("3"))
        self.act_mode_double.setCheckable(True)

        for a in (self.act_mode_single, self.act_mode_continuous, self.act_mode_double):
            self.mode_group.addAction(a)
        self.act_mode_single.triggered.connect(lambda: self.set_mode(MODE_SINGLE))
        self.act_mode_continuous.triggered.connect(lambda: self.set_mode(MODE_CONTINUOUS))
        self.act_mode_double.triggered.connect(lambda: self.set_mode(MODE_DOUBLE))

        self.act_direction = QAction(tr("action.direction", direction=tr("direction.western")), self)
        self.act_direction.setShortcut(QKeySequence("D"))
        self.act_direction.triggered.connect(self.toggle_direction)

        # --- Exibir: ajuste de página ---
        self.fit_group = QActionGroup(self)
        self.act_fit_width = QAction(tr("action.fit_width"), self)
        self.act_fit_width.setShortcut(QKeySequence("W"))
        self.act_fit_width.setCheckable(True)
        self.act_fit_width.triggered.connect(lambda: self._set_fit("width"))

        self.act_fit_height = QAction(tr("action.fit_height"), self)
        self.act_fit_height.setShortcut(QKeySequence("H"))
        self.act_fit_height.setCheckable(True)
        self.act_fit_height.triggered.connect(lambda: self._set_fit("height"))

        self.act_fit_page = QAction(tr("action.fit_page"), self)
        self.act_fit_page.setShortcut(QKeySequence("F"))
        self.act_fit_page.setCheckable(True)
        self.act_fit_page.triggered.connect(lambda: self._set_fit("page"))

        for a in (self.act_fit_width, self.act_fit_height, self.act_fit_page):
            self.fit_group.addAction(a)

        # --- Exibir: zoom manual e panorâmica ---
        self.act_zoom_in = QAction(tr("action.zoom_in"), self)
        self.act_zoom_in.setShortcut(QKeySequence.ZoomIn)
        self.act_zoom_in.triggered.connect(lambda: self._change_zoom("in"))

        self.act_zoom_out = QAction(tr("action.zoom_out"), self)
        self.act_zoom_out.setShortcut(QKeySequence.ZoomOut)
        self.act_zoom_out.triggered.connect(lambda: self._change_zoom("out"))

        self.act_zoom_100 = QAction(tr("action.zoom_100"), self)
        self.act_zoom_100.setShortcut(QKeySequence("Ctrl+0"))
        self.act_zoom_100.triggered.connect(lambda: self._change_zoom(100))

        self.act_zoom_200 = QAction(tr("action.zoom_200"), self)
        self.act_zoom_200.setShortcut(QKeySequence("Ctrl+Alt+2"))
        self.act_zoom_200.triggered.connect(lambda: self._change_zoom(200))

        # --- Exibir: layout da imagem (centralizada x largura total) ---
        self.layout_group = QActionGroup(self)
        self.act_layout_centered = QAction(tr("action.layout_centered"), self)
        self.act_layout_centered.setShortcut(QKeySequence("Ctrl+Alt+C"))
        self.act_layout_centered.setCheckable(True)
        self.act_layout_centered.triggered.connect(lambda: self.set_layout_mode(LAYOUT_CENTERED))

        self.act_layout_full_width = QAction(tr("action.layout_full_width"), self)
        self.act_layout_full_width.setShortcut(QKeySequence("Ctrl+Alt+L"))
        self.act_layout_full_width.setCheckable(True)
        self.act_layout_full_width.triggered.connect(lambda: self.set_layout_mode(LAYOUT_FULL_WIDTH))

        for a in (self.act_layout_centered, self.act_layout_full_width):
            self.layout_group.addAction(a)

        # --- Exibir: sombra de encadernação (páginas duplas) ---
        self.act_double_shadow = QAction(tr("action.double_shadow"), self)
        self.act_double_shadow.setCheckable(True)
        self.act_double_shadow.setShortcut(QKeySequence("Ctrl+Alt+S"))
        self.act_double_shadow.triggered.connect(self.set_double_shadow)

        # --- Exibir: fundo da área de leitura (claro/escuro) ---
        self.bg_group = QActionGroup(self)
        self.act_bg_light = QAction(tr("action.bg_light"), self)
        self.act_bg_light.setCheckable(True)
        self.act_bg_light.triggered.connect(lambda: self.set_page_background(BG_LIGHT))

        self.act_bg_dark = QAction(tr("action.bg_dark"), self)
        self.act_bg_dark.setCheckable(True)
        self.act_bg_dark.triggered.connect(lambda: self.set_page_background(BG_DARK))

        self.act_bg_checker = QAction(tr("action.bg_checker"), self)
        self.act_bg_checker.setCheckable(True)
        self.act_bg_checker.triggered.connect(lambda: self.set_page_background(BG_CHECKER))

        for a in (self.act_bg_light, self.act_bg_dark, self.act_bg_checker):
            self.bg_group.addAction(a)

        # --- Exibir: rotação e papel de parede ---
        self.act_rotate_left = QAction(tr("action.rotate_left"), self)
        self.act_rotate_left.triggered.connect(lambda: self.rotate_visible_pages(-90))

        self.act_rotate_right = QAction(tr("action.rotate_right"), self)
        self.act_rotate_right.triggered.connect(lambda: self.rotate_visible_pages(90))

        self.act_flip_horizontal = QAction(tr("action.flip_horizontal"), self)
        self.act_flip_horizontal.triggered.connect(lambda: self.flip_visible_pages(horizontal=True))

        self.act_flip_vertical = QAction(tr("action.flip_vertical"), self)
        self.act_flip_vertical.triggered.connect(lambda: self.flip_visible_pages(vertical=True))

        self.act_set_wallpaper = QAction(tr("action.wallpaper"), self)
        self.act_set_wallpaper.triggered.connect(self.set_current_as_wallpaper)

        # --- Ordenação de imagens em pastas ---
        self.folder_sort_group = QActionGroup(self)
        self.folder_sort_group.setExclusive(True)
        self.folder_sort_actions = {}
        for mode, key in (("name","action.sort_name"),("date","action.sort_date"),("size","action.sort_size"),("extension","action.sort_extension")):
            action = QAction(tr(key), self)
            action.setCheckable(True)
            action.setChecked(self.settings.folder_sort_mode() == mode)
            action.triggered.connect(lambda _checked=False, value=mode: self.set_folder_sort(value, self.settings.folder_sort_descending()))
            self.folder_sort_group.addAction(action)
            self.folder_sort_actions[mode] = action
        self.act_sort_desc = QAction(tr("action.sort_descending"), self)
        self.act_sort_desc.setCheckable(True)
        self.act_sort_desc.setChecked(self.settings.folder_sort_descending())
        self.act_sort_desc.triggered.connect(lambda checked: self.set_folder_sort(self.settings.folder_sort_mode(), checked))

        # --- Apresentação / slideshow ---
        self.act_slideshow_start = QAction(tr("action.slideshow_start"), self)
        self.act_slideshow_start.setShortcut(QKeySequence("F5"))
        self.act_slideshow_start.triggered.connect(self.start_slideshow)

        self.act_slideshow_pause = QAction(tr("action.slideshow_pause"), self)
        self.act_slideshow_pause.setShortcut(QKeySequence("Shift+F5"))
        self.act_slideshow_pause.setCheckable(True)
        self.act_slideshow_pause.triggered.connect(self.pause_slideshow)

        self.act_slideshow_stop = QAction(tr("action.slideshow_stop"), self)
        self.act_slideshow_stop.setShortcut(QKeySequence("Ctrl+F5"))
        self.act_slideshow_stop.triggered.connect(self.stop_slideshow)

        self.act_slideshow_shuffle = QAction(tr("action.slideshow_shuffle"), self)
        self.act_slideshow_shuffle.setCheckable(True)
        self.act_slideshow_shuffle.setChecked(self._slideshow_shuffle)
        self.act_slideshow_shuffle.triggered.connect(self.set_slideshow_shuffle)

        self.act_slideshow_repeat = QAction(tr("action.slideshow_repeat"), self)
        self.act_slideshow_repeat.setCheckable(True)
        self.act_slideshow_repeat.setChecked(self._slideshow_repeat)
        self.act_slideshow_repeat.triggered.connect(self.set_slideshow_repeat)

        self.slideshow_interval_group = QActionGroup(self)
        self.slideshow_interval_group.setExclusive(True)
        self.slideshow_interval_actions = {}
        for seconds in (1, 2, 5, 10):
            action = QAction(tr("action.slideshow_interval", seconds=seconds), self)
            action.setCheckable(True)
            action.setChecked(self._slideshow_interval_ms == seconds * 1000)
            action.triggered.connect(lambda _checked=False, sec=seconds: self.set_slideshow_interval(sec))
            self.slideshow_interval_group.addAction(action)
            self.slideshow_interval_actions[seconds] = action

        # --- Controles de animação ---
        self.animation_speed_group = QActionGroup(self)
        self.animation_speed_group.setExclusive(True)
        self.animation_speed_actions = {}
        for speed in (0.5, 1.0, 2.0):
            action = QAction(tr("action.animation_speed", speed=speed), self)
            action.setCheckable(True)
            action.setChecked(self._animation_speed == speed)
            action.triggered.connect(lambda _checked=False, value=speed: self.set_animation_speed(value))
            self.animation_speed_group.addAction(action)
            self.animation_speed_actions[speed] = action
        self.act_animation_prev_frame = QAction(tr("action.animation_prev_frame"), self)
        self.act_animation_prev_frame.setShortcut(QKeySequence("Alt+Left"))
        self.act_animation_prev_frame.triggered.connect(lambda: self.step_animation_frame(-1))
        self.act_animation_next_frame = QAction(tr("action.animation_next_frame"), self)
        self.act_animation_next_frame.setShortcut(QKeySequence("Alt+Right"))
        self.act_animation_next_frame.triggered.connect(lambda: self.step_animation_frame(1))
        self.act_save_animation_frame = QAction(tr("action.save_animation_frame"), self)
        self.act_save_animation_frame.setShortcut(QKeySequence("Ctrl+Alt+S"))
        self.act_save_animation_frame.triggered.connect(self.save_current_animation_frame)

        # --- Ferramentas ---
        self.act_magnifier = QAction(tr("action.magnifier"), self)
        self.act_magnifier.setShortcut(QKeySequence("M"))
        self.act_magnifier.setCheckable(True)
        self.act_magnifier.triggered.connect(self.toggle_magnifier)

        self.act_color_picker = QAction(tr("action.color_picker"), self)
        self.act_color_picker.setShortcut(QKeySequence("P"))
        self.act_color_picker.setCheckable(True)
        self.act_color_picker.triggered.connect(self.toggle_color_picker)

        self.act_crop = QAction(tr("action.crop"), self)
        self.act_crop.setShortcut(QKeySequence("Ctrl+Shift+X"))
        self.act_crop.triggered.connect(self.open_crop_tool)

        self.act_compare = QAction(tr("action.compare"), self)
        self.act_compare.setShortcut(QKeySequence("Ctrl+Shift+D"))
        self.act_compare.triggered.connect(self.open_compare_tool)

        self.act_adjustments = QAction(tr("action.adjustments"), self)
        self.act_adjustments.triggered.connect(self.open_adjustments_dialog)

        self.act_reset_adjustments = QAction(tr("action.reset_adjustments"), self)
        self.act_reset_adjustments.triggered.connect(self.reset_adjustments)

        self.act_shortcuts = QAction(tr("action.shortcuts"), self)
        self.act_shortcuts.triggered.connect(self.open_shortcuts_dialog)

        self.act_restore_session = QAction(tr("action.restore_session"), self)
        self.act_restore_session.setCheckable(True)
        self.act_restore_session.setChecked(self.settings.restore_session_enabled())
        self.act_restore_session.triggered.connect(self.settings.set_restore_session_enabled)

        self.act_epub_settings = QAction(tr("action.epub"), self)
        self.act_epub_settings.triggered.connect(self.open_epub_settings_dialog)

        self.act_register = QAction(tr("action.register_comics"), self)
        self.act_register.triggered.connect(self.register_file_types)

        self.act_register_epub = QAction(tr("action.register_epub"), self)
        self.act_register_epub.triggered.connect(self.register_epub_file_type)

        self.act_register_pdf = QAction(tr("action.register_pdf"), self)
        self.act_register_pdf.triggered.connect(self.register_pdf_file_type)

        self.act_register_images = QAction(tr("action.register_images"), self)
        self.act_register_images.triggered.connect(self.register_image_file_types)

        self.act_unregister = QAction(tr("action.unregister"), self)
        self.act_unregister.triggered.connect(self.unregister_file_types)

        self.act_language = QAction(tr("action.language"), self)
        self.act_language.triggered.connect(self.open_language_dialog)

        self.act_about = QAction(tr("action.about"), self)
        self.act_about.triggered.connect(self.show_about)

        self._apply_custom_shortcuts()
        # As QAction que vivem apenas dentro de menus podem perder o contexto
        # de atalho quando a barra de menu é ocultada. Associá-las também à
        # QMainWindow mantém todos os atalhos ativos no modo somente imagem.
        self._ensure_window_shortcut_actions()

    def _build_menu(self):
        mb = self.menuBar()

        self.menu_file = mb.addMenu(tr("menu.file"))
        self.menu_file.addAction(self.act_open_file)
        self.menu_file.addAction(self.act_open_folder)
        self.menu_file.addAction(self.act_reopen_last)
        self.recent_menu = self.menu_file.addMenu(tr("menu.recent"))
        self._refresh_recent_menu()
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_file_info)
        self.menu_file.addAction(self.act_reveal_file)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_rename_file)
        self.menu_file.addAction(self.act_move_file)
        self.menu_file.addAction(self.act_copy_file_to)
        self.menu_file.addAction(self.act_delete_file)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_save_page)
        self.menu_file.addAction(self.act_save_changes)
        self.menu_file.addAction(self.act_batch_export)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_direction)
        self.menu_file.addSeparator()
        self.menu_file.addAction(self.act_quit)

        self.menu_edit = mb.addMenu(tr("menu.edit"))
        self.menu_edit.addAction(self.act_copy_image)
        self.menu_edit.addAction(self.act_paste_image)
        self.menu_edit.addSeparator()
        self.menu_edit.addAction(self.act_copy_path)

        self.menu_nav = mb.addMenu(tr("menu.navigate"))
        self.menu_nav.addAction(self.act_prev)
        self.menu_nav.addAction(self.act_next)
        self.menu_nav.addAction(self.act_first)
        self.menu_nav.addAction(self.act_last)
        self.menu_nav.addSeparator()
        self.menu_nav.addAction(self.act_toggle_bookmark)
        self.menu_nav.addAction(self.act_show_bookmarks)
        self.menu_nav.addSeparator()
        self.menu_nav.addAction(self.act_toggle_favorite)
        self.menu_nav.addAction(self.act_show_favorites)

        self.menu_view = mb.addMenu(tr("menu.view"))
        self.menu_view.addAction(self.act_mode_single)
        self.menu_view.addAction(self.act_mode_continuous)
        self.menu_view.addAction(self.act_mode_double)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_fit_width)
        self.menu_view.addAction(self.act_fit_height)
        self.menu_view.addAction(self.act_fit_page)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_zoom_in)
        self.menu_view.addAction(self.act_zoom_out)
        self.menu_view.addAction(self.act_zoom_100)
        self.menu_view.addAction(self.act_zoom_200)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_layout_centered)
        self.menu_view.addAction(self.act_layout_full_width)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_double_shadow)
        self.menu_view.addSeparator()
        self.menu_bg = self.menu_view.addMenu(tr("menu.page_background"))
        self.menu_bg.addAction(self.act_bg_light)
        self.menu_bg.addAction(self.act_bg_dark)
        self.menu_bg.addAction(self.act_bg_checker)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_rotate_left)
        self.menu_view.addAction(self.act_rotate_right)
        self.menu_view.addAction(self.act_flip_horizontal)
        self.menu_view.addAction(self.act_flip_vertical)
        self.menu_view.addAction(self.act_set_wallpaper)
        self.menu_view.addSeparator()
        self.menu_view.addAction(self.act_thumbs)
        self.menu_view.addAction(self.act_summary)
        self.menu_view.addAction(self.act_fullscreen)
        self.menu_view.addAction(self.act_image_only)
        self.menu_view.addAction(self.act_hide_cursor)
        self.menu_view.addSeparator()
        self.menu_sort = self.menu_view.addMenu(tr("menu.sort"))
        for mode in ("name", "date", "size", "extension"):
            self.menu_sort.addAction(self.folder_sort_actions[mode])
        self.menu_sort.addSeparator()
        self.menu_sort.addAction(self.act_sort_desc)
        self.menu_view.addSeparator()
        self.menu_slideshow = self.menu_view.addMenu(tr("menu.slideshow"))
        self.menu_slideshow.addAction(self.act_slideshow_start)
        self.menu_slideshow.addAction(self.act_slideshow_pause)
        self.menu_slideshow.addAction(self.act_slideshow_stop)
        self.menu_slideshow.addSeparator()
        self.menu_slideshow_interval = self.menu_slideshow.addMenu(tr("menu.slideshow_interval"))
        for seconds in (1, 2, 5, 10):
            self.menu_slideshow_interval.addAction(self.slideshow_interval_actions[seconds])
        self.menu_slideshow.addSeparator()
        self.menu_slideshow.addAction(self.act_slideshow_shuffle)
        self.menu_slideshow.addAction(self.act_slideshow_repeat)

        self.menu_tools = mb.addMenu(tr("menu.tools"))
        self.menu_tools.addAction(self.act_magnifier)
        self.menu_tools.addAction(self.act_color_picker)
        self.menu_tools.addAction(self.act_crop)
        self.menu_tools.addAction(self.act_compare)
        self.menu_tools.addSeparator()
        self.menu_tools.addAction(self.act_adjustments)
        self.menu_tools.addAction(self.act_reset_adjustments)
        self.menu_tools.addSeparator()
        self.menu_animation = self.menu_tools.addMenu(tr("menu.animation"))
        for speed in (0.5, 1.0, 2.0): self.menu_animation.addAction(self.animation_speed_actions[speed])
        self.menu_animation.addSeparator()
        self.menu_animation.addAction(self.act_animation_prev_frame)
        self.menu_animation.addAction(self.act_animation_next_frame)
        self.menu_animation.addAction(self.act_save_animation_frame)
        self.menu_tools.addSeparator()
        self.menu_tools.addAction(self.act_shortcuts)
        self.menu_tools.addAction(self.act_restore_session)
        self.menu_tools.addSeparator()
        self.menu_tools.addAction(self.act_epub_settings)
        self.menu_tools.addSeparator()
        self.menu_assoc = self.menu_tools.addMenu(tr("menu.file_association"))
        self.menu_assoc.addAction(self.act_register)
        self.menu_assoc.addAction(self.act_register_epub)
        self.menu_assoc.addAction(self.act_register_pdf)
        self.menu_assoc.addAction(self.act_register_images)
        self.menu_assoc.addSeparator()
        self.menu_assoc.addAction(self.act_unregister)

        self.menu_help = mb.addMenu(tr("menu.help"))
        self.menu_help.addAction(self.act_language)
        self.menu_help.addSeparator()
        self.menu_help.addAction(self.act_about)

    def _shutdown_loaders(self):
        """Interrompe pré-carregamentos antigos antes de trocar/fechar o arquivo."""
        self._animation_sync_timer.stop()
        if self.bookmark_panel is not None:
            try:
                self.bookmark_panel.clear_source()
            except Exception:
                pass
        if self.provider is not None:
            try:
                self.provider.shutdown()
            except Exception:
                pass
            self.provider = None
        if self.thumb_panel is not None:
            try:
                self.thumb_panel.clear_provider()
            except Exception:
                pass

    # ------------------------------------------------------------ Abrir --
    def open_file_dialog(self):
        initial_dir = self.settings.last_open_directory()
        path, _ = QFileDialog.getOpenFileName(
            self, tr("dialog.open_file"), initial_dir, file_filter()
        )
        if path:
            self.settings.set_last_open_directory(path)
            self.open_path(path)

    def open_folder_dialog(self):
        initial_dir = self.settings.last_open_directory()
        path = QFileDialog.getExistingDirectory(
            self, tr("dialog.open_folder"), initial_dir
        )
        if path:
            self.settings.set_last_open_directory(path)
            self.open_path(path)

    @staticmethod
    def _is_supported_drop_path(path):
        """Retorna True somente para caminhos que a janela consegue abrir."""
        try:
            path_obj = Path(path)
        except (TypeError, ValueError, OSError):
            return False
        if not path_obj.exists():
            return False
        if path_obj.is_dir():
            return True
        return is_container_path(path_obj) or path_obj.suffix.lower() in SUPPORTED_FILE_EXTS

    def _supported_paths_from_drop(self, mime_data):
        """Extrai URLs locais suportadas de um arrastar-e-soltar do sistema."""
        if mime_data is None or not mime_data.hasUrls():
            return []
        paths = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if self._is_supported_drop_path(path):
                paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        if self._supported_paths_from_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if self._supported_paths_from_drop(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self._supported_paths_from_drop(event.mimeData())
        if not paths:
            event.ignore()
            return

        # O leitor exibe um documento/coleção por janela. Quando o Explorer
        # entrega vários itens de uma vez, abre o primeiro caminho suportado
        # na mesma ordem em que foi fornecido.
        path = paths[0]
        self.settings.set_last_open_directory(path)
        self.open_path(path)
        event.acceptProposedAction()

    def open_path(self, path):
        from app.archive import ArchiveError
        from app.compressed_collection import DirectoryComicCollection
        path_obj = Path(path)
        # Só promove este caminho a "item aberto" depois que a ativação tiver
        # sucesso. Assim uma tentativa inválida não altera o alvo de Favoritos.
        self._pending_open_root_path = str(path_obj)
        # Uma pasta com mistura de imagens e arquivos compactados vira uma
        # coleção. Pastas contendo somente imagens continuam sendo abertas
        # como um único conjunto de páginas, preservando o comportamento antigo.
        if path_obj.is_dir():
            try:
                folder_collection = DirectoryComicCollection(path_obj)
            except ArchiveError as e:
                show_warning(self, tr("dialog.open_error"), str(e))
                return
            if folder_collection.has_non_image_members():
                self._open_directory_collection(folder_collection)
            else:
                # A DirectoryComicCollection já fez scandir + ordenação.
                # Reaproveita os nomes para não varrer a pasta inteira de novo.
                pages = folder_collection.image_page_names()
                folder_collection.close()
                self._open_regular_archive(path_obj, directory_pages=pages)
            return

        # ZIP/RAR/7Z/TAR top-level funcionam como pasta virtual mista.
        if is_container_path(path_obj):
            self._open_compressed_collection(path_obj)
            return

        self._open_regular_archive(path_obj)

    def _open_directory_collection(self, new_collection):
        """Ativa uma pasta mista já indexada como coleção."""
        if self.archive:
            self._save_progress(immediate=True)
            self._shutdown_loaders()
            self.archive.close()
            self.archive = None
        self._close_collection()
        self.collection = new_collection
        self.collection_index = 0
        self.settings.add_recent(str(self.collection.path))
        self._refresh_recent_menu()
        self._open_collection_member(0)

    def _open_compressed_collection(self, path):
        from app.archive import ArchiveError
        from app.compressed_collection import CompressedComicCollection
        try:
            new_collection = CompressedComicCollection(path)
        except ArchiveError as e:
            show_warning(self, tr("dialog.open_error"), str(e))
            return
        except Exception as e:  # noqa: BLE001
            show_critical(self, tr("dialog.unexpected_error"), str(e))
            return

        if self.archive:
            self._save_progress(immediate=True)
            self._shutdown_loaders()
            self.archive.close()
            self.archive = None
        self._close_collection()
        self.collection = new_collection
        self.collection_index = 0

        self.settings.add_recent(str(self.collection.path))
        self._refresh_recent_menu()
        self._open_collection_member(0)

    def _open_regular_archive(self, path, add_recent=True, directory_pages=None):
        from app.archive import ComicArchive, ArchiveError
        try:
            new_archive = ComicArchive(path, directory_pages=directory_pages)
        except ArchiveError as e:
            show_warning(self, tr("dialog.open_error"), str(e))
            return
        except Exception as e:  # noqa: BLE001
            show_critical(self, tr("dialog.unexpected_error"), str(e))
            return

        if self.archive:
            self._save_progress(immediate=True)
            self._shutdown_loaders()
            self.archive.close()
            self.archive = None
        self._close_collection()

        self._progress_key = str(new_archive.path)
        self._activate_archive(new_archive, add_recent=add_recent)

    def _open_collection_member(self, index, go_to_last=False):
        from app.archive import ComicArchive, ArchiveError
        from app.compressed_collection import ContainerImageArchive
        if self.collection is None:
            return
        index = max(0, min(int(index), self.collection.count() - 1))
        previous_index = self.collection_index
        try:
            member_kind = self.collection.member_kind(index)
            member_path = self.collection.member_path(index)
            if member_kind == "archive":
                new_archive = ContainerImageArchive(member_path)
            else:
                new_archive = ComicArchive(member_path)
        except ArchiveError as e:
            show_warning(self, tr("dialog.open_error"), str(e))
            return
        except Exception as e:  # noqa: BLE001
            show_critical(self, tr("dialog.unexpected_error"), str(e))
            return

        if self.archive:
            self._save_progress(immediate=True)
            self._shutdown_loaders()
            self.archive.close()

        # A partir daqui o membro anterior já não está em uso por nenhum
        # decoder. Imagens extraídas do compactado podem ser descartadas para
        # que navegar por milhares de arquivos não faça a pasta temporária
        # crescer indefinidamente. CBZ/CBR continuam em cache como antes.
        self.collection.set_active_member(index)
        if previous_index is not None and previous_index != index:
            self.collection.discard_member(previous_index)

        self.collection_index = index
        self._progress_key = self.collection.progress_key(index)
        # O nome lógico é o nome do arquivo dentro do compactado, não o
        # caminho temporário usado internamente para abri-lo.
        new_archive.display_name_override = self.collection.member_display_name(index)
        new_archive.source_container_path = self.collection.path
        new_archive.source_member_name = self.collection.member_name(index)
        new_archive.read_only_override = True
        self._activate_archive(new_archive, add_recent=False, go_to_last=go_to_last)

    def _activate_archive(self, new_archive, add_recent=False, go_to_last=False):
        from app.pixmap_provider import PixmapProvider
        self.archive = new_archive
        if self._pending_open_root_path:
            self._opened_root_path = self._pending_open_root_path
            self._pending_open_root_path = None
        self._sort_archive_pages(self.archive)
        self.provider = PixmapProvider(self.archive)
        self.provider.set_adjustments(*self.settings.get_adjustments())
        self.provider.set_filter(self.settings.image_filter())
        self.provider.set_animation_speed(self._animation_speed)
        self.current_index = 0

        self._rebuild_views()
        self._prepare_thumbnail_scope()
        self._refresh_bookmarks()

        self.slider.setMaximum(max(self.archive.count() - 1, 0))
        self._update_ui_enabled(True)
        mixed_epub = self.archive.has_text_pages()
        single_image = self.archive.kind == "image"
        for action in (self.act_mode_continuous, self.act_mode_double):
            action.setEnabled(not (mixed_epub or single_image))
            if mixed_epub:
                action.setToolTip(tr("tooltip.epub_text_single"))
            elif single_image:
                action.setToolTip(tr("tooltip.standalone_single"))
            else:
                action.setToolTip("")
        self.act_mode_single.setEnabled(True)

        if add_recent:
            self.settings.add_recent(str(self.archive.path))
            self._refresh_recent_menu()

        if self.collection is not None:
            title = (
                f"{self.collection.display_name()} — "
                f"{self.collection.member_display_name(self.collection_index)}"
            )
            self.setWindowTitle(f"{tr('app.title')} — {title}")
            self.statusBar().showMessage(
                tr("status.collection_member",
                   name=self.collection.member_display_name(self.collection_index),
                   index=self.collection_index + 1, total=self.collection.count(),
                   pages=self.archive.count()), 5000
            )
        else:
            self.setWindowTitle(f"{tr('app.title')} — {self.archive.display_name()}")
            unit = tr("unit.reading_items") if self.archive.has_text_pages() else tr("unit.pages")
            self.statusBar().showMessage(
                tr("status.archive_opened", name=self.archive.display_name(), count=self.archive.count(), unit=unit), 5000
            )

        if go_to_last:
            start_index = self.archive.count() - 1
        else:
            start_index = self.settings.get_progress(self._progress_key or str(self.archive.path))
            start_index = max(0, min(start_index, self.archive.count() - 1))
        self.go_to_page(start_index)
        self._watch_current_sources()

    def _close_collection(self):
        if self.collection is not None:
            self.collection.close()
            self.collection = None
            self.collection_index = None
        self._thumbnail_member_map = None
        self._thumbnail_member_row = None
        self._thumbnail_scope_archive = None

    def reopen_last_file(self):
        """Atalho 'R': reabre o último arquivo lido. Se já houver um arquivo
        aberto, pula-o e vai para o anterior a ele na lista de recentes
        (já que o próprio arquivo atual fica em 1º lugar assim que é aberto)."""
        current_path = (
            str(self.collection.path) if self.collection is not None
            else (str(self.archive.path) if self.archive else None)
        )
        target = None
        for f in self.settings.recent_files():
            if f != current_path:
                target = f
                break
        if target is None:
            self.statusBar().showMessage(tr("status.no_recent"), 4000)
            return
        self.open_path(target)

    def _refresh_recent_menu(self):
        self.recent_menu.clear()
        files = self.settings.recent_files()
        if not files:
            act = self.recent_menu.addAction(tr("recent.empty"))
            act.setEnabled(False)
        else:
            for f in files:
                act = self.recent_menu.addAction(f)
                act.triggered.connect(lambda checked=False, p=f: self.open_path(p))

        self.recent_menu.addSeparator()
        act_clear = self.recent_menu.addAction(tr("recent.clear"))
        act_clear.setEnabled(bool(files))
        act_clear.triggered.connect(self.clear_recent_files)

    def _ask_yes_no(self, title, message):
        return ask_yes_no(self, title, message, default_no=True)

    def clear_recent_files(self):
        if not self._ask_yes_no(tr("dialog.clear_history"), tr("recent.confirm_clear")):
            return
        self.settings.clear_recent()
        self._refresh_recent_menu()

    # -------------------------------------------- Alterações externas --
    def _ensure_file_watcher(self):
        """Cria watcher/timer somente depois que há uma fonte real aberta.

        QFileSystemWatcher mantém handles nativos e sinais ativos; não há
        motivo para pagar esse custo na tela vazia do aplicativo.
        """
        if self._fs_watcher is None:
            watcher = QFileSystemWatcher(self)
            watcher.fileChanged.connect(self._schedule_file_system_refresh)
            watcher.directoryChanged.connect(self._schedule_file_system_refresh)
            self._fs_watcher = watcher
        if self._fs_refresh_timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(220)
            timer.timeout.connect(self._refresh_external_changes)
            self._fs_refresh_timer = timer
        return self._fs_watcher

    def _clear_file_watches(self):
        watcher = self._fs_watcher
        if watcher is not None:
            files = watcher.files()
            dirs = watcher.directories()
            if files:
                watcher.removePaths(files)
            if dirs:
                watcher.removePaths(dirs)
        self._watched_folder = None
        self._watched_primary_file = None
        self._watched_primary_signature = None
        self._watch_folder_reload = False
        self._fs_changed_paths.clear()

    @staticmethod
    def _is_directory_collection(collection):
        if collection is None:
            return False
        # A classe só é importada quando alguma coleção já foi aberta; não
        # puxa archive/Pillow no startup vazio.
        from app.compressed_collection import DirectoryComicCollection
        return isinstance(collection, DirectoryComicCollection)

    def _watch_current_sources(self):
        """Arma watchers sem varrer toda a pasta no caminho de abertura.

        QFileSystemWatcher já sinaliza alterações. Para um arquivo individual
        basta comparar seu stat O(1); para pastas/coleções, uma notificação do
        diretório é rara e então reindexamos sob demanda. Isso remove um segundo
        scandir+stat de todos os itens logo após abrir uma pasta grande.
        """
        self._clear_file_watches()
        if not self.archive:
            return
        watcher = self._ensure_file_watcher()
        source = self._current_source_path()
        if self.collection is not None:
            root = Path(self.collection.path)
            folder = root if root.is_dir() else root.parent
            primary = root if root.is_file() else None
            folder_reload = self._is_directory_collection(self.collection)
        elif self.archive.kind == "dir":
            folder = Path(self.archive.path)
            primary = None
            folder_reload = True
        else:
            root = Path(source or self.archive.path)
            folder = root.parent
            primary = root if root.is_file() else None
            folder_reload = False

        if folder.exists() and folder.is_dir():
            watcher.addPath(str(folder))
            self._watched_folder = folder
        self._watch_folder_reload = bool(folder_reload)
        if primary is not None and primary.exists():
            watcher.addPath(str(primary))
            self._watched_primary_file = primary
            self._watched_primary_signature = file_signature(primary)

    def _schedule_file_system_refresh(self, path=None):
        if path:
            self._fs_changed_paths.add(str(path))
        if self._fs_refresh_timer is not None:
            self._fs_refresh_timer.start()

    def _refresh_external_changes(self):
        if not self.archive:
            return
        changed_paths = set(self._fs_changed_paths)
        self._fs_changed_paths.clear()
        current_source = self._current_source_path()
        current_name = current_source.name if current_source is not None else None
        current_index = self.current_index
        summary_visible = bool(self._summary_dialog is not None and self._summary_dialog.isVisible())

        # Pastas e coleções de pasta só são reindexadas quando o sistema de
        # arquivos realmente sinaliza o diretório; não há snapshot inicial.
        if self._watch_folder_reload:
            reopen_folder = (
                Path(self.collection.path)
                if self._is_directory_collection(self.collection)
                else Path(self.archive.path)
            )
            self.open_path(str(reopen_folder))
            if self.archive:
                if current_name:
                    if self.archive.kind == "dir":
                        for i, name in enumerate(self.archive.pages):
                            if Path(name).name == current_name:
                                self.go_to_page(i)
                                break
                    elif self._is_directory_collection(self.collection):
                        for i in range(self.collection.count()):
                            if self.collection.member_name(i) == current_name:
                                self._open_collection_member(i)
                                break
                elif self._page_count():
                    self.go_to_page(min(current_index, self._page_count() - 1))
            if summary_visible:
                self.show_summary()
            self.statusBar().showMessage(tr("status.external_changes_reloaded"), 2200)
            return

        # Arquivos individuais: mudanças em irmãos não exigem varrer a pasta.
        primary = self._watched_primary_file
        if primary is not None:
            new_signature = file_signature(primary)
            if new_signature != self._watched_primary_signature:
                if new_signature is not None:
                    index = current_index
                    self.open_path(str(primary))
                    if self.archive and self._page_count():
                        self.go_to_page(min(index, self._page_count() - 1))
                    self.statusBar().showMessage(tr("status.external_file_reloaded"), 2200)
                else:
                    self._reset_to_empty()
                return
            self._watched_primary_signature = new_signature
            # Em substituições atômicas o watcher do arquivo pode ser removido;
            # rearma sem recriar watchers da pasta inteira.
            watcher = self._fs_watcher
            if new_signature is not None and watcher is not None and str(primary) not in watcher.files():
                try:
                    watcher.addPath(str(primary))
                except Exception:
                    pass

        if summary_visible and changed_paths:
            self.show_summary()

    # ------------------------------------------------------------ Views --
    def _rebuild_views(self):
        # Views e overlays são o maior bloco de widgets do programa. Deferir
        # seus imports até existir conteúdo elimina dezenas de classes do
        # caminho de inicialização com a janela vazia.
        from app.views import SinglePageView

        if self._color_picker is not None:
            try:
                self._color_picker.set_enabled(False)
                self._color_picker.panel.deleteLater()
                self._color_picker.deleteLater()
            except RuntimeError:
                pass
            self._color_picker = None
            self._update_global_event_filter()

        if self._magnifier is not None:
            try:
                self._magnifier.set_enabled(False)
                self._magnifier.deleteLater()
            except RuntimeError:
                pass
            self._magnifier = None

        while self.stack.count() > 1:
            w = self.stack.widget(1)
            self.stack.removeWidget(w)
            w.deleteLater()

        for name in ("single_view", "continuous_view", "double_view", "epub_text_view"):
            if hasattr(self, name):
                try:
                    delattr(self, name)
                except Exception:
                    pass

        self.single_view = SinglePageView(self.provider)
        self.single_view.set_fit_mode(self.fit_mode)
        self.single_view.set_layout_mode(self.layout_mode)

        self.stack.addWidget(self.single_view)
        self._connect_overlay_refresh_to_view(self.single_view)
        self._apply_mode_widget()
        self._apply_page_background()

    def _connect_overlay_refresh_to_view(self, view):
        """Conecta refresh de overlay apenas depois que algum overlay existe.

        Scroll é um hot path. Na leitura comum não há motivo para atravessar
        um callback Python em cada pixel rolado só para descobrir que Lente e
        Color Picker nunca foram abertos.
        """
        if self._magnifier is None and self._color_picker is None:
            return
        if bool(view.property("quaintOverlayRefreshConnected")):
            return
        try:
            view.verticalScrollBar().valueChanged.connect(self._on_magnifier_content_changed)
            view.horizontalScrollBar().valueChanged.connect(self._on_magnifier_content_changed)
            view.setProperty("quaintOverlayRefreshConnected", True)
        except Exception:
            pass

    def _connect_overlay_refresh_to_existing_views(self):
        for name in ("single_view", "continuous_view", "double_view"):
            view = getattr(self, name, None)
            if view is not None:
                self._connect_overlay_refresh_to_view(view)

    def _ensure_continuous_view(self):
        view = getattr(self, "continuous_view", None)
        if view is not None:
            return view
        from app.views import ContinuousView
        view = ContinuousView(self.provider)
        view.page_changed.connect(self._on_continuous_scroll)
        view.set_layout_mode(self.layout_mode)
        self.continuous_view = view
        self.stack.addWidget(view)
        self._connect_overlay_refresh_to_view(view)
        color = BG_COLORS.get(self.page_background, BG_COLORS[BG_LIGHT])
        view.set_background_color(color, checker=self.page_background == BG_CHECKER)
        view.set_edge_to_edge(self._interface_hidden or self.isFullScreen())
        return view

    def _ensure_double_view(self):
        view = getattr(self, "double_view", None)
        if view is not None:
            return view
        from app.views import DoublePageView
        view = DoublePageView(self.provider)
        view.set_direction(self.direction)
        view.set_shadow_enabled(self.double_shadow)
        view.set_layout_mode(self.layout_mode)
        self.double_view = view
        self.stack.addWidget(view)
        self._connect_overlay_refresh_to_view(view)
        color = BG_COLORS.get(self.page_background, BG_COLORS[BG_LIGHT])
        view.set_background_color(color, checker=self.page_background == BG_CHECKER)
        view.set_edge_to_edge(self._interface_hidden or self.isFullScreen())
        return view

    def _apply_mode_widget(self):
        # EPUB com capítulos textuais alterna entre texto refluível e página
        # única para imagens. Modos contínuo/duplo continuam integrais em
        # EPUBs somente de imagens.
        if self.archive and (self.archive.has_text_pages() or self.archive.kind == "image"):
            self.stack.setCurrentWidget(self.single_view)
            self.act_mode_single.setChecked(True)
            return
        if self.mode == MODE_SINGLE:
            self.stack.setCurrentWidget(self.single_view)
            self.act_mode_single.setChecked(True)
        elif self.mode == MODE_CONTINUOUS:
            view = self._ensure_continuous_view()
            if view.total != self.archive.count():
                view.build(self.archive.count())
            self.stack.setCurrentWidget(view)
            self.act_mode_continuous.setChecked(True)
        else:
            self.stack.setCurrentWidget(self._ensure_double_view())
            self.act_mode_double.setChecked(True)

    def set_mode(self, mode):
        if self.archive and self.archive.kind == "image" and mode != MODE_SINGLE:
            self.act_mode_single.setChecked(True)
            self.statusBar().showMessage(
                tr("status.image_single_only"), 3500
            )
            return
        self.mode = mode
        self.settings.set("mode", mode)
        if not self.archive:
            return
        self._apply_mode_widget()
        self.go_to_page(self.current_index)

    def toggle_direction(self):
        self.direction = DIR_RTL if self.direction == DIR_LTR else DIR_LTR
        self.settings.set("direction", self.direction)
        label = tr("direction.manga") if self.direction == DIR_RTL else tr("direction.western")
        self.act_direction.setText(tr("action.direction", direction=label))
        if hasattr(self, "double_view"):
            self.double_view.set_direction(self.direction)
            if self.mode == MODE_DOUBLE and self.archive and not self.archive.has_text_pages() and self.archive.kind != "image":
                self.double_view.show_spread(self.current_index, self.archive.count())

    def _set_fit(self, mode):
        self.fit_mode = mode
        self.settings.set("fit_mode", mode)
        if hasattr(self, "single_view"):
            self.single_view.set_fit_mode(mode)

    def _current_image_view(self):
        if not self.archive or self.archive.is_text_page(self.current_index):
            return None
        widget = self.stack.currentWidget() if hasattr(self, "stack") else None
        if widget in (getattr(self, "single_view", None), getattr(self, "double_view", None), getattr(self, "continuous_view", None)):
            return widget
        return getattr(self, "single_view", None)

    def _change_zoom(self, value):
        view = self._current_image_view()
        if view is None:
            return
        if value == "in":
            view.zoom_in()
        elif value == "out":
            view.zoom_out()
        else:
            view.set_zoom_percent(int(value))
        percent = view.zoom_percent()
        if percent is not None:
            self.statusBar().showMessage(tr("status.zoom", percent=percent), 1800)
        self._refresh_magnifier_target()

    def set_layout_mode(self, mode):
        self.layout_mode = mode
        self.settings.set("layout_mode", mode)
        if hasattr(self, "single_view"):
            self.single_view.set_layout_mode(mode)
        if hasattr(self, "double_view"):
            self.double_view.set_layout_mode(mode)
        if hasattr(self, "continuous_view"):
            self.continuous_view.set_layout_mode(mode)

    def set_double_shadow(self, checked):
        self.double_shadow = checked
        self.settings.set("double_shadow", checked)
        if hasattr(self, "double_view"):
            self.double_view.set_shadow_enabled(checked)

    # -------------------------------------------------- Fundo da página --
    def set_page_background(self, mode):
        if mode not in BG_COLORS:
            return
        self.page_background = mode
        self.settings.set("page_background", mode)
        self._apply_page_background()

    def _apply_page_background(self):
        color = BG_COLORS.get(self.page_background, BG_COLORS[BG_LIGHT])
        self.act_bg_light.setChecked(self.page_background == BG_LIGHT)
        self.act_bg_dark.setChecked(self.page_background == BG_DARK)
        self.act_bg_checker.setChecked(self.page_background == BG_CHECKER)
        # O padrão quadriculado só é relevante por trás de imagens com alfa;
        # o leitor textual do EPUB continua usando uma superfície neutra.
        stack_color = color if self.page_background != BG_CHECKER else BG_COLORS[BG_LIGHT]
        self.stack.setStyleSheet(f"QStackedWidget {{ background-color: {stack_color}; border: none; }}")
        for attr in ("single_view", "continuous_view", "double_view"):
            view = getattr(self, attr, None)
            if view is not None:
                view.set_background_color(color, checker=self.page_background == BG_CHECKER)
        epub_view = getattr(self, "epub_text_view", None)
        if epub_view is not None:
            epub_view.set_background_color(stack_color)

    # --------------------------------------------------- Lente de aumento --
    def _on_magnifier_content_changed(self, *args):
        if self._magnifier is not None:
            self._magnifier.request_refresh()

    def toggle_magnifier(self, checked):
        if checked and self._interface_hidden:
            self.act_magnifier.setChecked(False)
            return
        self.act_magnifier.setChecked(bool(checked))
        if checked:
            self._ensure_magnifier()
        self._refresh_magnifier_target()
        if checked:
            self.statusBar().showMessage(tr("status.magnifier_enabled"), 2500)
        else:
            self.statusBar().showMessage(tr("status.magnifier_disabled"), 2000)

    def _ensure_magnifier(self):
        if self._magnifier is not None:
            return self._magnifier
        if not self.archive or not hasattr(self, "single_view"):
            return None
        from app.magnifier import MagnifierOverlay
        lens = MagnifierOverlay(
            self.single_view.viewport(), self.single_view.magnifier_sample
        )
        self._magnifier = lens
        self._connect_overlay_refresh_to_existing_views()
        # Sinais do provider só são conectados quando a lente é usada pela
        # primeira vez. Sessões comuns não pagam esse custo nem importam o módulo.
        self.provider.pixmap_ready.connect(self._on_magnifier_content_changed)
        self.provider.animation_frame_ready.connect(self._on_magnifier_content_changed)
        return lens

    def _refresh_magnifier_target(self):
        lens = self._magnifier
        if lens is None:
            return
        if (
            not self.act_magnifier.isChecked()
            or not self.archive
            or self.archive.is_text_page(self.current_index)
            or self.isMinimized()
        ):
            lens.set_enabled(False)
            return

        view = self.stack.currentWidget()
        if view in (getattr(self, "single_view", None),
                    getattr(self, "continuous_view", None),
                    getattr(self, "double_view", None)):
            lens.set_target(view.viewport(), view.magnifier_sample)
            lens.set_enabled(True)
        else:
            lens.set_enabled(False)

    # ---------------------------------------------------- Seletor de cores --
    def toggle_color_picker(self, checked):
        if checked and self._interface_hidden:
            self.act_color_picker.setChecked(False)
            return
        self.act_color_picker.setChecked(bool(checked))
        if checked:
            self._ensure_color_picker()
        self._refresh_color_picker_target()
        self._update_global_event_filter()
        if checked:
            self.statusBar().showMessage(tr("status.color_picker_enabled"), 2500)
        else:
            self.statusBar().showMessage(tr("status.color_picker_disabled"), 1800)

    def _update_global_event_filter(self):
        app = QApplication.instance()
        if app is None:
            return
        needed = bool(
            self._interface_hidden
            or (self._color_picker is not None and self._color_picker.is_enabled())
        )
        if needed and not self._global_event_filter_installed:
            app.installEventFilter(self)
            self._global_event_filter_installed = True
        elif not needed and self._global_event_filter_installed:
            app.removeEventFilter(self)
            self._global_event_filter_installed = False

    def _ensure_color_picker(self):
        if self._color_picker is not None:
            return self._color_picker
        if not self.archive or not hasattr(self, "single_view"):
            return None
        from app.color_picker import ColorPickerOverlay
        picker = ColorPickerOverlay(
            self.single_view.viewport(), self.single_view.magnifier_sample, self
        )
        picker.copied.connect(
            lambda value: self.statusBar().showMessage(
                tr("status.color_picker_copied", value=value), 1800
            )
        )
        self._color_picker = picker
        self._connect_overlay_refresh_to_existing_views()
        return picker

    def _refresh_color_picker_target(self):
        picker = self._color_picker
        if picker is None:
            return
        if (
            not self.act_color_picker.isChecked()
            or not self.archive
            or self.archive.is_text_page(self.current_index)
            or self.isMinimized()
        ):
            picker.set_enabled(False)
            return

        view = self.stack.currentWidget()
        if view in (getattr(self, "single_view", None),
                    getattr(self, "continuous_view", None),
                    getattr(self, "double_view", None)):
            picker.set_target(view.viewport(), view.magnifier_sample)
            picker.set_enabled(True)
        else:
            picker.set_enabled(False)

    # ------------------------------------------------ Configurações EPUB --
    def _ensure_epub_text_view(self):
        view = getattr(self, "epub_text_view", None)
        if view is not None:
            return view
        from app.epub_view import EpubTextView
        view = EpubTextView()
        self.epub_text_view = view
        self.stack.addWidget(view)
        self._apply_epub_text_settings()
        color = BG_COLORS.get(self.page_background, BG_COLORS[BG_LIGHT])
        if self.page_background == BG_CHECKER:
            color = BG_COLORS[BG_LIGHT]
        view.set_background_color(color)
        return view

    def _apply_epub_text_settings(self):
        view = getattr(self, "epub_text_view", None)
        if view is not None:
            view.set_epub_settings(
                self.settings.epub_font_family(),
                self.settings.epub_font_size(),
                self.settings.epub_text_width(),
            )

    def open_epub_settings_dialog(self):
        from app.epub_settings_dialog import EpubSettingsDialog
        dlg = EpubSettingsDialog(self.settings, self)
        if dlg.exec():
            self._apply_epub_text_settings()
            if self.archive and self.archive.is_text_page(self.current_index):
                self._ensure_epub_text_view().show_chapter(self.archive.text_html(self.current_index))
            self.statusBar().showMessage(tr("status.epub_updated"), 4000)

    # -------------------------------------------------- Ajustes de imagem --
    def open_compare_tool(self):
        from app.compare_dialog import CompareDialog
        from app.archive import ComicArchive, ArchiveError
        if not self.provider or not self.archive or self.archive.is_text_page(self.current_index):
            return
        path, _ = QFileDialog.getOpenFileName(
            self, tr("compare.choose_second"), self.settings.last_open_directory(), tr("filter.images")
        )
        if not path:
            return
        other = None
        right_img = None
        try:
            other = ComicArchive(path)
            if other.count() <= 0 or other.is_text_page(0):
                raise ArchiveError(tr("compare.not_image"))
            # A comparação é uma ferramenta de tela; 4096 px no maior lado
            # preserva muito detalhe mesmo em zoom alto sem criar QPixmaps de
            # centenas de MB para fotografias gigantes.
            right_img = other.load_image(0, max_dim=4096)
        except Exception as e:
            show_warning(self, tr("compare.title"), str(e))
            return
        finally:
            if other is not None:
                try:
                    other.close()
                except Exception:
                    pass

        from app.pixmap_provider import _pil_to_pixmap
        try:
            right_pixmap = _pil_to_pixmap(right_img)
        finally:
            if right_img is not None:
                try:
                    right_img.close()
                except Exception:
                    pass
        left_name = self.archive.page_name(self.current_index)
        right_name = Path(path).name

        def show_compare(left_pixmap):
            if left_pixmap is None or left_pixmap.isNull():
                return
            if self._compare_dialog is not None:
                try:
                    self._compare_dialog.close()
                except RuntimeError:
                    pass
            self._compare_dialog = CompareDialog(
                left_pixmap, right_pixmap, left_name, right_name, self
            )
            self._compare_dialog.show()
            self._compare_dialog.raise_()
            self._compare_dialog.activateWindow()

        self.provider.full_res(
            self.current_index, show_compare, max_dim=4096
        )

    def open_crop_tool(self):
        from app.crop_dialog import CropDialog
        if not self.provider or not self.archive or self.archive.is_text_page(self.current_index):
            return
        index = self.current_index
        self.statusBar().showMessage(tr("status.preparing_crop"), 1800)

        def show_dialog(image):
            if image is None or image.isNull():
                return
            if self._crop_dialog is not None:
                try:
                    self._crop_dialog.close()
                except RuntimeError:
                    pass
            self._crop_dialog = CropDialog(
                image, self, initial_dir=self._save_dialog_directory()
            )
            self._crop_dialog.show()
            self._crop_dialog.raise_()
            self._crop_dialog.activateWindow()

        # O recorte recebe QImage em resolução integral. Assim a seleção salva
        # mantém todos os pixels originais sem criar um QPixmap integral extra.
        self.provider.full_res(index, show_dialog, as_qimage=True)

    def open_adjustments_dialog(self):
        from app.adjustments_dialog import AdjustmentsDialog
        values = self.settings.get_adjustments()
        if self._adjustments_dialog is None:
            self._adjustments_dialog = AdjustmentsDialog(values, self, filter_name=self.settings.image_filter())
            self._adjustments_dialog.adjustments_changed.connect(self._on_adjustments_changed)
            self._adjustments_dialog.filter_changed.connect(self._on_filter_changed)
        else:
            self._adjustments_dialog.set_values(values)
            self._adjustments_dialog.set_filter(self.settings.image_filter())
        self._adjustments_dialog.show()
        self._adjustments_dialog.raise_()
        self._adjustments_dialog.activateWindow()

    def _on_adjustments_changed(self, *values):
        self.settings.set_adjustments(*values)
        if self.provider:
            self.provider.set_adjustments(*values)

    def _on_filter_changed(self, name):
        """Persiste o filtro e força a atualização imediata da imagem exibida."""
        self.settings.set_image_filter(name)
        if self.provider:
            self.provider.set_filter(name)

    def reset_adjustments(self):
        self.settings.reset_adjustments()
        self.settings.set_image_filter("none")
        values = self.settings.get_adjustments()
        if self.provider:
            self.provider.set_adjustments(*values)
            self.provider.set_filter("none")
        if self._adjustments_dialog is not None:
            self._adjustments_dialog.set_values(values)
            self._adjustments_dialog.set_filter("none")
        self.statusBar().showMessage(tr("status.adjustments_reset"), 4000)

    # ---------------------------------------------------- Rotação/desktop --
    def _visible_image_indices(self):
        if not self.archive:
            return []
        if self.archive.is_text_page(self.current_index):
            return []
        if (
            self.mode == MODE_DOUBLE
            and not self.archive.has_text_pages()
            and self.archive.kind != "image"
            and hasattr(self, "double_view")
            and self.stack.currentWidget() is self.double_view
        ):
            return [
                index for index in (self.double_view.left_index, self.double_view.right_index)
                if index is not None
            ]
        return [self.current_index]

    def rotate_visible_pages(self, clockwise_degrees):
        if not self.provider:
            return
        indices = self._visible_image_indices()
        if not indices:
            return
        self.provider.rotate_pages(indices, clockwise_degrees)
        side = tr("side.right") if clockwise_degrees > 0 else tr("side.left")
        if len(indices) > 1:
            self.statusBar().showMessage(tr("status.pages_rotated", side=side), 3000)
        else:
            self.statusBar().showMessage(tr("status.image_rotated", side=side), 3000)

    def flip_visible_pages(self, horizontal=False, vertical=False):
        if not self.provider:
            return
        indices = self._visible_image_indices()
        if not indices:
            return
        self.provider.flip_pages(indices, horizontal=horizontal, vertical=vertical)
        self._on_magnifier_content_changed()

    def set_current_as_wallpaper(self):
        from app.wallpaper import set_windows_wallpaper
        from app.image_effects import apply_image_effects, rotate_image
        if not self.archive or not self.provider:
            return
        if self.archive.is_text_page(self.current_index):
            show_information(
                self, tr("dialog.wallpaper"),
                tr("wallpaper.text_page")
            )
            return
        if sys.platform != "win32":
            show_information(
                self, tr("dialog.wallpaper"),
                tr("wallpaper.windows_only")
            )
            return
        try:
            source_img = self.archive.load_image(self.current_index, max_dim=None)
            image = apply_image_effects(source_img, self.settings.get_adjustments(), self.settings.image_filter())
            image = rotate_image(image, self.provider.rotation_for(self.current_index))
            target = set_windows_wallpaper(image)
            self.statusBar().showMessage(
                tr("status.wallpaper_set", path=target), 5000
            )
        except Exception as e:  # noqa: BLE001
            show_warning(self, tr("dialog.wallpaper"), str(e))

    # --------------------------------------------------------- Animações --
    def _sync_active_animations(self):
        """Mantém decoders apenas para imagens realmente em exibição."""
        if not self.provider or not self.archive:
            return
        if self.archive.is_text_page(self.current_index):
            self.provider.set_animation_indices([])
            return

        if (
            self.mode == MODE_DOUBLE
            and not self.archive.has_text_pages()
            and self.archive.kind != "image"
            and hasattr(self, "double_view")
            and self.stack.currentWidget() is self.double_view
        ):
            indices = [
                i for i in (self.double_view.left_index, self.double_view.right_index)
                if i is not None
            ]
        else:
            # No contínuo animamos somente a página central/atual. Isso evita
            # dezenas de decoders simultâneos numa pasta cheia de GIF/WebM.
            indices = [self.current_index]
        self.provider.set_animation_indices(indices)

    def _schedule_animation_sync(self):
        if not self.provider:
            return
        # Interrompe o decoder da página que já saiu do centro e só inicia o
        # novo quando a rolagem ficar estável por 180 ms.
        self.provider.set_animation_indices([])
        self._animation_sync_timer.start()

    def _handle_space(self):
        if self.provider and self.provider.has_animation_activity():
            paused = self.provider.toggle_animation_pause()
            if paused is True:
                self.statusBar().showMessage(tr("status.animation_paused"), 2500)
            elif paused is False:
                self.statusBar().showMessage(tr("status.animation_resumed"), 2000)
            return
        # Preserva a navegação por Espaço em páginas que não são animadas.
        self.next_page()

    # ----------------------------------------------------- Apresentação --
    def start_slideshow(self):
        if not self.archive:
            return
        self.act_slideshow_pause.setChecked(False)
        self._slideshow_timer.start(self._slideshow_interval_ms)
        self.statusBar().showMessage(tr("status.slideshow_started"), 2200)

    def pause_slideshow(self, checked=True):
        if checked:
            self._slideshow_timer.stop()
            self.statusBar().showMessage(tr("status.slideshow_paused"), 2200)
        elif self.archive:
            self._slideshow_timer.start(self._slideshow_interval_ms)
            self.statusBar().showMessage(tr("status.slideshow_resumed"), 1800)

    def stop_slideshow(self):
        self._slideshow_timer.stop()
        self.act_slideshow_pause.setChecked(False)
        self.statusBar().showMessage(tr("status.slideshow_stopped"), 1800)

    def set_slideshow_interval(self, seconds):
        seconds = max(1, int(seconds))
        self._slideshow_interval_ms = seconds * 1000
        self.settings.set("slideshow_interval_ms", self._slideshow_interval_ms)
        if self._slideshow_timer.isActive():
            self._slideshow_timer.start(self._slideshow_interval_ms)

    def set_slideshow_shuffle(self, checked):
        self._slideshow_shuffle = bool(checked)
        self.settings.set("slideshow_shuffle", self._slideshow_shuffle)

    def set_slideshow_repeat(self, checked):
        self._slideshow_repeat = bool(checked)
        self.settings.set("slideshow_repeat", self._slideshow_repeat)

    def _slideshow_tick(self):
        if not self.archive or self._page_count() <= 0:
            self.stop_slideshow()
            return
        if self._slideshow_shuffle and self._page_count() > 1:
            candidates = [i for i in range(self._page_count()) if i != self.current_index]
            self.go_to_page(random.choice(candidates))
            return

        # Coleções virtuais avançam de membro ao final do arquivo atual.
        if self.current_index >= self._page_count() - 1:
            if self.collection is not None and self.collection_index is not None:
                if self.collection_index + 1 < self.collection.count():
                    self._open_collection_member(self.collection_index + 1)
                    return
                if self._slideshow_repeat:
                    self._open_collection_member(0)
                    return
                self.stop_slideshow()
                return

            # Pastas de imagens já são um único archive; repetir volta ao início.
            if self.archive.kind == "dir":
                if self._slideshow_repeat:
                    self.go_to_page(0)
                else:
                    self.stop_slideshow()
                return

            sibling = self._find_sibling_archive(+1)
            if sibling is not None:
                self.open_path(str(sibling))
                return
            if self._slideshow_repeat:
                self.go_to_page(0)
            else:
                self.stop_slideshow()
            return

        self.next_page()

    # -------------------------------------------------------- Navegação --
    def _page_count(self):
        return self.archive.count() if self.archive else 0

    def go_to_page(self, index):
        if not self.archive:
            return
        index = max(0, min(index, self._page_count() - 1))
        old_index = self.current_index
        self.current_index = index
        if index != old_index and self._color_picker is not None:
            self._color_picker.reset_selection()

        is_text = self.archive.is_text_page(index)
        if is_text:
            epub_view = self._ensure_epub_text_view()
            epub_view.show_chapter(self.archive.text_html(index))
            self.stack.setCurrentWidget(epub_view)
        elif self.archive.has_text_pages() or self.archive.kind == "image":
            # EPUB misto e imagens avulsas sempre usam a visualização de
            # página única. No EPUB, isso também evita spreads atravessando
            # capítulos textuais.
            self.stack.setCurrentWidget(self.single_view)
            self.single_view.show_page(index)
        elif self.mode == MODE_SINGLE:
            self.stack.setCurrentWidget(self.single_view)
            self.single_view.show_page(index)
        elif self.mode == MODE_CONTINUOUS:
            self.stack.setCurrentWidget(self.continuous_view)
            self.continuous_view.scroll_to(index)
        else:
            self.stack.setCurrentWidget(self.double_view)
            self.double_view.show_spread(index, self._page_count())

        self.act_save_page.setEnabled(not is_text)
        self._update_image_actions()
        self._refresh_magnifier_target()
        self._refresh_color_picker_target()
        self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(False)
        self.page_label.setText(tr("page.counter", current=index + 1, total=self._page_count()))
        self._sync_thumbnail_current(index)
        if self.mode == MODE_CONTINUOUS and self.stack.currentWidget() is self.continuous_view:
            self._schedule_animation_sync()
        else:
            self._animation_sync_timer.stop()
            self._sync_active_animations()
        self._save_progress()

    def next_page(self):
        if not self.archive:
            return
        if self.mode == MODE_DOUBLE and not self.archive.has_text_pages() and self.archive.kind != "image":
            nxt = self.double_view.spread_end_index() + 1
        else:
            nxt = self.current_index + 1
        if nxt >= self._page_count():
            self._load_sibling_archive(+1)
            return
        self.go_to_page(nxt)

    def prev_page(self):
        if not self.archive:
            return
        if self.mode == MODE_DOUBLE and not self.archive.has_text_pages() and self.archive.kind != "image":
            prv = self.double_view.spread_start_index() - 1
        else:
            prv = self.current_index - 1
        if prv < 0:
            self._load_sibling_archive(-1)
            return
        self.go_to_page(prv)

    # ------------------------------------------ Avançar/voltar entre arquivos --
    def _find_sibling_archive(self, direction):
        """Procura o próximo (direction=+1) ou anterior (direction=-1) arquivo
        suportado na mesma pasta do arquivo atualmente aberto."""
        if not self.archive or self.archive.kind == "dir":
            return None
        try:
            current = self.archive.path.resolve()
        except OSError:
            current = self.archive.path
        folder = current.parent
        try:
            st = folder.stat()
            signature = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
        except OSError:
            return None
        if (
            self._sibling_cache_folder == folder
            and self._sibling_cache_signature == signature
            and self._sibling_cache_names is not None
        ):
            siblings = self._sibling_cache_names
        else:
            try:
                # scandir reutiliza o stat do DirEntry e evita criar um Path
                # para cada irmão. O resultado ordenado é reutilizado enquanto
                # o diretório não mudar.
                with os.scandir(folder) as entries:
                    siblings = [
                        entry.name for entry in entries
                        if entry.is_file() and (
                            os.path.splitext(entry.name)[1].lower() in SUPPORTED_FILE_EXTS
                            or is_container_path(entry.name)
                        )
                    ]
                siblings.sort(key=natural_key)
            except OSError:
                return None
            self._sibling_cache_folder = folder
            self._sibling_cache_signature = signature
            self._sibling_cache_names = siblings
        if not siblings:
            return None
        try:
            idx = siblings.index(current.name)
        except ValueError:
            return None
        new_idx = idx + direction
        if 0 <= new_idx < len(siblings):
            return folder / siblings[new_idx]
        return None

    def _load_sibling_archive(self, direction):
        if self.collection is not None and self.collection_index is not None:
            new_index = self.collection_index + direction
            if not (0 <= new_index < self.collection.count()):
                position = tr("position.last") if direction > 0 else tr("position.first")
                self.statusBar().showMessage(tr("status.already_collection_end", position=position), 4000)
                return
            self._open_collection_member(new_index, go_to_last=(direction < 0))
            if self.archive:
                self.statusBar().showMessage(
                    tr("status.auto_loaded", name=self.collection.member_display_name(new_index)), 4000
                )
            return

        sibling = self._find_sibling_archive(direction)
        if sibling is None:
            position = tr("position.last") if direction > 0 else tr("position.first")
            self.statusBar().showMessage(tr("status.already_folder_end", position=position), 4000)
            return
        self.open_path(str(sibling))
        if direction < 0 and self.archive:
            # Ao voltar para o arquivo anterior, começa pela última página dele.
            self.go_to_page(self._page_count() - 1)
        self.statusBar().showMessage(
            tr("status.auto_loaded", name=sibling.name), 4000
        )

    def _on_slider_moved(self, value):
        self.go_to_page(value)

    def _on_continuous_scroll(self, index):
        if (self.mode == MODE_CONTINUOUS and self.archive
                and not self.archive.has_text_pages()
                and self.stack.currentWidget() is self.continuous_view):
            self.current_index = index
            self.slider.blockSignals(True)
            self.slider.setValue(index)
            self.slider.blockSignals(False)
            self.page_label.setText(tr("page.counter", current=index + 1, total=self._page_count()))
            self._sync_thumbnail_current(index)
            self._schedule_animation_sync()
            self._save_progress()

    def _save_progress(self, immediate=False):
        if not self.archive:
            return
        key = self._progress_key or str(self.archive.path)
        self._pending_progress = (key, int(self.current_index))
        if immediate:
            self._flush_progress()
        else:
            self._progress_timer.start()

    def _flush_progress(self):
        pending = self._pending_progress
        self._pending_progress = None
        self._progress_timer.stop()
        if pending is not None:
            key, index = pending
            self.settings.set_progress(key, index)

    # -------------------------------------------------------------- I/O --
    def _current_source_path(self):
        if not self.archive:
            return None
        # Em uma pasta mista, o item atual é um arquivo físico real. Em um
        # compactado virtual, operações de arquivo se aplicam ao contêiner
        # externo, nunca à cópia temporária materializada.
        if self.collection is not None and self.collection_index is not None:
            if self._is_directory_collection(self.collection):
                return Path(self.collection.member_path(self.collection_index))
            return Path(self.collection.path)
        if self.archive.kind == "dir":
            return Path(self.archive.path) / self.archive.pages[self.current_index]
        source_container = getattr(self.archive, "source_container_path", None)
        return Path(source_container or self.archive.path)

    def _file_mutations_allowed(self):
        path = self._current_source_path()
        if path is None or not path.exists() or not path.is_file():
            return False
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        return resolved not in {str(Path(p).resolve()) for p in self._clipboard_temp_files if Path(p).exists()}

    def reveal_current_file(self):
        path = self._current_source_path()
        if path is None or not path.exists():
            return
        try:
            if sys.platform.startswith("win"):
                subprocess.Popen(["explorer", "/select,", str(path.resolve())])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path.resolve())])
            else:
                subprocess.Popen(["xdg-open", str(path.resolve().parent)])
        except Exception as e:
            show_warning(self, tr("dialog.file_operation"), str(e))

    def _release_document_for_mutation(self):
        """Fecha handles antes de renomear/mover/excluir no Windows."""
        if self.archive:
            self._save_progress(immediate=True)
            self._shutdown_loaders()
            try:
                self.archive.close()
            except Exception:
                pass
            self.archive = None
        self._close_collection()

    def _reset_to_empty(self):
        self._clear_file_watches()
        self._shutdown_loaders()
        if self.archive is not None:
            try:
                self.archive.close()
            except Exception:
                pass
        self.archive = None
        self._close_collection()
        self.current_index = 0
        self._progress_key = None
        self.stack.setCurrentWidget(self.empty_label)
        self.slider.setRange(0, 0)
        self.page_label.setText(tr("page.counter", current=0, total=0))
        self.thumb_dock.setVisible(False)
        self._update_ui_enabled(False)
        self.setWindowTitle(tr("app.title"))

    def _mutation_context(self, source):
        folder_mode = bool(self.archive and self.archive.kind == "dir")
        directory_collection = self._is_directory_collection(self.collection)
        reopen_folder = None
        if folder_mode:
            reopen_folder = Path(self.archive.path)
        elif directory_collection:
            reopen_folder = Path(self.collection.path)
        sibling = None
        if not reopen_folder and self.collection is None:
            sibling = self._find_sibling_archive(+1) or self._find_sibling_archive(-1)
        return reopen_folder, sibling

    def _reopen_after_mutation(self, reopen_folder=None, target=None, sibling=None):
        if reopen_folder is not None and reopen_folder.exists():
            try:
                self.open_path(str(reopen_folder))
                if self.archive is not None:
                    if target is not None and self.archive.kind == "dir":
                        for index in range(self.archive.count()):
                            if self.archive.page_name(index) == Path(target).name:
                                self.go_to_page(index)
                                break
                    return
            except Exception:
                pass
        if target is not None and Path(target).exists() and self._is_supported_drop_path(target):
            self.open_path(str(target))
            return
        if sibling is not None and Path(sibling).exists():
            self.open_path(str(sibling))
            return
        self._reset_to_empty()

    def rename_current_file(self):
        if not self._file_mutations_allowed():
            return
        source = self._current_source_path()
        name, ok = QInputDialog.getText(
            self, tr("file_ops.rename_title"), tr("file_ops.rename_prompt"), text=source.name
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        if Path(name).name != name or Path(name).suffix.lower() != source.suffix.lower():
            show_warning(self, tr("dialog.file_operation"), tr("file_ops.keep_extension"))
            return
        target = source.with_name(name)
        if target == source:
            return
        if target.exists():
            show_warning(self, tr("dialog.file_operation"), tr("file_ops.target_exists"))
            return
        reopen_folder, sibling = self._mutation_context(source)
        self._release_document_for_mutation()
        try:
            source.rename(target)
        except Exception as e:
            self._reopen_after_mutation(reopen_folder, source, sibling)
            show_warning(self, tr("dialog.file_operation"), str(e))
            return
        self._reopen_after_mutation(reopen_folder, target, sibling)

    def move_current_file(self):
        if not self._file_mutations_allowed():
            return
        source = self._current_source_path()
        folder = QFileDialog.getExistingDirectory(
            self, tr("file_ops.move_title"), str(source.parent)
        )
        if not folder:
            return
        target = Path(folder) / source.name
        if target.exists():
            show_warning(self, tr("dialog.file_operation"), tr("file_ops.target_exists"))
            return
        reopen_folder, sibling = self._mutation_context(source)
        self._release_document_for_mutation()
        try:
            shutil.move(str(source), str(target))
        except Exception as e:
            self._reopen_after_mutation(reopen_folder, source, sibling)
            show_warning(self, tr("dialog.file_operation"), str(e))
            return
        self._reopen_after_mutation(reopen_folder, target, sibling)

    def copy_current_file_to(self):
        if not self._file_mutations_allowed():
            return
        source = self._current_source_path()
        folder = QFileDialog.getExistingDirectory(
            self, tr("file_ops.copy_title"), str(source.parent)
        )
        if not folder:
            return
        target = Path(folder) / source.name
        if target.exists() and not self._ask_yes_no(
            tr("dialog.replace_files"), tr("file_ops.replace_copy")
        ):
            return
        try:
            shutil.copy2(source, target)
            self.statusBar().showMessage(tr("status.file_copied", path=target), 3500)
        except Exception as e:
            show_warning(self, tr("dialog.file_operation"), str(e))

    def delete_current_file(self):
        if not self._file_mutations_allowed():
            return
        source = self._current_source_path()
        if not self._ask_yes_no(
            tr("file_ops.delete_title"), tr("file_ops.delete_confirm", name=source.name)
        ):
            return
        reopen_folder, sibling = self._mutation_context(source)
        self._release_document_for_mutation()
        try:
            from send2trash import send2trash
            send2trash(str(source))
        except Exception as e:
            self._reopen_after_mutation(reopen_folder, source, sibling)
            show_warning(self, tr("dialog.file_operation"), str(e))
            return
        self._reopen_after_mutation(reopen_folder, None, sibling)


    def copy_current_path(self):
        path = self._current_source_path()
        if path is None:
            return
        QApplication.clipboard().setText(str(path.resolve()))
        self.statusBar().showMessage(tr("status.path_copied"), 1800)

    def copy_current_image(self):
        if not self.archive or self.archive.is_text_page(self.current_index):
            return
        pixmap = self.provider.get(self.current_index, priority=100) if self.provider else None
        if pixmap is None or pixmap.isNull():
            self.statusBar().showMessage(tr("status.image_still_loading"), 2200)
            return
        QApplication.clipboard().setPixmap(pixmap)
        self.statusBar().showMessage(tr("status.image_copied"), 1800)

    def paste_from_clipboard(self):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime is not None and mime.hasImage():
            image = clipboard.image()
            if image.isNull():
                return
            handle = tempfile.NamedTemporaryFile(
                prefix="quaint_clipboard_", suffix=".png", delete=False
            )
            temp_path = handle.name
            handle.close()
            if not image.save(temp_path, "PNG"):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                show_warning(self, tr("dialog.clipboard_error"), tr("clipboard.save_failed"))
                return
            self._clipboard_temp_files.append(temp_path)
            self._open_regular_archive(temp_path, add_recent=False)
            if self.archive is not None:
                self.archive.display_name_override = tr("clipboard.image_name")
                self.setWindowTitle(f"{tr('app.title')} — {self.archive.display_name()}")
            return

        text = clipboard.text().strip()
        if text:
            candidate = Path(text.strip('"'))
            if self._is_supported_drop_path(candidate):
                self.settings.set_last_open_directory(candidate)
                self.open_path(candidate)
                return
        show_information(self, tr("dialog.clipboard"), tr("clipboard.no_supported_data"))

    def _save_dialog_directory(self):
        from app.save_helpers import initial_save_directory
        """Pasta inicial de Salvar como; memória apenas da sessão atual."""
        return initial_save_directory(
            session_dir=self._session_save_dir,
            archive_path=(self.archive.path if self.archive else None),
            archive_kind=(self.archive.kind if self.archive else None),
            collection_path=(self.collection.path if self.collection is not None else None),
        )

    def save_current_page(self):
        from app.save_helpers import build_save_targets, image_save_filter, save_converted_image
        if not self.archive:
            return

        # Em páginas duplas, salva as duas imagens que estão efetivamente
        # visíveis no spread. A ordem dos arquivos segue o número real da
        # página, independentemente da direção LTR/RTL.
        if (
            self.mode == MODE_DOUBLE
            and not self.archive.has_text_pages()
            and self.archive.kind != "image"
            and hasattr(self, "double_view")
            and self.stack.currentWidget() is self.double_view
        ):
            indices = [
                index for index in (self.double_view.left_index, self.double_view.right_index)
                if index is not None
            ]
        else:
            indices = [self.current_index]

        indices = sorted(set(indices))
        if not indices:
            return
        if any(self.archive.is_text_page(index) for index in indices):
            show_information(
                self, tr("dialog.text_page"),
                tr("save.text_page_message")
            )
            return

        if len(indices) == 2:
            suggested = tr("save.suggested_pages")
        else:
            suggested = tr("save.suggested_page", number=indices[0] + 1)

        initial_dir = self._save_dialog_directory()
        initial_path = str(initial_dir / suggested)
        path, selected_filter = QFileDialog.getSaveFileName(
            self, tr("dialog.save_page"), initial_path,
            image_save_filter()
        )
        if not path:
            return

        targets = build_save_targets(path, indices, selected_filter)
        existing = [str(target) for _, target in targets if target.exists()]
        if existing:
            if not self._ask_yes_no(tr("dialog.replace_files"), tr("save.replace_prompt")):
                return

        try:
            saved = []
            for index, target in targets:
                # Usa o decoder unificado, inclusive para WebM em pastas.
                # Salvar uma animação como imagem exporta o primeiro quadro,
                # mantendo o comportamento previsível dos formatos estáticos.
                source_img = self.archive.load_image(index, max_dim=None)
                save_converted_image(source_img, target)
                saved.append(str(target))

            if len(saved) == 2:
                self.statusBar().showMessage(
                    tr("status.saved_two", first=saved[0], second=saved[1]), 6000
                )
            else:
                self.statusBar().showMessage(tr("status.saved_one", path=saved[0]), 5000)
            # Só passa a lembrar a pasta depois de uma gravação concluída.
            self._session_save_dir = str(Path(saved[0]).parent)
        except Exception as e:  # noqa: BLE001
            show_warning(self, tr("dialog.save_error"), str(e))

    def _visible_image_indices(self):
        if not self.archive:
            return []
        if (
            self.mode == MODE_DOUBLE and not self.archive.has_text_pages()
            and self.archive.kind != "image" and hasattr(self, "double_view")
            and self.stack.currentWidget() is self.double_view
        ):
            values = [self.double_view.left_index, self.double_view.right_index]
        else:
            values = [self.current_index]
        return sorted({int(i) for i in values if i is not None and 0 <= int(i) < self.archive.count() and not self.archive.is_text_page(int(i))})

    def save_current_page_with_changes(self):
        from app.image_effects import apply_image_effects, rotate_image, flip_image
        from app.save_helpers import build_save_targets, image_save_filter, save_converted_image
        """Salva em resolução integral exatamente as transformações visuais atuais."""
        if not self.archive:
            return
        indices = self._visible_image_indices()
        if not indices:
            show_information(self, tr("dialog.text_page"), tr("save.text_page_message"))
            return
        suggested = tr("save.suggested_pages_changed") if len(indices) > 1 else tr("save.suggested_page_changed", number=indices[0] + 1)
        initial_path = str(self._save_dialog_directory() / suggested)
        path, selected_filter = QFileDialog.getSaveFileName(self, tr("dialog.save_changes"), initial_path, image_save_filter())
        if not path:
            return
        targets = build_save_targets(path, indices, selected_filter)
        existing = [str(target) for _, target in targets if target.exists()]
        if existing and not self._ask_yes_no(tr("dialog.replace_files"), tr("save.replace_prompt")):
            return
        try:
            saved=[]
            for index,target in targets:
                img = self.archive.load_image(index, max_dim=None)
                img = apply_image_effects(img, self.settings.get_adjustments(), self.settings.image_filter())
                rotation = self.provider.rotation_for(index) if self.provider else 0
                flip_h, flip_v = self.provider.flip_for(index) if self.provider else (False, False)
                if rotation: img = rotate_image(img, rotation)
                if flip_h or flip_v: img = flip_image(img, flip_h, flip_v)
                save_converted_image(img, target)
                saved.append(str(target))
            self._session_save_dir = str(Path(saved[0]).parent)
            self.statusBar().showMessage(tr("status.saved_changes", count=len(saved)), 5000)
        except Exception as e:
            show_warning(self, tr("dialog.save_error"), str(e))

    def _current_display_pixmap(self):
        view = self.stack.currentWidget()
        if hasattr(view, "_orig_pixmap") and view._orig_pixmap is not None:
            return view._orig_pixmap
        if hasattr(view, "_source_pix_a") and self.current_index == getattr(view, "left_index", None):
            return view._source_pix_a
        if hasattr(view, "_source_pix_b") and view._source_pix_b is not None:
            return view._source_pix_b
        if hasattr(view, "label"):
            return view.label.pixmap()
        return None

    def save_current_animation_frame(self):
        from PIL import Image
        from app.save_helpers import build_save_targets, image_save_filter, save_converted_image
        pix = self._current_display_pixmap()
        if pix is None or pix.isNull():
            return
        path, selected_filter = QFileDialog.getSaveFileName(self, tr("dialog.save_animation_frame"), str(self._save_dialog_directory() / tr("save.suggested_frame")), image_save_filter())
        if not path:
            return
        targets = build_save_targets(path, [0], selected_filter)
        target = targets[0][1]
        qimg = pix.toImage().convertToFormat(QImage.Format_RGBA8888)
        raw = qimg.bits().tobytes()
        img = Image.frombuffer("RGBA", (qimg.width(), qimg.height()), raw, "raw", "RGBA", qimg.bytesPerLine(), 1).copy()
        save_converted_image(img, target)
        self._session_save_dir = str(target.parent)
        self.statusBar().showMessage(tr("status.frame_saved", path=str(target)), 3500)

    def open_batch_export(self):
        from app.batch_export_dialog import BatchExportDialog
        from app.image_effects import apply_image_effects, rotate_image, flip_image
        from app.save_helpers import save_converted_image
        if not self.archive or self.archive.has_text_pages():
            return
        dlg = BatchExportDialog(self._save_dialog_directory(), self)
        if dlg.exec() != QDialog.Accepted:
            return
        options = dlg.options(); outdir = options["directory"]
        try:
            outdir.mkdir(parents=True, exist_ok=True)
            total=0
            for index in range(self.archive.count()):
                if self.archive.is_text_page(index):
                    continue
                img = self.archive.load_image(index, max_dim=options["max_dim"])
                if options["apply_changes"]:
                    img = apply_image_effects(img, self.settings.get_adjustments(), self.settings.image_filter())
                    rotation = self.provider.rotation_for(index) if self.provider else 0
                    fh,fv = self.provider.flip_for(index) if self.provider else (False,False)
                    if rotation: img=rotate_image(img, rotation)
                    if fh or fv: img=flip_image(img, fh, fv)
                target = outdir / f"{index+1:04d}{options['extension']}"
                save_converted_image(img, target, quality=options["quality"])
                total += 1
            self._session_save_dir = str(outdir)
            self.statusBar().showMessage(tr("status.batch_exported", count=total, path=str(outdir)), 6000)
        except Exception as e:
            show_warning(self, tr("dialog.save_error"), str(e))

    def _bookmark_key(self):
        if self._progress_key:
            return self._progress_key
        return str(self.archive.path) if self.archive else ""

    def toggle_current_bookmark(self):
        if not self.archive:
            return
        key=self._bookmark_key(); marks=set(self.settings.bookmarks(key)); page=int(self.current_index)
        if page in marks:
            marks.remove(page); message=tr("status.bookmark_removed", page=page+1)
        else:
            marks.add(page); message=tr("status.bookmark_added", page=page+1)
        self.settings.set_bookmarks(key, marks); self._refresh_bookmarks(); self.statusBar().showMessage(message, 2500)

    def _refresh_bookmarks(self):
        if self.bookmark_panel is None:
            return
        if not self.archive or not self.provider:
            self.bookmark_panel.clear_source()
            return
        valid = [
            index for index in self.settings.bookmarks(self._bookmark_key())
            if 0 <= int(index) < self.archive.count()
        ]
        self.bookmark_panel.set_source(self.archive, self.provider, valid)

    def _remove_bookmark(self, index):
        if not self.archive:
            return
        index = int(index)
        key = self._bookmark_key()
        marks = set(self.settings.bookmarks(key))
        if index not in marks:
            return
        marks.remove(index)
        self.settings.set_bookmarks(key, marks)
        self._refresh_bookmarks()
        self.statusBar().showMessage(tr("status.bookmark_removed", page=index + 1), 2500)

    def _favorite_target_path(self):
        """Retorna o item raiz aberto pelo usuário para Favoritos.

        Favoritos não acompanham a página/membro interno: uma pasta aberta
        continua sendo a pasta, e um ZIP/CBZ/PDF/imagem aberto continua sendo
        aquele arquivo inteiro.
        """
        raw = self._opened_root_path
        if raw:
            return str(Path(raw))
        if self.collection is not None:
            return str(self.collection.path)
        if self.archive is not None:
            return str(self.archive.path)
        return ""

    def toggle_current_favorite(self):
        path = self._favorite_target_path()
        if not path:
            return
        if self.settings.is_favorite(path):
            self.settings.remove_favorite(path)
            message = tr("status.favorite_removed", name=Path(path).name or path)
        else:
            self.settings.add_favorite(path)
            message = tr("status.favorite_added", name=Path(path).name or path)
        self._refresh_favorites()
        self.statusBar().showMessage(message, 2600)

    def _refresh_favorites(self):
        if self.favorite_panel is not None:
            self.favorite_panel.set_paths(self.settings.favorites())

    def _remove_favorite(self, path):
        if self.settings.remove_favorite(path):
            self._refresh_favorites()
            self.statusBar().showMessage(
                tr("status.favorite_removed", name=Path(path).name or str(path)), 2600
            )

    def _sort_archive_pages(self, archive):
        if archive is None or getattr(archive, "kind", None) != "dir" or getattr(archive, "has_text_pages", lambda: False)():
            return
        mode = self.settings.folder_sort_mode()
        desc = self.settings.folder_sort_descending()
        requested = (mode, bool(desc))
        current = getattr(archive, "_folder_sort_applied", ("name", False))
        if current == requested:
            return

        # A abertura de ComicArchive já entrega nome/natural crescente. Voltar
        # ao inverso desse estado pode ser O(n), sem recalcular natural_key.
        if mode == "name" and current == ("name", not bool(desc)):
            archive.pages = list(reversed(archive.pages))
            archive._folder_sort_applied = requested
            return

        root = Path(archive.path)
        pages = list(archive.pages)
        if mode in {"date", "size"}:
            # Um único scandir captura os stats em C. Filtrar pela extensão
            # evita construir um set duplicando todos os nomes só para testar
            # pertinência em pastas gigantes.
            stats = {}
            try:
                with os.scandir(root) as entries:
                    for entry in entries:
                        if not entry.is_file():
                            continue
                        if os.path.splitext(entry.name)[1].lower() not in STANDALONE_IMAGE_EXTS:
                            continue
                        try:
                            st = entry.stat()
                            stats[entry.name] = (st.st_mtime_ns, st.st_size)
                        except OSError:
                            pass
            except OSError:
                stats = {}
            if mode == "date":
                key = lambda name: (stats.get(name, (0, 0))[0], natural_key(name))
            else:
                key = lambda name: (stats.get(name, (0, 0))[1], natural_key(name))
        elif mode == "extension":
            key = lambda name: (os.path.splitext(name)[1].casefold(), natural_key(name))
        else:
            key = natural_key
        archive.pages = sorted(pages, key=key, reverse=bool(desc))
        archive._folder_sort_applied = requested

    def set_folder_sort(self, mode, descending=False):
        self.settings.set_folder_sort(mode, descending)
        self.act_sort_desc.setChecked(bool(descending))
        if mode in self.folder_sort_actions: self.folder_sort_actions[mode].setChecked(True)
        if self.archive and self.archive.kind == "dir":
            current_name = self.archive.pages[self.current_index] if self.archive.pages else None
            self._sort_archive_pages(self.archive)
            if current_name is not None:
                try:
                    self.current_index = self.archive.pages.index(current_name)
                except ValueError:
                    pass
            if self.provider:
                from app.pixmap_provider import PixmapProvider
                self._shutdown_loaders(); self.provider=PixmapProvider(self.archive); self.provider.set_adjustments(*self.settings.get_adjustments()); self.provider.set_filter(self.settings.image_filter()); self.provider.set_animation_speed(self._animation_speed)
                self._rebuild_views(); self._prepare_thumbnail_scope(); self.go_to_page(self.current_index)

    def _shortcut_entries(self):
        pairs = {
            "open": (self.act_open_file,"Ctrl+O"), "open_folder":(self.act_open_folder,"Ctrl+Shift+O"),
            "save":(self.act_save_page,"Ctrl+S"), "save_changes":(self.act_save_changes,"Ctrl+Shift+S"),
            "prev":(self.act_prev,"Left"), "next":(self.act_next,"Right"), "first":(self.act_first,"Home"), "last":(self.act_last,"End"),
            "mode_single":(self.act_mode_single,"1"), "mode_continuous":(self.act_mode_continuous,"2"), "mode_double":(self.act_mode_double,"3"),
            "fit_width":(self.act_fit_width,"W"), "fit_height":(self.act_fit_height,"H"), "fit_page":(self.act_fit_page,"F"),
            "zoom_in":(self.act_zoom_in,"Ctrl++"), "zoom_out":(self.act_zoom_out,"Ctrl+-"), "zoom_100":(self.act_zoom_100,"Ctrl+0"), "zoom_200":(self.act_zoom_200,"Ctrl+Alt+2"),
            "fullscreen":(self.act_fullscreen,"F11"), "image_only":(self.act_image_only,"Tab"), "thumbs":(self.act_thumbs,"T"),
            "bookmark":(self.act_toggle_bookmark,"B"), "bookmarks":(self.act_show_bookmarks,"Ctrl+B"),
            "favorite":(self.act_toggle_favorite,"Ctrl+D"), "favorites":(self.act_show_favorites,"Ctrl+Shift+F"),
            "magnifier":(self.act_magnifier,"M"), "color_picker":(self.act_color_picker,"P"),
            "crop":(self.act_crop,"Ctrl+Shift+X"), "compare":(self.act_compare,"Ctrl+Shift+D"),
            "slideshow":(self.act_slideshow_start,"F5"), "slideshow_pause":(self.act_slideshow_pause,"Shift+F5"), "slideshow_stop":(self.act_slideshow_stop,"Ctrl+F5"),
            "anim_prev":(self.act_animation_prev_frame,"Alt+Left"), "anim_next":(self.act_animation_next_frame,"Alt+Right"),
        }
        return [(key, action.text().replace('&',''), default, action) for key,(action,default) in pairs.items()]

    def _apply_custom_shortcuts(self):
        mapping=self.settings.shortcut_map()
        for key,_label,default,action in self._shortcut_entries():
            action.setShortcut(QKeySequence(mapping.get(key, default)))
        # Reafirma o contexto após qualquer personalização feita pelo usuário.
        if hasattr(self, "act_image_only"):
            self._ensure_window_shortcut_actions()

    def _ensure_window_shortcut_actions(self):
        """Mantém os atalhos no escopo da janela mesmo com menus/docks ocultos.

        QAction adicionadas somente a QMenu/QMenuBar podem deixar de receber
        atalhos quando o menu é escondido. No modo somente imagem a barra de
        menu desaparece, portanto registramos as mesmas ações diretamente na
        janela principal e usamos ``Qt.WindowShortcut``.
        """
        direct_actions = list(self.actions())
        for _key, _label, _default, action in self._shortcut_entries():
            action.setShortcutContext(Qt.WindowShortcut)
            if action not in direct_actions:
                self.addAction(action)
                direct_actions.append(action)

        # Espaço é contextual e não faz parte da tela de personalização.
        if hasattr(self, "act_space"):
            self.act_space.setShortcutContext(Qt.WindowShortcut)
            if self.act_space not in direct_actions:
                self.addAction(self.act_space)

    def open_shortcuts_dialog(self):
        from app.shortcuts_dialog import ShortcutsDialog
        mapping = self.settings.shortcut_map()
        entries=[(key,label,mapping.get(key, action.shortcut().toString(QKeySequence.PortableText) or default), default) for key,label,default,action in self._shortcut_entries()]
        dlg=ShortcutsDialog(entries,self)
        if dlg.exec()!=QDialog.Accepted:return
        self.settings.set_shortcut_map(dlg.values()); self._apply_custom_shortcuts(); self.statusBar().showMessage(tr("status.shortcuts_saved"),3000)

    def _maybe_restore_session(self):
        if not self.settings.restore_session_enabled(): return
        path=self.settings.last_session_path()
        if path and Path(path).exists() and self.archive is None: self.open_path(path)

    def _hide_cursor_if_image_only(self):
        if self._interface_hidden and self.act_hide_cursor.isChecked(): QApplication.setOverrideCursor(Qt.BlankCursor)

    def _show_cursor_temporarily(self):
        while QApplication.overrideCursor() is not None: QApplication.restoreOverrideCursor()
        if self._interface_hidden and self.act_hide_cursor.isChecked(): self._cursor_hide_timer.start()

    def eventFilter(self, obj, event):
        if (
            event.type() == QEvent.KeyPress
            and event.key() == Qt.Key_Escape
            and self._color_picker is not None
            and self._color_picker.is_enabled()
        ):
            self.toggle_color_picker(False)
            return True
        if event.type() in (QEvent.MouseMove, QEvent.MouseButtonPress) and self._interface_hidden:
            self._show_cursor_temporarily()
        return super().eventFilter(obj,event)

    def event(self, event):
        if event.type() == QEvent.Gesture:
            pinch=event.gesture(Qt.PinchGesture); swipe=event.gesture(Qt.SwipeGesture)
            if pinch is not None:
                factor=float(pinch.scaleFactor())
                if factor > 1.03: self._change_zoom("in")
                elif factor < 0.97: self._change_zoom("out")
                return True
            if swipe is not None:
                direction=swipe.horizontalDirection()
                # Qt expõe os valores de direção no próprio objeto em algumas
                # versões e no enum da classe em outras; o nome mantém ambos.
                name = getattr(direction, "name", str(direction)).lower()
                if "left" in name: self.next_page()
                elif "right" in name: self.prev_page()
                return True
        if event.type() == QEvent.NativeGesture:
            try:
                kind = event.gestureType()
                value = float(event.value())
                if kind == Qt.ZoomNativeGesture:
                    if value > 0: self._change_zoom("in")
                    elif value < 0: self._change_zoom("out")
                    return True
                if hasattr(Qt, "SwipeNativeGesture") and kind == Qt.SwipeNativeGesture:
                    if value < 0: self.next_page()
                    elif value > 0: self.prev_page()
                    return True
            except Exception:
                pass
        return super().event(event)

    def set_animation_speed(self, speed):
        self._animation_speed=float(speed); self.settings.set("animation_speed",self._animation_speed)
        if self.provider: self.provider.set_animation_speed(self._animation_speed)
        self.statusBar().showMessage(tr("status.animation_speed", speed=self._animation_speed),1800)

    def step_animation_frame(self, direction):
        if self.provider and self.provider.step_animation(direction):
            self.statusBar().showMessage(tr("status.animation_frame_step"),1200)


    def show_file_info(self):
        from app.file_info_dialog import FileInfoDialog
        if not self.archive:
            return
        self.setCursor(Qt.WaitCursor)
        try:
            tags, metadata_files = self.archive.gather_metadata()
        finally:
            self.unsetCursor()
        dlg = FileInfoDialog(self.archive, tags, metadata_files, self, current_index=self.current_index)
        dlg.exec()

    def show_summary(self):
        from app.summary_dialog import SummaryDialog
        if not self.archive:
            return
        if self.archive.kind == "dir":
            self.statusBar().showMessage(
                tr("status.summary_unavailable"), 4000
            )
            return

        if self._summary_dialog is not None:
            self._summary_dialog.close()

        if self.collection is not None:
            self._summary_dialog = SummaryDialog(
                parent=self,
                collection=self.collection,
                current_member_index=self.collection_index,
            )
            self._summary_dialog.member_selected.connect(self._open_collection_member)
        else:
            try:
                current = self.archive.path.resolve()
            except OSError:
                current = self.archive.path
            folder = current.parent
            self._summary_dialog = SummaryDialog(folder, current.name, self)
            self._summary_dialog.file_selected.connect(self.open_path)

        self._summary_dialog.show()
        self._summary_dialog.raise_()
        self._summary_dialog.activateWindow()

    # ---------------------------------------------------------- Janela --
    def _sync_native_window_edge(self):
        """Sincroniza a borda nativa do Windows com o estado da janela.

        No Windows 11 o DWM pode desenhar uma linha clara de 1 px mesmo com o
        cliente ocupando toda a janela. Em maximizado/fullscreen pintamos essa
        linha com a mesma cor do Quaint e removemos o arredondamento dos cantos;
        em janela normal restauramos o chrome padrao do sistema.
        """
        try:
            enlarged = bool(self.isMaximized() or self.isFullScreen())
            apply_edge_chrome(int(self.winId()), enlarged)
        except Exception:
            # Recurso estritamente cosmetico; nunca deve afetar abertura,
            # navegacao ou alternancia F11/Tab.
            pass

    def _queue_native_window_edge_sync(self):
        # O HWND/estado final pode mudar um pouco depois de showMaximized() ou
        # showFullScreen(). Duas passagens baratas cobrem a recriacao do frame
        # pelo Windows sem polling continuo.
        QTimer.singleShot(0, self._sync_native_window_edge)
        QTimer.singleShot(80, self._sync_native_window_edge)

    def _set_reading_views_edge_to_edge(self, enabled):
        """Remove margens internas dos leitores em fullscreen/modo imagem.

        O redimensionamento conserva a proporção da página; apenas elimina os
        paddings artificiais de 4/8/10/20 px que podiam aparecer como faixas
        claras ao redor da imagem em tela cheia.
        """
        enabled = bool(enabled)
        for name in ("single_view", "double_view", "continuous_view"):
            view = getattr(self, name, None)
            if view is not None and hasattr(view, "set_edge_to_edge"):
                view.set_edge_to_edge(enabled)

    def _refresh_edge_to_edge_mode(self):
        self._set_reading_views_edge_to_edge(
            self._interface_hidden or self.isFullScreen()
        )

    def _enter_image_only_frame(self):
        """Remove a barra de titulo somente no modo *Somente imagem*.

        Em fullscreen nativo a decoracao ja e ocultada pelo proprio sistema;
        alterar ``windowFlags`` nesse estado pode fazer o Windows recriar a
        janela como uma frameless maximizada, deixando uma borda do DWM e
        quebrando o segundo F11. Por isso, se ja estivermos em fullscreen,
        apenas guardamos o estado e adiamos a flag frameless para quando o
        fullscreen for encerrado (caso o modo Somente imagem continue ativo).
        """
        if self._interface_prev_window_flags is not None:
            return

        self._interface_prev_window_flags = self.windowFlags()
        self._interface_prev_window_state = self.windowState()
        self._interface_prev_window_geometry = self.saveGeometry()
        self._interface_entered_during_fullscreen = self.isFullScreen()

        if self._interface_entered_during_fullscreen:
            # Fullscreen nativo ja nao exibe barra de titulo. Nao toque nas
            # flags aqui: isso mantem o fullscreen realmente cobrindo o monitor.
            return

        was_maximized = self.isMaximized()
        geometry = self._interface_prev_window_geometry
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        # Alterar flags recria a janela no Windows, portanto reexibimos no
        # mesmo estado visual que ela possuia antes de ocultar o titulo.
        if was_maximized:
            self.showMaximized()
        else:
            self.showNormal()
            if geometry is not None:
                self.restoreGeometry(geometry)
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def _leave_image_only_frame(self):
        """Restaura a barra de titulo ao sair do modo *Somente imagem*."""
        flags = self._interface_prev_window_flags
        state = self._interface_prev_window_state
        geometry = self._interface_prev_window_geometry
        if flags is None:
            return

        if self.isFullScreen():
            # Nao recrie a janela enquanto ela esta em fullscreen. Se o F11
            # tiver sido iniciado a partir de uma janela frameless, trocamos o
            # estado de restauracao pelo estado decorado salvo antes do modo
            # Somente imagem. Assim, ao sair do F11 a barra volta normalmente.
            if (
                not self._interface_entered_during_fullscreen
                and self._fullscreen_prev_window_flags is not None
            ):
                self._fullscreen_prev_window_flags = flags
                self._fullscreen_prev_window_state = state
                self._fullscreen_prev_window_geometry = geometry
        else:
            self.setWindowFlags(flags)
            if state is not None and bool(state & Qt.WindowMaximized):
                self.showMaximized()
            else:
                self.showNormal()
                if geometry is not None:
                    self.restoreGeometry(geometry)
            self.activateWindow()
            self.setFocus(Qt.ActiveWindowFocusReason)

        self._interface_prev_window_flags = None
        self._interface_prev_window_state = None
        self._interface_prev_window_geometry = None
        self._interface_entered_during_fullscreen = False

    def toggle_interface_hidden(self, checked):
        """Oculta todo o chrome do app e deixa somente a area de leitura."""
        checked = bool(checked)
        if checked == self._interface_hidden:
            self.act_image_only.setChecked(checked)
            return

        self._interface_hidden = checked
        self.act_image_only.setChecked(checked)
        self._update_global_event_filter()

        if checked:
            self._interface_prev_thumbs = self.thumb_dock.isVisible()
            self._interface_prev_magnifier = bool(self.act_magnifier.isChecked())
            self._interface_prev_color_picker = bool(self.act_color_picker.isChecked())
            self._interface_prev_bookmarks = self.bookmark_dock.isVisible()
            self._interface_prev_favorites = self.favorite_dock.isVisible()

            # Overlays sao interface tambem. Esconde temporariamente e restaura
            # o estado anterior quando o usuario pressiona Tab novamente.
            if self._interface_prev_magnifier:
                self.toggle_magnifier(False)
            if self._interface_prev_color_picker:
                self.toggle_color_picker(False)

            self.thumb_dock.hide()
            self.bookmark_dock.hide()
            self.favorite_dock.hide()
            self.bottom_bar.hide()
            self.statusBar().hide()
            self.menuBar().hide()
            self._enter_image_only_frame()
            self._refresh_edge_to_edge_mode()
            self._show_cursor_temporarily()
        else:
            # A moldura volta antes do chrome para que o layout seja calculado
            # diretamente com a geometria final da janela.
            self._leave_image_only_frame()
            self.menuBar().show()
            self.bottom_bar.show()
            self.statusBar().show()
            self._cursor_hide_timer.stop()
            self._show_cursor_temporarily()
            if self._interface_prev_thumbs:
                self.thumb_dock.show()
                QTimer.singleShot(0, self._widen_thumb_dock)
            if self._interface_prev_bookmarks:
                self.bookmark_dock.show()
            if self._interface_prev_favorites:
                self.favorite_dock.show()
            if self._interface_prev_magnifier:
                self.toggle_magnifier(True)
            if self._interface_prev_color_picker:
                self.toggle_color_picker(True)
            self._refresh_edge_to_edge_mode()

        # Hiding/showing chrome changes the viewport dimensions. A queued
        # update lets the image views receive resizeEvent first.
        QTimer.singleShot(0, self._refresh_edge_to_edge_mode)
        QTimer.singleShot(0, self._refresh_magnifier_target)
        QTimer.singleShot(0, self._refresh_color_picker_target)
        self._queue_native_window_edge_sync()
        QTimer.singleShot(0, self._update_global_event_filter)

    def _set_fullscreen_action_checked(self, checked):
        """Sincroniza o estado visual da acao F11 sem redisparar o signal."""
        if not hasattr(self, "act_fullscreen"):
            return
        old = self.act_fullscreen.blockSignals(True)
        try:
            self.act_fullscreen.setChecked(bool(checked))
        finally:
            self.act_fullscreen.blockSignals(old)

    def _restore_window_after_fullscreen(self):
        """Restaura a janela ao estado anterior ao F11.

        Fullscreen nao altera flags. A unica excecao e quando o usuario entrou
        no modo Somente imagem *durante* o fullscreen: nesse caso aplicamos a
        flag frameless somente depois que o fullscreen nativo ja foi encerrado.
        """
        flags = self._fullscreen_prev_window_flags
        state = self._fullscreen_prev_window_state
        geometry = self._fullscreen_prev_window_geometry

        # Sai primeiro do fullscreen usando a decoracao nativa normal. Isso e
        # importante no Windows para liberar integralmente o estado exclusivo
        # do monitor antes de qualquer eventual mudanca de flags.
        self.showNormal()

        if flags is not None:
            desired_flags = flags
            if self._interface_hidden:
                desired_flags = flags | Qt.FramelessWindowHint
            if self.windowFlags() != desired_flags:
                self.setWindowFlags(desired_flags)

        # Se o modo Somente imagem foi ativado enquanto F11 ja estava ativo,
        # o estado salvo por ele era o proprio fullscreen. Depois de sair do
        # F11, passe a guardar o estado real anterior ao fullscreen para que
        # um Tab posterior restaure corretamente barra de titulo e geometria.
        if self._interface_hidden and self._interface_entered_during_fullscreen:
            self._interface_prev_window_flags = flags
            self._interface_prev_window_state = state
            self._interface_prev_window_geometry = geometry
            self._interface_entered_during_fullscreen = False

        if state is not None and bool(state & Qt.WindowMaximized):
            self.showMaximized()
        else:
            self.showNormal()
            if geometry is not None:
                self.restoreGeometry(geometry)

        self._fullscreen_prev_window_flags = None
        self._fullscreen_prev_window_state = None
        self._fullscreen_prev_window_geometry = None
        self.activateWindow()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def toggle_fullscreen(self, checked=None):
        """Alterna F11 usando exclusivamente o fullscreen nativo do Qt.

        Nao usamos ``FramelessWindowHint`` aqui. Em Windows, misturar a troca
        de flags com ``showFullScreen`` pode produzir uma janela frameless
        maximizada com uma pequena margem do DWM e ainda perder o atalho que
        deveria encerrar o fullscreen.
        """
        # O estado real da janela e a fonte da verdade. Isso torna o segundo
        # F11 robusto mesmo se o QAction ficar temporariamente dessincronizado.
        entering = not self.isFullScreen()

        if entering:
            if self._fullscreen_prev_window_flags is None:
                self._fullscreen_prev_window_flags = self.windowFlags()
                self._fullscreen_prev_window_state = self.windowState()
                self._fullscreen_prev_window_geometry = self.saveGeometry()
            self.showFullScreen()
            self._set_fullscreen_action_checked(True)
        else:
            self._restore_window_after_fullscreen()
            self._set_fullscreen_action_checked(False)

        QTimer.singleShot(0, self._refresh_edge_to_edge_mode)
        QTimer.singleShot(0, self._ensure_window_shortcut_actions)
        self._queue_native_window_edge_sync()

    def _ensure_thumb_panel(self):
        if self.thumb_panel is None:
            from app.thumbnail_panel import ThumbnailPanel
            panel = ThumbnailPanel()
            panel.page_selected.connect(self._on_thumbnail_selected)
            self.thumb_panel = panel
            self.thumb_dock.setWidget(panel)
            if self._thumbnail_scope_pending is not None:
                panel.prepare(self._thumbnail_scope_pending)
                panel.set_current(int(self._thumbnail_current_pending))
        return self.thumb_panel

    def _ensure_bookmark_panel(self):
        if self.bookmark_panel is None:
            from app.bookmark_panel import BookmarkPanel
            panel = BookmarkPanel()
            panel.page_selected.connect(self.go_to_page)
            panel.remove_requested.connect(self._remove_bookmark)
            self.bookmark_panel = panel
            self.bookmark_dock.setWidget(panel)
        return self.bookmark_panel

    def _ensure_favorite_panel(self):
        if self.favorite_panel is None:
            from app.favorite_panel import FavoritePanel
            panel = FavoritePanel()
            panel.path_selected.connect(self.open_path)
            panel.remove_requested.connect(self._remove_favorite)
            self.favorite_panel = panel
            self.favorite_dock.setWidget(panel)
        return self.favorite_panel

    def _prepare_thumbnail_scope(self):
        """Escolhe o conteúdo das miniaturas de acordo com o item atual.

        * imagem avulsa numa coleção: todas as imagens avulsas da coleção;
        * CBZ/CBR/compactado interno: páginas do próprio arquivo;
        * arquivo aberto normalmente: páginas do próprio arquivo.
        """
        self._thumbnail_member_map = None
        self._thumbnail_member_row = None
        self._thumbnail_scope_archive = None

        scope = self.archive
        if (
            self.collection is not None
            and self.collection_index is not None
            and self.collection.member_kind(self.collection_index) == "image"
        ):
            from app.compressed_collection import CollectionImageGroupArchive
            group = CollectionImageGroupArchive(self.collection)
            self._thumbnail_scope_archive = group
            self._thumbnail_member_map = list(group.member_indices)
            self._thumbnail_member_row = {
                member_index: row
                for row, member_index in enumerate(self._thumbnail_member_map)
            }
            scope = group

        self._thumbnail_scope_pending = scope
        if self.thumb_panel is not None:
            self.thumb_panel.prepare(scope)
            if self.thumb_dock.isVisible():
                self.thumb_panel.ensure_built()

    def _on_thumbnail_selected(self, row):
        if self._thumbnail_member_map is not None:
            row = int(row)
            if 0 <= row < len(self._thumbnail_member_map):
                self._open_collection_member(self._thumbnail_member_map[row])
            return
        self.go_to_page(int(row))

    def _sync_thumbnail_current(self, page_index):
        row = int(page_index)
        if self._thumbnail_member_map is not None:
            if self.collection_index is None or self._thumbnail_member_row is None:
                return
            mapped = self._thumbnail_member_row.get(self.collection_index)
            if mapped is None:
                return
            row = int(mapped)
        self._thumbnail_current_pending = row
        if self.thumb_panel is not None:
            self.thumb_panel.set_current(row)

    def _toggle_bookmarks_dock(self, checked):
        if self._interface_hidden:
            self.act_show_bookmarks.setChecked(False)
            self.bookmark_dock.hide()
            return
        if checked:
            self._ensure_bookmark_panel()
        self.bookmark_dock.setVisible(bool(checked))
        if checked:
            self._refresh_bookmarks()
            self.bookmark_dock.raise_()

    def _toggle_favorites_dock(self, checked):
        if self._interface_hidden:
            self.act_show_favorites.setChecked(False)
            self.favorite_dock.hide()
            return
        if checked:
            self._ensure_favorite_panel()
        self.favorite_dock.setVisible(bool(checked))
        if checked:
            self._refresh_favorites()
            self.favorite_dock.raise_()

    def _toggle_thumbs(self, checked):
        if self._interface_hidden:
            # Image-only mode must remain free of panels until Tab is pressed
            # again. The pre-hide thumbnail state is restored separately.
            self.act_thumbs.setChecked(False)
            self.thumb_dock.hide()
            return
        if checked:
            self._ensure_thumb_panel()
        self.thumb_dock.setVisible(checked)
        if checked:
            QTimer.singleShot(0, self._widen_thumb_dock)

    def _widen_thumb_dock(self):
        """Redimensiona o painel de miniaturas para ocupar ~35% da largura da janela."""
        target_width = int(self.width() * 0.40)
        self.resizeDocks([self.thumb_dock], [target_width], Qt.Horizontal)

    def _on_thumb_visibility(self, visible):
        self.act_thumbs.setChecked(visible)
        if visible:
            self._ensure_thumb_panel().ensure_built()
            QTimer.singleShot(0, self._widen_thumb_dock)

    def _update_ui_enabled(self, enabled):
        for a in (self.act_save_page, self.act_save_changes, self.act_batch_export, self.act_file_info, self.act_prev, self.act_next, self.act_toggle_bookmark, self.act_show_bookmarks, self.act_toggle_favorite,
                  self.act_first, self.act_last, self.act_summary, self.act_zoom_in,
                  self.act_zoom_out, self.act_zoom_100, self.act_zoom_200, self.act_copy_path,
                  self.act_slideshow_start, self.act_slideshow_pause, self.act_slideshow_stop,
                  self.act_reveal_file, self.act_animation_prev_frame, self.act_animation_next_frame, self.act_save_animation_frame):
            a.setEnabled(enabled)
        mutation_enabled = bool(enabled and self._file_mutations_allowed())
        for action in (self.act_rename_file, self.act_move_file, self.act_copy_file_to, self.act_delete_file):
            action.setEnabled(mutation_enabled)
        self.slider.setEnabled(enabled)
        if not enabled:
            for action in (self.act_rotate_left, self.act_rotate_right, self.act_flip_horizontal, self.act_flip_vertical, self.act_set_wallpaper, self.act_magnifier, self.act_color_picker, self.act_crop, self.act_compare):
                action.setEnabled(False)
            if self._magnifier is not None:
                self._magnifier.set_enabled(False)
            if self._color_picker is not None:
                self._color_picker.set_enabled(False)
        else:
            self._update_image_actions()

    def _update_image_actions(self):
        enabled = bool(
            self.archive
            and 0 <= self.current_index < self.archive.count()
            and not self.archive.is_text_page(self.current_index)
        )
        for action in (self.act_rotate_left, self.act_rotate_right, self.act_flip_horizontal, self.act_flip_vertical, self.act_set_wallpaper, self.act_magnifier, self.act_color_picker, self.act_crop, self.act_compare, self.act_copy_image):
            action.setEnabled(enabled)
        self._refresh_magnifier_target()
        self._refresh_color_picker_target()

    def _show_registration_result(self, ok, msg):
        if ok:
            show_information(self, tr("dialog.file_association"), msg)
        else:
            show_warning(self, tr("dialog.file_association"), msg)

    def register_file_types(self):
        from app.win_registration import register
        self._show_registration_result(*register())

    def register_epub_file_type(self):
        from app.win_registration import register_epub
        self._show_registration_result(*register_epub())

    def register_pdf_file_type(self):
        from app.win_registration import register_pdf
        self._show_registration_result(*register_pdf())

    def register_image_file_types(self):
        from app.win_registration import register_images
        self._show_registration_result(*register_images())

    def unregister_file_types(self):
        from app.win_registration import unregister
        self._show_registration_result(*unregister())

    def open_language_dialog(self):
        from app.language_dialog import LanguageDialog
        dlg = LanguageDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        selected = dlg.selected_locale()
        if selected == current_locale():
            return
        selected = set_locale(selected)
        self.settings.set_language(selected)
        self.retranslate_ui()
        name = next((name for code, name in available_locales() if code == selected), selected)
        self.statusBar().showMessage(tr("language.changed", name=name), 4000)

    def retranslate_ui(self):
        action_texts = {
            self.act_open_file: "action.open_file", self.act_open_folder: "action.open_folder",
            self.act_reopen_last: "action.reopen_last", self.act_save_page: "action.save_page", self.act_save_changes: "action.save_changes", self.act_batch_export: "action.batch_export",
            self.act_file_info: "action.file_info", self.act_reveal_file: "action.reveal_file",
            self.act_rename_file: "action.rename_file", self.act_move_file: "action.move_file",
            self.act_copy_file_to: "action.copy_file_to", self.act_delete_file: "action.delete_file",
            self.act_copy_image: "action.copy_image",
            self.act_paste_image: "action.paste_image", self.act_copy_path: "action.copy_path",
            self.act_quit: "action.quit",
            self.act_prev: "action.prev_page", self.act_next: "action.next_page",
            self.act_first: "action.first_page", self.act_last: "action.last_page",
            self.act_fullscreen: "action.fullscreen", self.act_image_only: "action.image_only_mode",
            self.act_thumbs: "action.show_thumbnails",
            self.act_summary: "action.summary", self.act_toggle_bookmark: "action.toggle_bookmark", self.act_show_bookmarks: "action.show_bookmarks", self.act_toggle_favorite: "action.toggle_favorite", self.act_show_favorites: "action.show_favorites", self.act_hide_cursor: "action.hide_cursor_image_only", self.act_mode_single: "action.mode_single",
            self.act_mode_continuous: "action.mode_continuous", self.act_mode_double: "action.mode_double",
            self.act_fit_width: "action.fit_width", self.act_fit_height: "action.fit_height",
            self.act_fit_page: "action.fit_page", self.act_zoom_in: "action.zoom_in",
            self.act_zoom_out: "action.zoom_out", self.act_zoom_100: "action.zoom_100",
            self.act_zoom_200: "action.zoom_200", self.act_layout_centered: "action.layout_centered",
            self.act_layout_full_width: "action.layout_full_width", self.act_double_shadow: "action.double_shadow",
            self.act_bg_light: "action.bg_light", self.act_bg_dark: "action.bg_dark",
            self.act_bg_checker: "action.bg_checker",
            self.act_rotate_left: "action.rotate_left", self.act_rotate_right: "action.rotate_right",
            self.act_flip_horizontal: "action.flip_horizontal", self.act_flip_vertical: "action.flip_vertical",
            self.act_set_wallpaper: "action.wallpaper", self.act_magnifier: "action.magnifier",
            self.act_color_picker: "action.color_picker", self.act_crop: "action.crop",
            self.act_compare: "action.compare",
            self.act_slideshow_start: "action.slideshow_start", self.act_slideshow_pause: "action.slideshow_pause",
            self.act_slideshow_stop: "action.slideshow_stop", self.act_slideshow_shuffle: "action.slideshow_shuffle",
            self.act_slideshow_repeat: "action.slideshow_repeat",
            self.act_adjustments: "action.adjustments",
            self.act_reset_adjustments: "action.reset_adjustments", self.act_shortcuts: "action.shortcuts", self.act_restore_session: "action.restore_session", self.act_animation_prev_frame: "action.animation_prev_frame", self.act_animation_next_frame: "action.animation_next_frame", self.act_save_animation_frame: "action.save_animation_frame", self.act_epub_settings: "action.epub",
            self.act_register: "action.register_comics", self.act_register_epub: "action.register_epub",
            self.act_register_pdf: "action.register_pdf", self.act_register_images: "action.register_images",
            self.act_unregister: "action.unregister", self.act_language: "action.language",
            self.act_about: "action.about",
        }
        for action, key in action_texts.items():
            action.setText(tr(key))
        for seconds, action in self.slideshow_interval_actions.items():
            action.setText(tr("action.slideshow_interval", seconds=seconds))
        for speed, action in self.animation_speed_actions.items():
            action.setText(tr("action.animation_speed", speed=speed))
        for mode, key in (("name","action.sort_name"),("date","action.sort_date"),("size","action.sort_size"),("extension","action.sort_extension")):
            self.folder_sort_actions[mode].setText(tr(key))
        self.act_sort_desc.setText(tr("action.sort_descending"))
        direction = tr("direction.manga") if self.direction == DIR_RTL else tr("direction.western")
        self.act_direction.setText(tr("action.direction", direction=direction))

        self.menu_file.setTitle(tr("menu.file")); self.menu_edit.setTitle(tr("menu.edit")); self.menu_nav.setTitle(tr("menu.navigate"))
        self.menu_view.setTitle(tr("menu.view")); self.menu_tools.setTitle(tr("menu.tools"))
        self.menu_help.setTitle(tr("menu.help")); self.recent_menu.setTitle(tr("menu.recent"))
        self.menu_bg.setTitle(tr("menu.page_background")); self.menu_assoc.setTitle(tr("menu.file_association"))
        self.menu_slideshow.setTitle(tr("menu.slideshow")); self.menu_slideshow_interval.setTitle(tr("menu.slideshow_interval")); self.menu_sort.setTitle(tr("menu.sort")); self.menu_animation.setTitle(tr("menu.animation"))
        self.thumb_dock.setWindowTitle(tr("dock.thumbnails")); self.bookmark_dock.setWindowTitle(tr("dock.bookmarks")); self.favorite_dock.setWindowTitle(tr("dock.favorites"))
        if self.bookmark_panel is not None:
            self.bookmark_panel.retranslate_ui()
        if self.favorite_panel is not None:
            self.favorite_panel.retranslate_ui()
        self._refresh_recent_menu()
        total = self._page_count() if self.archive is not None else 0
        current = self.current_index + 1 if total > 0 else 0
        self.page_label.setText(tr("page.counter", current=current, total=total))

        if self._adjustments_dialog is not None:
            self._adjustments_dialog.retranslate_ui()
        if self._summary_dialog is not None:
            self._summary_dialog.retranslate_ui()
        if self.thumb_panel is not None:
            self.thumb_panel.retranslate_ui()
        if self._color_picker is not None:
            self._color_picker.retranslate_ui()
        for view_name in ("single_view", "double_view", "continuous_view"):
            view = getattr(self, view_name, None)
            if view is not None and hasattr(view, "retranslate_ui"):
                view.retranslate_ui()

        if self.archive is not None:
            if self.collection is not None:
                title = f"{self.collection.display_name()} — {self.collection.member_display_name(self.collection_index)}"
            else:
                title = self.archive.display_name()
            self.setWindowTitle(f"{tr('app.title')} — {title}")
        else:
            self.setWindowTitle(tr("app.title"))

    def show_about(self):
        show_about_box(
            self, tr("about.title"), tr("about.body", version=APP_VERSION)
        )

    def _restore_state(self):
        self.act_direction.setText(
            tr("action.direction", direction=(
                tr("direction.manga") if self.direction == DIR_RTL else tr("direction.western")
            ))
        )
        if self.mode == MODE_SINGLE:
            self.act_mode_single.setChecked(True)
        elif self.mode == MODE_CONTINUOUS:
            self.act_mode_continuous.setChecked(True)
        else:
            self.act_mode_double.setChecked(True)

        fit_actions = {
            "width": self.act_fit_width,
            "height": self.act_fit_height,
            "page": self.act_fit_page,
        }
        fit_actions.get(self.fit_mode, self.act_fit_page).setChecked(True)

        if self.layout_mode == LAYOUT_FULL_WIDTH:
            self.act_layout_full_width.setChecked(True)
        else:
            self.act_layout_centered.setChecked(True)

        self.act_double_shadow.setChecked(self.double_shadow)

        geo = self.settings.get("window_geometry")
        if geo:
            self.restoreGeometry(geo)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            # Independe de existir uma imagem aberta: maximizar/restaurar a
            # janela deve atualizar imediatamente a borda externa do DWM.
            self._queue_native_window_edge_sync()
            if self.provider is not None:
                if self.isMinimized():
                    # Nada é visível: zera CPU de GIF/WebM enquanto minimizado.
                    self._animation_sync_timer.stop()
                    self.provider.set_animation_indices([])
                    if self._magnifier is not None:
                        self._magnifier.set_enabled(False)
                    if self._color_picker is not None:
                        self._color_picker.set_enabled(False)
                elif self.archive is not None:
                    self._refresh_magnifier_target()
                    self._refresh_color_picker_target()
                    if self.mode == MODE_CONTINUOUS and hasattr(self, "continuous_view"):
                        self._schedule_animation_sync()
                    else:
                        self._sync_active_animations()

    def closeEvent(self, event):
        if self.settings.restore_session_enabled():
            self.settings.set_last_session_path(self._opened_root_path or "")
        self._cursor_hide_timer.stop()
        self._show_cursor_temporarily()
        self._slideshow_timer.stop()
        if self._fs_refresh_timer is not None:
            self._fs_refresh_timer.stop()
        self._clear_file_watches()
        if self._global_event_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._global_event_filter_installed = False
        for temp_path in getattr(self, "_clipboard_temp_files", []):
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        self._clipboard_temp_files = []
        geo = self.saveGeometry()
        self.settings.set("window_geometry", geo)
        if self.archive:
            self._save_progress(immediate=True)
            self._shutdown_loaders()
            self.archive.close()
        else:
            self._shutdown_loaders()
        self._close_collection()
        super().closeEvent(event)
