import json
from pathlib import Path

from PySide6.QtCore import QSettings

from app.adjustment_defs import ADJUSTMENT_KEYS, DEFAULT_ADJUSTMENTS, normalize_filter_name
from app.i18n import DEFAULT_LOCALE

DEFAULT_EPUB_FONT = "Georgia"
DEFAULT_EPUB_FONT_SIZE = 17
DEFAULT_EPUB_TEXT_WIDTH = 820


class Settings:
    _MISSING = object()

    def __init__(self):
        self.qs = QSettings("Quaint", "Quaint")
        # QSettings no Windows usa o Registro. O cache reduz consultas repetidas
        # durante construção de menus/ações e navegação sem alterar persistência.
        self._value_cache = {}
        self._progress_cache = None
        self._json_cache = {}

    # ------------------------------------------------------------ Genérico --
    def get(self, key, default=None):
        cached = self._value_cache.get(key, self._MISSING)
        if cached is not self._MISSING:
            return cached
        value = self.qs.value(key, default)
        self._value_cache[key] = value
        return value

    def get_bool(self, key, default=False):
        val = self.get(key, default)
        if isinstance(val, str):
            return val.lower() in ("1", "true", "yes")
        return bool(val)

    def get_float(self, key, default=0.0):
        val = self.get(key, default)
        try:
            return float(val)
        except (TypeError, ValueError):
            return default

    def set(self, key, value):
        cached = self._value_cache.get(key, self._MISSING)
        # QSettings no Windows escreve no Registro. Evitar setValue quando o
        # valor não mudou reduz bastante I/O durante sliders/atalhos e também
        # evita notificações de registro desnecessárias.
        if cached is not self._MISSING and cached == value:
            return False
        self._value_cache[key] = value
        self.qs.setValue(key, value)
        self._json_cache.pop(key, None)
        return True


    # -------------------------------------------------------------- Idioma --
    def language(self):
        value = str(self.get("language", DEFAULT_LOCALE) or DEFAULT_LOCALE)
        return value

    def set_language(self, locale):
        self.set("language", str(locale or DEFAULT_LOCALE))

    # -------------------------------------------------------- Arquivos recentes --
    def recent_files(self):
        files = self.get("recent_files", [])
        return list(files) if files else []

    def add_recent(self, path):
        files = self.recent_files()
        path = str(path)
        if path in files:
            files.remove(path)
        files.insert(0, path)
        self.set("recent_files", files[:10])

    def clear_recent(self):
        self.set("recent_files", [])

    # ---------------------------------------------- Último diretório aberto --
    def last_open_directory(self):
        """Retorna o último diretório usado em Abrir arquivo/pasta.

        O caminho é persistente entre execuções. Se a pasta tiver sido
        removida ou não estiver mais acessível, deixa o QFileDialog escolher
        o diretório padrão do sistema retornando uma string vazia.
        """
        raw = str(self.get("last_open_directory", "") or "").strip()
        if not raw:
            return ""
        try:
            path = Path(raw).expanduser()
            return str(path) if path.is_dir() else ""
        except (OSError, ValueError):
            return ""

    def set_last_open_directory(self, path):
        """Memoriza a pasta de um arquivo ou diretório selecionado."""
        if not path:
            return
        try:
            selected = Path(str(path)).expanduser()
            directory = selected if selected.is_dir() else selected.parent
            if directory.is_dir():
                self.set("last_open_directory", str(directory))
        except (OSError, ValueError):
            pass

    # --------------------------------------------------- Progresso de leitura --
    def _progress_map(self):
        if self._progress_cache is not None:
            return self._progress_cache
        raw = self.get("progress_map", "")
        if not raw:
            self._progress_cache = {}
            return self._progress_cache
        try:
            data = json.loads(raw)
            self._progress_cache = data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            self._progress_cache = {}
        return self._progress_cache

    def get_progress(self, path):
        """Retorna o índice (0-based) da última página lida desse arquivo, ou 0."""
        data = self._progress_map()
        try:
            return int(data.get(str(path), 0))
        except (TypeError, ValueError):
            return 0

    def set_progress(self, path, index):
        data = self._progress_map()
        data[str(path)] = int(index)
        # evita crescimento infinito: mantém só os 300 arquivos mais recentes
        if len(data) > 300:
            for k in list(data.keys())[: len(data) - 300]:
                data.pop(k, None)
        self.set("progress_map", json.dumps(data))


    # ------------------------------------------------------------ EPUB --
    DEFAULT_EPUB_FONT_SIZE = DEFAULT_EPUB_FONT_SIZE
    DEFAULT_EPUB_TEXT_WIDTH = DEFAULT_EPUB_TEXT_WIDTH

    def default_epub_font(self):
        from PySide6.QtGui import QFont
        return QFont(DEFAULT_EPUB_FONT)

    def epub_font_family(self):
        return str(self.get("epub_font_family", DEFAULT_EPUB_FONT) or DEFAULT_EPUB_FONT)

    def epub_font(self):
        from PySide6.QtGui import QFont
        return QFont(self.epub_font_family())

    def epub_font_size(self):
        try:
            return max(10, min(36, int(self.get("epub_font_size", DEFAULT_EPUB_FONT_SIZE))))
        except (TypeError, ValueError):
            return DEFAULT_EPUB_FONT_SIZE

    def epub_text_width(self):
        try:
            return max(520, min(1400, int(self.get("epub_text_width", DEFAULT_EPUB_TEXT_WIDTH))))
        except (TypeError, ValueError):
            return DEFAULT_EPUB_TEXT_WIDTH

    def set_epub_settings(self, font_family, font_size, text_width):
        self.set("epub_font_family", str(font_family or DEFAULT_EPUB_FONT))
        self.set("epub_font_size", int(font_size))
        self.set("epub_text_width", int(text_width))

    # ------------------------------------------------------- Ajustes de imagem --
    def get_adjustments(self):
        """Retorna os ajustes de imagem na ordem definida por ADJUSTMENT_KEYS."""
        values = []
        for key in ADJUSTMENT_KEYS:
            values.append(self.get_float(f"adj_{key}", DEFAULT_ADJUSTMENTS[key]))
        return tuple(values)

    def set_adjustments(self, *values):
        # Compatibilidade apenas com formatos internos anteriores dos ajustes.
        values = list(values)
        if 0 < len(values) < len(ADJUSTMENT_KEYS):
            values.extend(DEFAULT_ADJUSTMENTS[k] for k in ADJUSTMENT_KEYS[len(values):])
        if len(values) != len(ADJUSTMENT_KEYS):
            from app.i18n import tr
            raise ValueError(tr("error.adjustment_count"))
        for key, value in zip(ADJUSTMENT_KEYS, values):
            value = float(value)
            setting_key = f"adj_{key}"
            # Em uma alteração de slider, normalmente só um dos ~30 valores
            # muda. Antes todos eram regravados no Registro a cada evento.
            current = self.get_float(setting_key, DEFAULT_ADJUSTMENTS[key])
            if current != value:
                self.set(setting_key, value)

    def reset_adjustments(self):
        self.set_adjustments(*(DEFAULT_ADJUSTMENTS[k] for k in ADJUSTMENT_KEYS))


    # ------------------------------------------------------- Filtro de imagem --
    def image_filter(self):
        return normalize_filter_name(self.get("image_filter", "none"))

    def set_image_filter(self, name):
        self.set("image_filter", normalize_filter_name(name))

    # ------------------------------------------------------------- Marcadores --
    def _json_map(self, key):
        cached = self._json_cache.get(key, self._MISSING)
        if cached is not self._MISSING:
            return dict(cached)
        raw = self.get(key, "")
        try:
            data = json.loads(raw) if raw else {}
            data = data if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            data = {}
        self._json_cache[key] = data
        return dict(data)

    def bookmarks(self, path):
        data = self._json_map("bookmarks_map")
        values = data.get(str(path), [])
        try:
            return sorted({max(0, int(v)) for v in values})
        except Exception:
            return []

    def set_bookmarks(self, path, values):
        data = self._json_map("bookmarks_map")
        clean = sorted({max(0, int(v)) for v in values})
        if clean:
            data[str(path)] = clean
        else:
            data.pop(str(path), None)
        self.set("bookmarks_map", json.dumps(data))

    # -------------------------------------------------------------- Favoritos --
    def favorites(self):
        """Retorna caminhos favoritos na ordem em que foram adicionados."""
        raw = self.get("favorites", "")
        try:
            values = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            values = []
        if not isinstance(values, list):
            return []
        result = []
        seen = set()
        for value in values:
            path = str(value or "").strip()
            if not path:
                continue
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            result.append(path)
        return result

    def set_favorites(self, values):
        clean = []
        seen = set()
        for value in values or []:
            path = str(value or "").strip()
            if not path:
                continue
            key = path.casefold()
            if key in seen:
                continue
            seen.add(key)
            clean.append(path)
        self.set("favorites", json.dumps(clean))

    def is_favorite(self, path):
        key = str(path or "").casefold()
        return bool(key) and any(item.casefold() == key for item in self.favorites())

    def add_favorite(self, path):
        path = str(path or "").strip()
        if not path:
            return False
        values = self.favorites()
        if any(item.casefold() == path.casefold() for item in values):
            return False
        values.append(path)
        self.set_favorites(values)
        return True

    def remove_favorite(self, path):
        key = str(path or "").casefold()
        values = self.favorites()
        filtered = [item for item in values if item.casefold() != key]
        if len(filtered) == len(values):
            return False
        self.set_favorites(filtered)
        return True

    # ------------------------------------------------------------ Atalhos UI --
    def shortcut_map(self):
        data = self._json_map("shortcut_map")
        return {str(k): str(v) for k, v in data.items()}

    def set_shortcut_map(self, mapping):
        self.set("shortcut_map", json.dumps({str(k): str(v) for k,v in dict(mapping).items()}))

    # ------------------------------------------------------- Restauração sessão --
    def restore_session_enabled(self):
        return self.get_bool("restore_session", False)

    def set_restore_session_enabled(self, enabled):
        self.set("restore_session", bool(enabled))

    def last_session_path(self):
        return str(self.get("last_session_path", "") or "")

    def set_last_session_path(self, path):
        self.set("last_session_path", str(path or ""))

    # --------------------------------------------------------- Ordenação pasta --
    def folder_sort_mode(self):
        mode = str(self.get("folder_sort_mode", "name") or "name")
        return mode if mode in {"name","date","size","extension"} else "name"

    def folder_sort_descending(self):
        return self.get_bool("folder_sort_descending", False)

    def set_folder_sort(self, mode, descending=False):
        self.set("folder_sort_mode", str(mode))
        self.set("folder_sort_descending", bool(descending))
