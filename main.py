"""Quaint — leitor de CBZ, CBR, PDF, EPUB, imagens e coleções compactadas.

Uso:
    python main.py                  Abre o programa vazio
    python main.py caminho.epub     Abre qualquer arquivo suportado diretamente
    python main.py --register       Associa .cbz/.cbr a este programa (Windows)
    python main.py --register-epub  Associa .epub a este programa (Windows)
    python main.py --register-pdf   Associa .pdf a este programa (Windows)
    python main.py --register-images Associa imagens suportadas (Windows)
    python main.py --unregister     Remove todas as associações
"""
import os
import sys


def main():
    args = sys.argv[1:]

    # Idioma persistente também vale para operações de linha de comando.
    from app.settings import Settings
    from app.i18n import set_locale, tr
    settings = Settings()
    set_locale(settings.language())

    if "--register" in args:
        from app.win_registration import register
        ok, msg = register()
        print(msg)
        return 0 if ok else 1

    if "--register-epub" in args:
        from app.win_registration import register_epub
        ok, msg = register_epub()
        print(msg)
        return 0 if ok else 1

    if "--register-pdf" in args:
        from app.win_registration import register_pdf
        ok, msg = register_pdf()
        print(msg)
        return 0 if ok else 1

    if "--register-images" in args:
        from app.win_registration import register_images
        ok, msg = register_images()
        print(msg)
        return 0 if ok else 1

    if "--unregister" in args:
        from app.win_registration import unregister
        ok, msg = unregister()
        print(msg)
        return 0 if ok else 1

    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QIcon
    from PySide6.QtCore import QTimer
    from app.reader_window import MainWindow
    from app.version import APP_VERSION

    app = QApplication(sys.argv)
    app.setApplicationName(tr("app.title"))
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName("Quaint")

    if getattr(sys, "frozen", False):
        # Empacotado pelo PyInstaller: resources/ vem dentro do bundle (_MEIPASS)
        res_dir = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "resources")
    else:
        res_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")

    icon_path = os.path.join(res_dir, "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    style_path = os.path.join(res_dir, "style.qss")
    if os.path.exists(style_path):
        with open(style_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())

    win = MainWindow(icon_path=icon_path, res_dir=res_dir, settings=settings)
    win.resize(1300, 860)
    win.show()

    # Se o sistema abriu o programa por causa de um duplo-clique num arquivo suportado,
    # o caminho do arquivo chega como argumento de linha de comando.
    initial_path = next((a for a in args if not a.startswith("--")), None)
    if initial_path and os.path.exists(initial_path):
        # Duplo-clique no Explorer não deve segurar o primeiro paint da janela
        # enquanto um PDF/CBZ grande é indexado. O arquivo começa a abrir logo
        # após o event loop entregar o primeiro frame ao Windows.
        QTimer.singleShot(35, lambda p=initial_path: win.open_path(p))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
