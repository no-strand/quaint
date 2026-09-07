"""Localized QMessageBox helpers.

Qt's convenience QMessageBox functions use the platform/Qt language for
standard buttons. These wrappers keep OK/Yes/No aligned with Quaint's
runtime locale, even when the app language differs from the OS language.
"""
import os

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMessageBox

from app.i18n import tr


def _message(parent, icon, title, text):
    box = QMessageBox(parent)
    box.setIcon(icon)
    box.setWindowTitle(str(title))
    box.setText(str(text))
    box.setStandardButtons(QMessageBox.Ok)
    ok = box.button(QMessageBox.Ok)
    if ok is not None:
        ok.setText(tr("common.ok"))
    return box.exec()


def information(parent, title, text):
    return _message(parent, QMessageBox.Information, title, text)


def warning(parent, title, text):
    return _message(parent, QMessageBox.Warning, title, text)


def critical(parent, title, text):
    return _message(parent, QMessageBox.Critical, title, text)


def about(parent, title, text):
    """Show the About dialog with Quaint's own application icon.

    Prefer the exact ``resources/icon.ico`` used by the executable/window.
    Falling back to the window icon keeps the dialog correct in unusual
    packaging layouts without ever requiring a separate icon asset.
    """
    box = QMessageBox(parent)
    box.setWindowTitle(str(title))
    box.setText(str(text))

    icon = QIcon()
    res_dir = getattr(parent, "res_dir", None)
    if res_dir:
        icon_path = os.path.join(str(res_dir), "icon.ico")
        if os.path.exists(icon_path):
            icon = QIcon(icon_path)
    if icon.isNull() and parent is not None:
        try:
            icon = parent.windowIcon()
        except Exception:
            pass

    if not icon.isNull():
        box.setIconPixmap(icon.pixmap(48, 48))
    else:
        box.setIcon(QMessageBox.Information)

    box.setStandardButtons(QMessageBox.Ok)
    ok = box.button(QMessageBox.Ok)
    if ok is not None:
        ok.setText(tr("common.ok"))
    return box.exec()


def ask_yes_no(parent, title, text, default_no=True):
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(str(title))
    box.setText(str(text))
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    yes = box.button(QMessageBox.Yes)
    no = box.button(QMessageBox.No)
    if yes is not None:
        yes.setText(tr("common.yes"))
    if no is not None:
        no.setText(tr("common.no"))
    if default_no and no is not None:
        box.setDefaultButton(no)
    elif yes is not None:
        box.setDefaultButton(yes)
    box.exec()
    return box.clickedButton() is yes
