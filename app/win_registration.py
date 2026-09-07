"""Windows file associations for Quaint."""
import os
import sys

from app.i18n import tr

IS_WINDOWS = sys.platform.startswith("win")

FILE_TYPE_GROUPS = {
    "comics": {
        "prog_id": "Quaint.ComicFile",
        "extensions": (".cbz", ".cbr"),
        "description_key": "registration.comics_description",
        "success_key": "registration.comics_success",
    },
    "epub": {
        "prog_id": "Quaint.EpubFile",
        "extensions": (".epub",),
        "description_key": "registration.epub_description",
        "success_key": "registration.epub_success",
    },
    "pdf": {
        "prog_id": "Quaint.PdfFile",
        "extensions": (".pdf",),
        "description_key": "registration.pdf_description",
        "success_key": "registration.pdf_success",
    },
    "images": {
        "prog_id": "Quaint.ImageFile",
        "extensions": (
            ".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif",
            ".tif", ".tiff", ".bmp", ".ico", ".webm",
            ".avif", ".heic", ".heif", ".jxl", ".svg", ".svgz",
        ),
        "description_key": "registration.image_description",
        "success_key": "registration.images_success",
    },
}

if IS_WINDOWS:
    import ctypes
    import winreg


def _open_command_and_icon():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" "%1"', sys.executable
    main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{sys.executable}" "{main_py}" "%1"', sys.executable


def _notify_shell():
    if IS_WINDOWS:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0x0000, None, None)


def register_group(group):
    if not IS_WINDOWS:
        return False, tr("registration.windows_only")
    config = FILE_TYPE_GROUPS[group]
    try:
        command, icon_target = _open_command_and_icon()
        prog_id = config["prog_id"]
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}") as k:
            winreg.SetValue(k, "", winreg.REG_SZ, tr(config["description_key"]))
            with winreg.CreateKey(k, "DefaultIcon") as ik:
                winreg.SetValue(ik, "", winreg.REG_SZ, f"{icon_target},0")
            with winreg.CreateKey(k, r"shell\open\command") as ck:
                winreg.SetValue(ck, "", winreg.REG_SZ, command)
        for ext in config["extensions"]:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{ext}") as k:
                winreg.SetValue(k, "", winreg.REG_SZ, prog_id)
        _notify_shell()
        return True, tr(config["success_key"])
    except Exception as e:
        return False, tr("registration.failed", error=e)


def unregister_group(group):
    if not IS_WINDOWS:
        return False, tr("registration.windows_only_short")
    config = FILE_TYPE_GROUPS[group]
    prog_id = config["prog_id"]
    try:
        for ext in config["extensions"]:
            key_path = rf"Software\Classes\{ext}"
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, key_path, 0,
                    winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
                ) as k:
                    try:
                        current, _ = winreg.QueryValueEx(k, "")
                    except FileNotFoundError:
                        current = None
                    if current == prog_id:
                        try:
                            winreg.DeleteValue(k, "")
                        except FileNotFoundError:
                            pass
            except FileNotFoundError:
                pass
        _delete_tree(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{prog_id}")
        _notify_shell()
        return True, tr("registration.removed")
    except Exception as e:
        return False, str(e)


def register():
    return register_group("comics")


def register_epub():
    return register_group("epub")


def register_pdf():
    return register_group("pdf")


def register_images():
    return register_group("images")


def unregister():
    if not IS_WINDOWS:
        return False, tr("registration.windows_only_short")
    errors = []
    for group in FILE_TYPE_GROUPS:
        ok, msg = unregister_group(group)
        if not ok:
            errors.append(msg)
    if errors:
        return False, "\n".join(errors)
    _notify_shell()
    return True, tr("registration.all_removed")


def _delete_tree(root, path):
    try:
        with winreg.OpenKey(root, path) as k:
            while True:
                try:
                    sub = winreg.EnumKey(k, 0)
                    _delete_tree(root, path + "\\" + sub)
                except OSError:
                    break
        winreg.DeleteKey(root, path)
    except FileNotFoundError:
        pass
