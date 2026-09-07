from pathlib import Path


def test_slideshow_has_timer_intervals_repeat_and_shuffle():
    source = Path('app/reader_window.py').read_text(encoding='utf-8')
    assert 'self._slideshow_timer = QTimer(self)' in source
    assert 'for seconds in (1, 2, 5, 10)' in source
    assert 'def _slideshow_tick' in source
    assert '_slideshow_shuffle' in source
    assert '_slideshow_repeat' in source
