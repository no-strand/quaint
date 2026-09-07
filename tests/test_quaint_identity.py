from pathlib import Path
import json


def test_project_identity_is_quaint():
    spec = Path('Quaint.spec')
    assert spec.is_file()
    assert 'name="Quaint"' in spec.read_text(encoding='utf-8')
    assert 'QSettings("Quaint", "Quaint")' in Path('app/settings.py').read_text(encoding='utf-8')
    assert 'app.setOrganizationName("Quaint")' in Path('main.py').read_text(encoding='utf-8')
    for filename in ('en_US.json', 'pt_BR.json', 'es_ES.json'):
        data = json.loads((Path('locales') / filename).read_text(encoding='utf-8'))
        assert data['app.title'] == 'Quaint'
        assert 'Quaint' in data['about.title']


def test_settings_cache_and_registration_use_only_quaint_identity():
    settings = Path('app/settings.py').read_text(encoding='utf-8')
    cache = Path('app/index_cache.py').read_text(encoding='utf-8')
    registration = Path('app/win_registration.py').read_text(encoding='utf-8')

    assert settings.count('QSettings(') == 1
    assert 'base / "Quaint" / "indexes"' in cache
    assert 'legacy_prog_ids' not in registration
    for prog_id in ('Quaint.ComicFile', 'Quaint.EpubFile', 'Quaint.PdfFile', 'Quaint.ImageFile'):
        assert prog_id in registration
