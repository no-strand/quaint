"""Lazy QtMultimedia video surface for Quaint.

Imported only when a real video is opened. Playback is delegated to Qt's
native multimedia backend instead of copying decoded frames through Python,
which keeps CPU/RAM overhead low and allows hardware acceleration when the
platform backend supports it.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal, QSize
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtGui import QIcon, QImage
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.i18n import tr


def _clock(ms: int) -> str:
    seconds = max(0, int(ms) // 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


class VideoView(QWidget):
    audio_enabled_changed = Signal(bool)
    volume_changed = Signal(int)
    repeat_changed = Signal(bool)
    playback_rate_changed = Signal(float)
    playback_error = Signal(str)
    ended = Signal()

    PLAYBACK_RATES = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)

    def __init__(self, *, audio_enabled=False, volume=75, playback_rate=1.0,
                 repeat_enabled=False, icons_dir=None, parent=None):
        super().__init__(parent)
        self._source_path = ""
        self._icons_dir = Path(icons_dir) if icons_dir else None
        self._play_icon = QIcon()
        self._pause_icon = QIcon()
        self._volume_icon = QIcon()
        self._muted_icon = QIcon()
        self._repeat_icon = QIcon()
        self._repeat_on_icon = QIcon()
        self._speed_down_icon = QIcon()
        self._speed_up_icon = QIcon()
        if self._icons_dir is not None:
            self._play_icon = QIcon(str(self._icons_dir / "video_play.png"))
            self._pause_icon = QIcon(str(self._icons_dir / "video_pause.png"))
            self._volume_icon = QIcon(str(self._icons_dir / "video_volume.png"))
            self._muted_icon = QIcon(str(self._icons_dir / "video_muted.png"))
            self._repeat_icon = QIcon(str(self._icons_dir / "video_repeat.png"))
            self._repeat_on_icon = QIcon(str(self._icons_dir / "video_repeat_on.png"))
            self._speed_down_icon = QIcon(str(self._icons_dir / "video_speed_down.png"))
            self._speed_up_icon = QIcon(str(self._icons_dir / "video_speed_up.png"))
        self._duration = 0
        self._seeking = False
        self._paused_by_minimize = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.video_widget = QVideoWidget(self)
        self.video_widget.setAspectRatioMode(Qt.KeepAspectRatio)
        self.video_widget.setObjectName("videoSurface")
        root.addWidget(self.video_widget, 1)

        self.controls = QWidget(self)
        self.controls.setObjectName("videoControls")
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(10, 7, 10, 7)
        row.setSpacing(8)

        self.play_button = QToolButton(self.controls)
        self.play_button.setObjectName("videoPlayButton")
        self.play_button.setIconSize(QSize(24, 24))
        self._set_play_button_state(False)
        self.play_button.setToolTip(tr("video.play"))
        self.play_button.clicked.connect(self.toggle_playback)
        row.addWidget(self.play_button)

        self.position_slider = QSlider(Qt.Horizontal, self.controls)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderPressed.connect(self._seek_started)
        self.position_slider.sliderReleased.connect(self._seek_finished)
        row.addWidget(self.position_slider, 1)

        self.time_label = QLabel("0:00 / 0:00", self.controls)
        self.time_label.setMinimumWidth(92)
        self.time_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self.time_label)

        self.repeat_button = QToolButton(self.controls)
        self.repeat_button.setObjectName("videoRepeatButton")
        self.repeat_button.setCheckable(True)
        self.repeat_button.setIconSize(QSize(24, 24))
        self.repeat_button.clicked.connect(self._repeat_button_changed)
        row.addWidget(self.repeat_button)

        self.speed_down_button = QToolButton(self.controls)
        self.speed_down_button.setObjectName("videoSpeedDownButton")
        self.speed_down_button.setIconSize(QSize(24, 24))
        if not self._speed_down_icon.isNull():
            self.speed_down_button.setIcon(self._speed_down_icon)
            self.speed_down_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            self.speed_down_button.setText("«")
            self.speed_down_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.speed_down_button.clicked.connect(lambda: self.step_playback_rate(-1))
        row.addWidget(self.speed_down_button)

        self.speed_label = QLabel("1×", self.controls)
        self.speed_label.setObjectName("videoSpeedLabel")
        self.speed_label.setMinimumWidth(44)
        self.speed_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self.speed_label)

        self.speed_up_button = QToolButton(self.controls)
        self.speed_up_button.setObjectName("videoSpeedUpButton")
        self.speed_up_button.setIconSize(QSize(24, 24))
        if not self._speed_up_icon.isNull():
            self.speed_up_button.setIcon(self._speed_up_icon)
            self.speed_up_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            self.speed_up_button.setText("»")
            self.speed_up_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.speed_up_button.clicked.connect(lambda: self.step_playback_rate(1))
        row.addWidget(self.speed_up_button)

        self.audio_button = QToolButton(self.controls)
        self.audio_button.setObjectName("videoAudioButton")
        self.audio_button.setCheckable(True)
        self.audio_button.setIconSize(QSize(24, 24))
        self.audio_button.setToolTip(tr("video.audio"))
        self.audio_button.clicked.connect(self._audio_button_changed)
        row.addWidget(self.audio_button)

        self.volume_slider = QSlider(Qt.Horizontal, self.controls)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setFixedWidth(92)
        self.volume_slider.setValue(max(0, min(100, int(volume))))
        self.volume_slider.valueChanged.connect(self._volume_changed)
        row.addWidget(self.volume_slider)
        root.addWidget(self.controls, 0)

        self.audio_output = QAudioOutput(self)
        self.player = QMediaPlayer(self)
        self.player.setVideoOutput(self.video_widget)
        self.player.positionChanged.connect(self._position_changed)
        self.player.durationChanged.connect(self._duration_changed)
        self.player.playbackStateChanged.connect(self._playback_state_changed)
        self.player.mediaStatusChanged.connect(self._media_status_changed)
        self.player.errorOccurred.connect(self._player_error)

        self.set_volume(volume, emit=False)
        self.set_audio_enabled(audio_enabled, emit=False)
        self.set_repeat_enabled(repeat_enabled, emit=False)
        self.set_playback_rate(playback_rate)
        self.retranslate_ui()

    @property
    def source_path(self):
        return self._source_path

    def open(self, path, autoplay=True):
        path = str(Path(path))
        if path != self._source_path:
            self.player.stop()
            self._source_path = path
            self.player.setSource(QUrl.fromLocalFile(path))
        if autoplay:
            self.player.play()

    def toggle_playback(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def restart(self):
        self.player.setPosition(0)
        self.player.play()

    def set_playback_rate(self, value):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 1.0
        value = max(self.PLAYBACK_RATES[0], min(self.PLAYBACK_RATES[-1], value))
        self.player.setPlaybackRate(value)
        if hasattr(self, "speed_label"):
            self.speed_label.setText(f"{value:g}×")
            self._update_speed_buttons(value)

    def step_playback_rate(self, direction):
        """Move one step through the same rates exposed by Tools > Video."""
        direction = 1 if int(direction) > 0 else -1
        current = self.playback_rate()
        nearest = min(
            range(len(self.PLAYBACK_RATES)),
            key=lambda index: abs(self.PLAYBACK_RATES[index] - current),
        )
        target_index = max(0, min(len(self.PLAYBACK_RATES) - 1, nearest + direction))
        target = self.PLAYBACK_RATES[target_index]
        if abs(target - current) < 1e-9:
            self._update_speed_buttons(target)
            return
        self.set_playback_rate(target)
        self.playback_rate_changed.emit(target)

    def _update_speed_buttons(self, value=None):
        if value is None:
            value = self.playback_rate()
        minimum = self.PLAYBACK_RATES[0]
        maximum = self.PLAYBACK_RATES[-1]
        self.speed_down_button.setEnabled(value > minimum + 1e-9)
        self.speed_up_button.setEnabled(value < maximum - 1e-9)

    def playback_rate(self):
        try:
            return float(self.player.playbackRate())
        except Exception:
            return 1.0

    def set_repeat_enabled(self, enabled, *, emit=True):
        enabled = bool(enabled)
        self.repeat_button.blockSignals(True)
        self.repeat_button.setChecked(enabled)
        self._set_repeat_button_state(enabled)
        self.repeat_button.blockSignals(False)
        if emit:
            self.repeat_changed.emit(enabled)

    def repeat_enabled(self):
        return bool(self.repeat_button.isChecked())

    def _set_repeat_button_state(self, enabled):
        icon = self._repeat_on_icon if enabled else self._repeat_icon
        if not icon.isNull():
            self.repeat_button.setIcon(icon)
            self.repeat_button.setText("")
            self.repeat_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            self.repeat_button.setIcon(QIcon())
            self.repeat_button.setText("↻✓" if enabled else "↻")
            self.repeat_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.repeat_button.setToolTip(
            tr("video.repeat_disable") if enabled else tr("video.repeat_enable")
        )

    def set_audio_enabled(self, enabled, *, emit=True):
        enabled = bool(enabled)
        # Sem áudio, desconecta a saída em vez de apenas mutá-la. Isso evita
        # manter o dispositivo de áudio ativo e reduz trabalho desnecessário
        # do backend em sessões usadas somente como visualizador silencioso.
        self.audio_output.setMuted(not enabled)
        self.player.setAudioOutput(self.audio_output if enabled else None)
        self.audio_button.blockSignals(True)
        self.audio_button.setChecked(enabled)
        self._set_audio_button_state(enabled)
        self.audio_button.blockSignals(False)
        self.volume_slider.setEnabled(enabled)
        if emit:
            self.audio_enabled_changed.emit(enabled)

    def _set_audio_button_state(self, enabled):
        audible = bool(enabled) and self.volume() > 0
        icon = self._volume_icon if audible else self._muted_icon
        if not icon.isNull():
            self.audio_button.setIcon(icon)
            self.audio_button.setText("")
            self.audio_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            self.audio_button.setIcon(QIcon())
            self.audio_button.setText("🔊" if audible else "🔇")
            self.audio_button.setToolButtonStyle(Qt.ToolButtonTextOnly)

    def audio_enabled(self):
        return not self.audio_output.isMuted()

    def set_volume(self, value, *, emit=True):
        value = max(0, min(100, int(value)))
        self.audio_output.setVolume(value / 100.0)
        self.volume_slider.blockSignals(True)
        self.volume_slider.setValue(value)
        self.volume_slider.blockSignals(False)
        if hasattr(self, "audio_button"):
            self._set_audio_button_state(self.audio_enabled())
        if emit:
            self.volume_changed.emit(value)

    def volume(self):
        return int(round(self.audio_output.volume() * 100))

    def position(self):
        return max(0, int(self.player.position()))

    def current_frame_image(self):
        """Copia o frame que o QVideoWidget está exibindo no momento.

        Usa o QVideoSink interno do próprio widget: não cria um segundo decoder,
        não busca o arquivo novamente e mantém a captura praticamente sem custo
        até o usuário acionar explicitamente Salvar frame.
        """
        try:
            sink_getter = getattr(self.video_widget, "videoSink", None)
            if sink_getter is None:
                return QImage()
            sink = sink_getter()
            if sink is None:
                return QImage()
            frame = sink.videoFrame()
            if frame is None or not frame.isValid():
                return QImage()
            image = frame.toImage()
            return image.copy() if image is not None and not image.isNull() else QImage()
        except Exception:
            return QImage()

    def set_controls_visible(self, visible):
        self.controls.setVisible(bool(visible))

    def pause_for_minimize(self):
        self._paused_by_minimize = (
            self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if self._paused_by_minimize:
            self.player.pause()

    def resume_after_minimize(self):
        if self._paused_by_minimize:
            self._paused_by_minimize = False
            self.player.play()

    def shutdown(self):
        self._paused_by_minimize = False
        self.player.stop()
        self.player.setSource(QUrl())
        self._source_path = ""

    def _repeat_button_changed(self, checked):
        self.set_repeat_enabled(bool(checked))

    def _audio_button_changed(self, checked):
        self.set_audio_enabled(bool(checked))

    def _volume_changed(self, value):
        self.set_volume(value)

    def _seek_started(self):
        self._seeking = True

    def _seek_finished(self):
        self._seeking = False
        self.player.setPosition(self.position_slider.value())

    def _position_changed(self, position):
        position = int(position)
        if not self._seeking:
            self.position_slider.setValue(position)
        self.time_label.setText(f"{_clock(position)} / {_clock(self._duration)}")

    def _duration_changed(self, duration):
        self._duration = max(0, int(duration))
        self.position_slider.setRange(0, self._duration)
        self.time_label.setText(f"{_clock(self.player.position())} / {_clock(self._duration)}")

    def _set_play_button_state(self, playing):
        icon = self._pause_icon if playing else self._play_icon
        if not icon.isNull():
            self.play_button.setIcon(icon)
            self.play_button.setText("")
            self.play_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        else:
            self.play_button.setIcon(QIcon())
            self.play_button.setText("⏸" if playing else "▶")
            self.play_button.setToolButtonStyle(Qt.ToolButtonTextOnly)

    def _playback_state_changed(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._set_play_button_state(playing)
        self.play_button.setToolTip(tr("video.pause") if playing else tr("video.play"))

    def _media_status_changed(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._set_play_button_state(False)
            self.ended.emit()

    def _player_error(self, _error, error_string):
        if error_string:
            message = str(error_string)
            self.setToolTip(message)
            self.playback_error.emit(message)

    def retranslate_ui(self):
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.play_button.setToolTip(tr("video.pause") if playing else tr("video.play"))
        self.audio_button.setToolTip(tr("video.audio"))
        self._set_repeat_button_state(self.repeat_enabled())
        self.speed_down_button.setToolTip(tr("video.speed_slower"))
        self.speed_up_button.setToolTip(tr("video.speed_faster"))
