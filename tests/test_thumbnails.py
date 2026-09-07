from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "thumbnail_panel.py").read_text(encoding="utf-8")

    # Geometria continua fixa (corrige o bug visual anterior), mas sem criar
    # um QListWidgetItem por página.
    assert "THUMB_ICON_SIZE = QSize(216, 296)" in source
    assert "THUMB_GRID_SIZE = QSize(236, 334)" in source
    assert "self.setGridSize(THUMB_GRID_SIZE)" in source
    assert "class _ThumbnailModel(QAbstractListModel)" in source
    assert "class ThumbnailPanel(QListView)" in source
    assert "self.setLayoutMode(QListView.Batched)" in source
    assert "self.setBatchSize(128)" in source
    assert "QListWidgetItem(" not in source

    # Ícones continuam lazy e o cache visual é limitado.
    assert "def _request_visible(self):" in source
    assert "_ICON_CACHE_LIMIT = 192" in source
    assert "while len(self._icons) > _ICON_CACHE_LIMIT" in source
    assert "def showEvent(" in source and "def resizeEvent(" in source

    print("Miniaturas virtualizadas: OK")


if __name__ == "__main__":
    main()
