from pathlib import Path


def test_windows_build_does_not_depend_on_cairo():
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    spec = (root / "Quaint.spec").read_text(encoding="utf-8").lower()
    archive = (root / "app" / "archive.py").read_text(encoding="utf-8").lower()

    assert "cairosvg" not in requirements
    assert "cairosvg" not in spec
    assert "import cairosvg" not in archive
    assert "pyside6.qtsvg" in spec
    assert "from pyside6.qtsvg import qsvgrenderer" in archive


def test_build_script_cleans_pyinstaller_cache():
    root = Path(__file__).resolve().parents[1]
    script = (root / "build" / "build_exe.bat").read_text(encoding="utf-8").lower()
    assert "pyinstaller --noconfirm --clean quaint.spec" in script
