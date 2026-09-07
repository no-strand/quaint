from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    settings = (root / "app" / "settings.py").read_text(encoding="utf-8")
    reader = (root / "app" / "reader_window.py").read_text(encoding="utf-8")

    assert 'def last_open_directory(self):' in settings
    assert 'def set_last_open_directory(self, path):' in settings
    assert '"last_open_directory"' in settings

    file_block = reader.split("def open_file_dialog(self):", 1)[1].split("def open_folder_dialog", 1)[0]
    folder_block = reader.split("def open_folder_dialog(self):", 1)[1].split("def open_path", 1)[0]
    assert "self.settings.last_open_directory()" in file_block
    assert "self.settings.set_last_open_directory(path)" in file_block
    assert "initial_dir" in file_block
    assert "self.settings.last_open_directory()" in folder_block
    assert "self.settings.set_last_open_directory(path)" in folder_block
    assert "initial_dir" in folder_block

    print("Último diretório de Abrir arquivo/pasta: OK")


if __name__ == "__main__":
    main()
