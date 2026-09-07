from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    views = (root / "app" / "views.py").read_text(encoding="utf-8")
    reader = (root / "app" / "reader_window.py").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")

    # O scroll não pode mais emitir pedidos de troca de página. Sem um
    # wheelEvent customizado, QScrollArea mantém seu comportamento nativo:
    # rolar a barra/área visível.
    assert "page_step_requested" not in views
    assert "_wheel_step_or_none" not in views
    assert "def wheelEvent(" not in views
    assert "_on_wheel_page_step" not in reader

    # Atalhos solicitados, mantendo também os já existentes.
    assert 'QKeySequence("<")' in reader
    assert 'QKeySequence(">")' in reader
    assert "QKeySequence(Qt.Key_Return)" in reader
    assert "QKeySequence(Qt.Key_Enter)" in reader
    assert "QKeySequence(Qt.Key_Backspace)" in reader
    assert "QKeySequence(Qt.Key_Left)" in reader
    assert "QKeySequence(Qt.Key_Right)" in reader

    assert "Scroll do mouse somente para rolagem" in readme
    print("Navegação por teclado/scroll: OK")


if __name__ == "__main__":
    main()
