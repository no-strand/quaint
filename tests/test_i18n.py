"""Regression tests for the JSON-based dynamic localization layer."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from string import Formatter

from app.i18n import DEFAULT_LOCALE, available_locales, current_locale, set_locale, tr


ROOT = Path(__file__).resolve().parents[1]
LOCALES = ROOT / "locales"


def _fields(value):
    fields = set()
    if not isinstance(value, str):
        return fields
    for _literal, field_name, _format_spec, _conversion in Formatter().parse(value):
        if field_name:
            fields.add(field_name.split(".", 1)[0].split("[", 1)[0])
    return fields


def _assert_no_literal_ui_text():
    """User-visible Qt strings must come from tr(), not Python literals."""
    methods = {
        "setWindowTitle", "setText", "setToolTip", "setTitle", "showMessage",
        "addAction", "addMenu", "setLabelText", "setOkButtonText",
        "setCancelButtonText", "setPlaceholderText", "setStatusTip", "setWhatsThis",
        "setSuffix", "setPrefix",
    }
    constructors = {
        "QAction", "QLabel", "QPushButton", "QGroupBox", "QCheckBox",
        "QRadioButton", "QMenu", "QListWidgetItem",
    }
    violations = []
    for path in sorted((ROOT / "app").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                continue
            if name not in methods | constructors:
                continue
            for arg in node.args:
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    continue
                value = arg.value.strip()
                if not value:
                    continue
                # Styles, HTML/data and glyph-only fallback buttons are not language text.
                if any(token in value for token in (
                    "background", "color:", "QScrollArea", "border", "href=", "#",
                )):
                    continue
                if all(not ch.isalpha() for ch in value):
                    continue
                violations.append(f"{path.name}:{node.lineno}: {value!r}")
    assert not violations, "Hardcoded user-facing text found: " + "; ".join(violations)


def main():
    assert DEFAULT_LOCALE == "en_US"

    expected_files = {"en_US.json", "pt_BR.json", "es_ES.json"}
    assert expected_files <= {p.name for p in LOCALES.glob("*.json")}

    catalogs = {
        code: json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))
        for code in ("en_US", "pt_BR", "es_ES")
    }
    base_keys = set(catalogs["en_US"])
    assert all(set(data) == base_keys for data in catalogs.values())
    assert len(base_keys) >= 255
    for code, data in catalogs.items():
        assert all(
            key == "_meta" or (isinstance(value, str) and value != "")
            for key, value in data.items()
        ), f"Empty/non-string translation in {code}"

    # Format placeholders must stay compatible across all languages.
    for key in base_keys - {"_meta"}:
        base_fields = _fields(catalogs["en_US"][key])
        for code in ("pt_BR", "es_ES"):
            assert _fields(catalogs[code][key]) == base_fields, (code, key)

    assert catalogs["en_US"]["_meta"]["name"] == "English (United States)"
    assert catalogs["pt_BR"]["_meta"]["name"] == "Português (Brasil)"
    assert catalogs["es_ES"]["_meta"]["name"] == "Español (España)"
    assert catalogs["pt_BR"]["language.description"] == "Escolha o idioma da interface"
    assert catalogs["en_US"]["language.description"] == "Choose the interface language"
    assert catalogs["es_ES"]["language.description"] == "Elige el idioma de la interfaz"

    set_locale("en_US")
    assert current_locale() == "en_US"
    assert tr("action.open_file") == "Open file..."
    assert tr("page.counter", current=3, total=9) == "3 / 9"

    set_locale("pt_BR")
    assert tr("action.open_file") == "Abrir arquivo..."
    set_locale("es_ES")
    assert tr("action.open_file") == "Abrir archivo..."

    # Unknown locales fail safely to English.
    set_locale("does_NOT_exist")
    assert current_locale() == "en_US"
    assert tr("action.open_file") == "Open file..."
    assert available_locales()[0][0] == "en_US"

    settings_source = (ROOT / "app" / "settings.py").read_text(encoding="utf-8")
    reader_source = (ROOT / "app" / "reader_window.py").read_text(encoding="utf-8")
    dialog_source = (ROOT / "app" / "language_dialog.py").read_text(encoding="utf-8")
    spec_source = (ROOT / "Quaint.spec").read_text(encoding="utf-8")

    assert 'self.qs.value("language", DEFAULT_LOCALE)' in settings_source
    assert 'self.qs.setValue("language"' in settings_source
    assert 'self.act_language = QAction(tr("action.language")' in reader_source
    assert 'self.menu_help.addAction(self.act_language)' in reader_source
    assert 'self.settings.set_language(selected)' in reader_source
    assert 'self.retranslate_ui()' in reader_source
    assert 'available_locales()' in dialog_source
    assert '(os.path.join(PROJECT_ROOT, "locales"), "locales")' in spec_source

    # QFormLayout rows must use explicit label widgets. Passing an empty label
    # string and later calling labelForField() is not portable across Qt builds:
    # some versions return None, which was the EPUB-settings crash reported on Windows.
    epub_source = (ROOT / "app" / "epub_settings_dialog.py").read_text(encoding="utf-8")
    info_source = (ROOT / "app" / "file_info_dialog.py").read_text(encoding="utf-8")
    assert "labelForField(" not in epub_source
    assert "labelForField(" not in info_source
    assert 'addRow("",' not in epub_source
    assert 'addRow("",' not in info_source
    for attr in ("font_label", "font_size_label", "text_width_label"):
        assert f"self.{attr} = QLabel()" in epub_source
    for attr in (
        "name_caption", "path_caption", "folder_caption",
        "type_caption", "size_caption", "pages_caption",
    ):
        assert f"self.{attr}" in info_source

    # Every literal translation-key-like string referenced anywhere in app/
    # must exist in the English catalog. This also covers keys stored in dicts
    # and later passed dynamically to tr().
    prefixes = {key.split(".", 1)[0] for key in base_keys if key != "_meta"}
    missing_refs = []
    for path in sorted((ROOT / "app").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            value = node.value
            if "." not in value:
                continue
            if Path(value).suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".jfif", ".webp", ".gif",
                ".tif", ".tiff", ".bmp", ".ico", ".webm", ".cbz",
                ".cbr", ".zip", ".rar", ".7z", ".pdf", ".epub",
                ".json", ".html", ".xhtml", ".xml", ".opf",
            }:
                continue
            prefix = value.split(".", 1)[0]
            if prefix in prefixes and value not in base_keys:
                missing_refs.append(f"{path.name}:{node.lineno}: {value}")
    assert not missing_refs, "Unknown localization keys: " + "; ".join(missing_refs)

    _assert_no_literal_ui_text()
    print("Internacionalização JSON (en_US/pt_BR/es_ES) + persistência: OK")


if __name__ == "__main__":
    main()
