"""Carregamento assíncrono e cache LRU das páginas do leitor.

A versão otimizada separa claramente três etapas:
1. leitura/descompressão + decodificação em threads de fundo;
2. cache da página-base já redimensionada;
3. ajustes de exibição/rotação sobre a página que já está em memória.

A página atual recebe prioridade maior que o pré-carregamento e as miniaturas
usam outro pool de threads, evitando que centenas de thumbs atrasem a leitura.
"""
from __future__ import annotations

import io
import os
import tempfile
import threading
import time
from collections import OrderedDict

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot
from PySide6.QtGui import QImage, QPixmap, QTransform

from app.animation_decode import (
    VIDEO_ANIMATION_EXTS,
    iter_webm_frames,
    normalize_frame_delay_ms,
    prepare_animation_frame,
)
from app.image_effects import (
    NEUTRAL_ADJUSTMENTS,
    apply_image_effects,
    normalize_adjustments,
    normalize_filter_name,
    rotate_image,
    flip_image,
)


class _LoadSignals(QObject):
    # QImage é seguro para ser construído fora da GUI; QPixmap é criado apenas
    # no slot do provider, que roda na thread principal.
    done = Signal(int, QImage, int)
    failed = Signal(int, str, int)


class _LoadTask(QRunnable):
    def __init__(self, archive, index, max_dim, adjustments=None, generation=0, filter_name="none"):
        super().__init__()
        self.archive = archive
        self.index = int(index)
        self.max_dim = max_dim
        self.adjustments = None if adjustments is None else normalize_adjustments(adjustments)
        self.generation = int(generation)
        self.filter_name = normalize_filter_name(filter_name)
        self.signals = _LoadSignals()

    @staticmethod
    def _pil_to_qimage(img: Image.Image) -> QImage:
        if "A" in img.getbands():
            rgba = img.convert("RGBA")
            width, height = rgba.size
            raw = rgba.tobytes()
            return QImage(raw, width, height, width * 4, QImage.Format_RGBA8888).copy()
        rgb = img.convert("RGB")
        width, height = rgb.size
        raw = rgb.tobytes()
        # copy() torna o QImage independente do buffer Python local.
        return QImage(raw, width, height, width * 3, QImage.Format_RGB888).copy()

    def run(self):
        try:
            # ComicArchive.load_image() já faz decode/downsample otimizado e,
            # em PDF, rasteriza diretamente perto da resolução alvo.
            img = self.archive.load_image(self.index, self.max_dim)
            if self.adjustments is not None:
                if self.adjustments != NEUTRAL_ADJUSTMENTS or self.filter_name != "none":
                    img = apply_image_effects(img, self.adjustments, self.filter_name)
            self.signals.done.emit(
                self.index, self._pil_to_qimage(img), self.generation
            )
        except Exception as e:  # noqa: BLE001
            self.signals.failed.emit(self.index, str(e), self.generation)




class _AnimationSignals(QObject):
    frame = Signal(int, QImage, int)
    started = Signal(int, int)
    failed = Signal(int, str, int)
    # index, token, animated_detected, canceled
    finished = Signal(int, int, bool, bool)


class _AnimationWorker:
    """Thread daemon dedicado somente às páginas animadas atualmente visíveis.

    Não usa QThreadPool: uma animação pode durar indefinidamente e não deve
    ocupar uma das threads reservadas para abrir/pré-carregar páginas.
    """

    def __init__(self, archive, index, token, max_dim, transform_getter):
        self.archive = archive
        self.index = int(index)
        self.token = int(token)
        self.max_dim = max_dim
        self.transform_getter = transform_getter
        self.signals = _AnimationSignals()
        self._stop = threading.Event()
        self._play = threading.Event()
        self._play.set()
        self._speed = 1.0
        self._step_lock = threading.Lock()
        self._step_request = 0
        # Histórico curto permite voltar quadros inclusive em WebM, sem
        # manter a animação/vídeo inteiro na memória.
        self._frame_history = []
        self._history_pos = -1
        self._history_bytes = 0
        self._history_limit_bytes = 48 * 1024 * 1024
        self._thread = threading.Thread(
            target=self._run,
            name=f"QuaintAnimation-{self.index}-{self.token}",
            daemon=True,
        )

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._play.set()  # acorda imediatamente se estiver pausado

    def set_paused(self, paused):
        if paused:
            self._play.clear()
        else:
            self._play.set()

    def set_speed(self, speed):
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 1.0
        self._speed = speed if speed in (0.5, 1.0, 2.0) else 1.0

    def request_step(self, direction=1):
        with self._step_lock:
            self._step_request = -1 if int(direction) < 0 else 1
        self._play.set()

    def _take_step(self):
        with self._step_lock:
            value = self._step_request
            self._step_request = 0
        return value

    def join(self, timeout=None):
        if self._thread.is_alive():
            self._thread.join(timeout)

    def _wait_playing(self):
        while not self._stop.is_set():
            if self._play.wait(0.08):
                return not self._stop.is_set()
        return False

    def _sleep_frame(self, delay_ms):
        """Espera o tempo do quadro sem contar o período em pausa."""
        remaining = max(0.0, float(delay_ms) / 1000.0 / max(self._speed, 0.01))
        while remaining > 0 and not self._stop.is_set():
            if not self._play.is_set():
                if not self._wait_playing():
                    return False
                continue
            slice_s = min(0.05, remaining)
            started = time.monotonic()
            if self._stop.wait(slice_s):
                return False
            if self._play.is_set():
                remaining -= max(0.0, time.monotonic() - started)
        return not self._stop.is_set()

    @staticmethod
    def _pil_to_qimage(img: Image.Image) -> QImage:
        if "A" in img.getbands():
            rgba = img.convert("RGBA")
            w, h = rgba.size
            raw = rgba.tobytes()
            return QImage(raw, w, h, w * 4, QImage.Format_RGBA8888).copy()
        rgb = img.convert("RGB")
        w, h = rgb.size
        raw = rgb.tobytes()
        return QImage(raw, w, h, w * 3, QImage.Format_RGB888).copy()

    def _transform(self, frame):
        adjustments, filter_name, rotation, flip_h, flip_v = self.transform_getter(self.index)
        if adjustments != NEUTRAL_ADJUSTMENTS or normalize_filter_name(filter_name) != "none":
            frame = apply_image_effects(frame, adjustments, filter_name)
        if rotation:
            frame = rotate_image(frame, rotation)
        if flip_h or flip_v:
            frame = flip_image(frame, flip_h, flip_v)
        return frame

    def _emit_frame(self, frame):
        if self._stop.is_set():
            return False
        frame = self._transform(frame)
        qimg = self._pil_to_qimage(frame)
        # Se o usuário voltou no histórico e a reprodução segue adiante,
        # descarta o ramo futuro antes de anexar o novo quadro decodificado.
        if self._history_pos < len(self._frame_history) - 1:
            removed = self._frame_history[self._history_pos + 1:]
            self._history_bytes -= sum(int(img.sizeInBytes()) for img in removed)
            self._frame_history = self._frame_history[: self._history_pos + 1]
        stored = QImage(qimg)
        self._frame_history.append(stored)
        self._history_bytes += int(stored.sizeInBytes())
        # Limite duplo: quantidade e bytes. Em animações 4K uma única cópia
        # pode ter dezenas de MB; o limite por bytes evita picos imprevisíveis.
        while len(self._frame_history) > 1 and (
            len(self._frame_history) > 8
            or self._history_bytes > self._history_limit_bytes
        ):
            old = self._frame_history.pop(0)
            self._history_bytes -= int(old.sizeInBytes())
        self._history_pos = len(self._frame_history) - 1
        self.signals.frame.emit(self.index, qimg, self.token)
        return True

    def _emit_history_step(self, direction):
        if not self._frame_history:
            return False
        target = self._history_pos + (-1 if int(direction) < 0 else 1)
        if not (0 <= target < len(self._frame_history)):
            return False
        self._history_pos = target
        self.signals.frame.emit(
            self.index, QImage(self._frame_history[target]), self.token
        )
        return True

    def _run_pillow(self, source):
        source_obj = io.BytesIO(source["data"]) if "data" in source else source["path"]
        with Image.open(source_obj) as img:
            try:
                total = int(getattr(img, "n_frames", 1) or 1)
            except Exception:
                total = 1
            if total <= 1:
                return False
            self.signals.started.emit(self.index, self.token)
            frame_index = 0
            while not self._stop.is_set():
                step = self._take_step()
                if not self._play.is_set() and step == 0:
                    if not self._wait_playing():
                        return True
                    step = self._take_step()
                if step and self._emit_history_step(step):
                    self._play.clear()
                    continue
                if step:
                    frame_index = (frame_index + step) % total
                img.seek(frame_index)
                delay = normalize_frame_delay_ms(img.info.get("duration", 100))
                frame = prepare_animation_frame(img.convert("RGBA"), self.max_dim)
                if not self._emit_frame(frame):
                    return True
                # Um passo manual mantém a animação pausada depois de emitir.
                if step:
                    self._play.clear()
                    continue
                frame_index = (frame_index + 1) % total
                if not self._sleep_frame(delay):
                    return True
            return True

    def _run_webm(self, source):
        temp_path = None
        path = source.get("path")
        if not path:
            handle = tempfile.NamedTemporaryFile(
                prefix="quaint_anim_", suffix=".webm", delete=False
            )
            try:
                handle.write(source["data"])
                temp_path = handle.name
            finally:
                handle.close()
            path = temp_path

        started = False
        try:
            while not self._stop.is_set():
                got_frame = False
                frames = iter_webm_frames(path, self.max_dim)
                try:
                    for frame, delay in frames:
                        got_frame = True
                        if not started:
                            started = True
                            self.signals.started.emit(self.index, self.token)
                        step = self._take_step()
                        if not self._play.is_set() and step == 0:
                            if not self._wait_playing():
                                return started
                            step = self._take_step()
                        if step and self._emit_history_step(step):
                            self._play.clear()
                            continue
                        if not self._emit_frame(frame):
                            return started
                        if step:
                            # WebM é um stream sequencial; próximo quadro é exato.
                            # Para "anterior", mantém o player pausado e reinicia
                            # a partir do quadro seguinte disponível sem tocar em memória massiva.
                            self._play.clear()
                            continue
                        if not self._sleep_frame(delay):
                            return started
                finally:
                    try:
                        frames.close()
                    except Exception:
                        pass
                if not got_frame:
                    return False
            return started
        finally:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _run(self):
        animated = False
        canceled = False
        try:
            source = self.archive.animation_source(self.index)
            if source is None:
                return
            suffix = str(source.get("suffix") or "").lower()
            if suffix in VIDEO_ANIMATION_EXTS:
                animated = self._run_webm(source)
            else:
                animated = self._run_pillow(source)
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                self.signals.failed.emit(self.index, str(e), self.token)
        finally:
            canceled = self._stop.is_set()
            self.signals.finished.emit(
                self.index, self.token, bool(animated), bool(canceled)
            )

def _pixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    """Converte um QPixmap já em memória para PIL preservando transparência."""
    source = pixmap.toImage()
    if source.hasAlphaChannel():
        qimg = source.convertToFormat(QImage.Format_RGBA8888)
        width, height = qimg.width(), qimg.height()
        stride = qimg.bytesPerLine()
        raw = qimg.bits().tobytes()
        return Image.frombuffer(
            "RGBA", (width, height), raw, "raw", "RGBA", stride, 1
        ).copy()
    qimg = source.convertToFormat(QImage.Format_RGB888)
    width, height = qimg.width(), qimg.height()
    stride = qimg.bytesPerLine()
    raw = qimg.bits().tobytes()
    return Image.frombuffer(
        "RGB", (width, height), raw, "raw", "RGB", stride, 1
    ).copy()


def _pil_to_pixmap(image: Image.Image) -> QPixmap:
    if "A" in image.getbands():
        img = image.convert("RGBA")
        width, height = img.size
        raw = img.tobytes()
        qimg = QImage(raw, width, height, width * 4, QImage.Format_RGBA8888).copy()
    else:
        img = image.convert("RGB")
        width, height = img.size
        raw = img.tobytes()
        qimg = QImage(raw, width, height, width * 3, QImage.Format_RGB888).copy()
    return QPixmap.fromImage(qimg)


class _FullResBridge(QObject):
    """Entrega o resultado de um worker para a thread gráfica com segurança.

    ``_LoadTask`` roda no QThreadPool. Conectar seu sinal diretamente a uma
    função Python local pode fazer o callback executar na thread do worker;
    criar QPixmap/QDialog dali é comportamento indefinido no Qt e pode encerrar
    o processo sem traceback. Este QObject vive na thread do PixmapProvider e
    força a etapa GUI a acontecer nela.
    """

    def __init__(self, provider, task, callback, *, as_qimage=False):
        super().__init__(provider)
        self.provider = provider
        self.task = task
        self.callback = callback
        self.as_qimage = bool(as_qimage)
        task.signals.done.connect(self._on_done)
        task.signals.failed.connect(self._on_failed)

    def _finish(self):
        provider = self.provider
        if provider is not None:
            provider._full_res_requests.discard(self)
        self.callback = None
        self.task = None
        self.deleteLater()

    @Slot(int, QImage, int)
    def _on_done(self, index, qimg, generation):
        provider = self.provider
        callback = self.callback
        try:
            if (
                provider is None
                or callback is None
                or provider._closed
                or generation != provider._generation
                or qimg.isNull()
            ):
                return

            rotation = provider.rotation_for(index)
            flip_h, flip_v = provider.flip_for(index)
            transform = QTransform()
            if rotation:
                transform.rotate(rotation)
            if flip_h or flip_v:
                transform.scale(-1 if flip_h else 1, -1 if flip_v else 1)

            if self.as_qimage:
                # QImage usa compartilhamento implícito: esta cópia de objeto
                # mantém os pixels vivos sem duplicar o buffer inteiro.
                payload = QImage(qimg)
                if not transform.isIdentity():
                    payload = payload.transformed(transform)
            else:
                # QPixmap é criado somente aqui, na thread gráfica.
                payload = QPixmap.fromImage(qimg)
                if not transform.isIdentity():
                    payload = payload.transformed(transform)

            callback(payload)
        finally:
            self._finish()

    @Slot(int, str, int)
    def _on_failed(self, _index, _error, _generation):
        self._finish()


class PixmapProvider(QObject):
    """Carrega páginas sob demanda com prioridade e cache limitado por memória."""

    pixmap_ready = Signal(int, QPixmap)
    animation_frame_ready = Signal(int, QPixmap)
    animation_state_changed = Signal(bool, bool)  # há animação ativa, pausada
    adjustments_changed = Signal()

    def __init__(
        self,
        archive,
        cache_size=10,
        max_dim=2200,
        parent=None,
        *,
        workers=None,
        max_cache_bytes=96 * 1024 * 1024,
        display_cache_size=2,
    ):
        super().__init__(parent)
        self.archive = archive
        self.cache = OrderedDict()          # index -> QPixmap base
        self._display_cache = OrderedDict() # (index, ajustes, rotação) -> QPixmap
        self.cache_size = max(2, int(cache_size))
        self.max_dim = max_dim
        self.max_cache_bytes = max(16 * 1024 * 1024, int(max_cache_bytes))
        self.display_cache_size = max(0, int(display_cache_size))
        self._cache_bytes = 0

        # Dois pools: páginas pedidas pelo usuário nunca ficam atrás de uma
        # fila de pré-carregamento. Isso é importante ao saltar milhares de
        # páginas em uma coleção enorme.
        cpu = os.cpu_count() or 4
        if workers is None:
            foreground_workers = 2
            # Pré-carregar demais disputa CPU, disco e descompressão com a página
            # que o usuário acabou de pedir. Dois workers dão boa antecipação
            # sem criar picos de memória em imagens 4K/8K.
            prefetch_workers = min(2, max(1, cpu // 3))
        else:
            foreground_workers = max(1, int(workers))
            prefetch_workers = max(1, min(2, int(workers)))

        self.pool = QThreadPool(self)  # foreground / clique / página atual
        self.pool.setMaxThreadCount(foreground_workers)
        self.pool.setExpiryTimeout(12_000)
        self.prefetch_pool = QThreadPool(self)
        self.prefetch_pool.setMaxThreadCount(prefetch_workers)
        self.prefetch_pool.setExpiryTimeout(12_000)

        # token -> (tipo, task). Guardar a task permite promover um preload
        # ainda enfileirado para o pool foreground quando o usuário chega nela.
        self._pending = {}
        self._generation = 0
        self.adjustments = normalize_adjustments()
        self.filter_name = "none"
        self._rotations = {}
        self._flips = {}  # index -> (horizontal, vertical)

        # Animações são criadas apenas para páginas efetivamente visíveis.
        # Miniaturas e preload nunca chamam set_animation_indices(), portanto
        # permanecem estáticos e baratos mesmo em pastas com milhares de GIFs.
        self._animation_workers = {}  # index -> (token, worker)
        self._animation_desired = set()
        self._animation_started = set()
        self._animation_static = set()
        self._animation_serial = 0
        self._animation_paused = False
        self._animation_speed = 1.0
        self._closed = False
        # Mantém bridges e QRunnables de operações em resolução integral vivos
        # até a entrega do resultado na thread gráfica.
        self._full_res_requests = set()

    # ------------------------------------------------------------- caches --
    @staticmethod
    def _pixmap_cost(pixmap: QPixmap) -> int:
        return max(1, pixmap.width()) * max(1, pixmap.height()) * 4

    def _adjustments_are_neutral(self):
        return self.adjustments == NEUTRAL_ADJUSTMENTS

    def _display_cache_key(self, index):
        return (int(index), self.adjustments, self.filter_name, self.rotation_for(index), self.flip_for(index))

    def _trim_display_cache(self):
        if self.display_cache_size <= 0:
            self._display_cache.clear()
            return
        while len(self._display_cache) > self.display_cache_size:
            self._display_cache.popitem(last=False)

    def _drop_display_for_index(self, index):
        for key in [k for k in self._display_cache if k[0] == index]:
            self._display_cache.pop(key, None)

    def _put_base(self, index, pixmap):
        old = self.cache.pop(index, None)
        if old is not None:
            self._cache_bytes -= self._pixmap_cost(old)
        self.cache[index] = pixmap
        self._cache_bytes += self._pixmap_cost(pixmap)
        self.cache.move_to_end(index)

        while self.cache and (
            len(self.cache) > self.cache_size
            or self._cache_bytes > self.max_cache_bytes
        ):
            removed_index, removed = self.cache.popitem(last=False)
            self._cache_bytes -= self._pixmap_cost(removed)
            self._drop_display_for_index(removed_index)

    def _display_pixmap(self, index, base_pixmap):
        key = self._display_cache_key(index)
        cached = self._display_cache.get(key)
        if cached is not None:
            self._display_cache.move_to_end(key)
            return cached

        out = base_pixmap
        if (not self._adjustments_are_neutral()) or self.filter_name != "none":
            pil = _pixmap_to_pil(base_pixmap)
            out = _pil_to_pixmap(apply_image_effects(pil, self.adjustments, self.filter_name))

        rotation = self.rotation_for(index)
        if rotation:
            out = out.transformed(QTransform().rotate(rotation))
        flip_h, flip_v = self.flip_for(index)
        if flip_h or flip_v:
            out = out.transformed(QTransform().scale(-1 if flip_h else 1, -1 if flip_v else 1))

        if self.display_cache_size > 0:
            self._display_cache[key] = out
            self._display_cache.move_to_end(key)
            self._trim_display_cache()
        return out

    # ------------------------------------------------------------ loading --
    def get(self, index, priority=100):
        """Retorna a página do cache ou inicia seu carregamento em alta prioridade."""
        if self._closed:
            return None
        if hasattr(self.archive, "is_text_page") and self.archive.is_text_page(index):
            return None
        if index in self.cache:
            self.cache.move_to_end(index)
            return self._display_pixmap(index, self.cache[index])
        self._request(index, priority=priority, foreground=True)
        return None

    def _request(self, index, priority=0, foreground=False):
        if self._closed or index < 0 or index >= self.archive.count():
            return
        if hasattr(self.archive, "is_text_page") and self.archive.is_text_page(index):
            return
        if index in self.cache:
            return
        token = (int(index), self._generation)
        pending = self._pending.get(token)
        if pending is not None:
            kind, task = pending
            if foreground and kind == "prefetch":
                # Se ainda não começou, remove da fila lenta e promove. Se já
                # está executando, deixamos terminar para não decodificar duas
                # vezes a mesma imagem gigante.
                try:
                    promoted = self.prefetch_pool.tryTake(task)
                except Exception:
                    promoted = False
                if promoted:
                    self._pending.pop(token, None)
                else:
                    return
            else:
                return

        task = _LoadTask(
            self.archive, index, self.max_dim,
            adjustments=None, generation=self._generation,
        )
        task.signals.done.connect(self._on_done)
        task.signals.failed.connect(self._on_failed)
        kind = "foreground" if foreground else "prefetch"
        self._pending[token] = (kind, task)
        target_pool = self.pool if foreground else self.prefetch_pool
        target_pool.start(task, int(priority))

    def _on_failed(self, index, _err, generation):
        self._pending.pop((index, generation), None)

    def _on_done(self, index, qimg, generation):
        self._pending.pop((index, generation), None)
        if self._closed or generation != self._generation or qimg.isNull():
            return
        pix = QPixmap.fromImage(qimg)
        self._put_base(index, pix)
        # Se o player animado já começou, não deixe o primeiro quadro estático
        # chegar atrasado e piscar sobre a animação. O cache-base ainda é
        # preenchido para resize/salvamento/preload.
        if index not in self._animation_started:
            self.pixmap_ready.emit(index, self._display_pixmap(index, pix))

    def _cancel_stale_prefetch(self, keep_indices):
        """Remove da fila preloads antigos após saltos rápidos de página."""
        keep = {int(i) for i in keep_indices}
        for token, pending in list(self._pending.items()):
            kind, task = pending
            if kind != "prefetch" or int(token[0]) in keep:
                continue
            try:
                removed = self.prefetch_pool.tryTake(task)
            except Exception:
                removed = False
            if removed:
                self._pending.pop(token, None)

    def preload_around(self, index, radius=4, backward=None):
        """Pré-carrega primeiro as próximas páginas e depois as anteriores.

        O leitor normalmente avança, então páginas à frente ganham prioridade.
        ``backward`` permite manter menos páginas para trás sem perder o retorno
        instantâneo para a página anterior.
        """
        if self._closed:
            return
        radius = max(0, int(radius))
        backward = min(radius, 2) if backward is None else max(0, int(backward))
        count = self.archive.count()
        keep = {int(index)}
        keep.update(i for i in range(int(index) + 1, min(count, int(index) + radius + 1)))
        keep.update(i for i in range(max(0, int(index) - backward), int(index)))
        self._cancel_stale_prefetch(keep)

        # Próximas páginas: prioridade decrescente conforme a distância.
        for distance in range(1, radius + 1):
            i = int(index) + distance
            if i < self.archive.count():
                self._request(i, priority=max(5, 55 - distance * 5), foreground=False)
        # Páginas anteriores recebem prioridade menor.
        for distance in range(1, backward + 1):
            i = int(index) - distance
            if i >= 0:
                self._request(i, priority=max(1, 20 - distance * 3), foreground=False)

    def preload_indices(self, indices, base_priority=35):
        """Pré-carrega uma sequência explícita, útil no modo contínuo."""
        for pos, index in enumerate(indices):
            self._request(int(index), priority=max(1, int(base_priority) - pos), foreground=False)

    def full_res(self, index, callback, *, max_dim=None, as_qimage=False):
        """Carrega uma página sem bloquear a UI e entrega na thread gráfica.

        ``max_dim`` permite que ferramentas apenas visuais (como comparação)
        limitem o custo de memória. ``as_qimage=True`` evita a cópia extra de
        memória de um QPixmap em operações como recorte, preservando a
        resolução original para salvar a seleção.
        """
        if self._closed:
            return
        task = _LoadTask(
            self.archive, index, max_dim=max_dim, adjustments=self.adjustments,
            generation=self._generation, filter_name=self.filter_name,
        )
        bridge = _FullResBridge(
            self, task, callback, as_qimage=as_qimage
        )
        self._full_res_requests.add(bridge)
        self.pool.start(task, 100)

    # ---------------------------------------------------------- animation --
    def _animation_transform_settings(self, index):
        flip_h, flip_v = self.flip_for(index)
        return self.adjustments, self.filter_name, self.rotation_for(index), flip_h, flip_v

    def _emit_animation_state(self):
        self.animation_state_changed.emit(
            bool(self._animation_workers), bool(self._animation_paused)
        )

    def set_animation_indices(self, indices):
        """Ativa loop somente para as páginas visíveis informadas.

        O teste inicial é O(1), baseado na extensão. Descobrir se um PNG/WebP
        candidato realmente possui mais de um quadro acontece na thread do
        player, nunca durante a navegação da GUI.
        """
        if self._closed:
            return
        desired = set()
        for raw in indices or ():
            try:
                index = int(raw)
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= self.archive.count():
                continue
            if index in self._animation_static:
                continue
            try:
                if self.archive.animation_candidate(index):
                    desired.add(index)
            except Exception:
                continue

        old_desired = set(self._animation_desired)
        if desired != old_desired:
            # Pausa é intencionalmente local à página/spread atual. Ao navegar
            # para outra animação, ela volta a iniciar em loop automaticamente.
            self._animation_paused = False
        self._animation_desired = desired

        # Para imediatamente páginas que saíram de cena. Não fazemos join aqui
        # para não travar uma troca rápida de página; os threads são daemon e
        # obedecem ao stop event em intervalos curtos.
        for index in list(self._animation_workers):
            if index not in desired:
                _token, worker = self._animation_workers.pop(index)
                worker.stop()
                self._animation_started.discard(index)

        for index in sorted(desired):
            if index in self._animation_workers or index in self._animation_static:
                continue
            self._animation_serial += 1
            token = self._animation_serial
            worker = _AnimationWorker(
                self.archive,
                index,
                token,
                self.max_dim,
                self._animation_transform_settings,
            )
            worker.set_paused(self._animation_paused)
            worker.set_speed(self._animation_speed)
            worker.signals.frame.connect(self._on_animation_frame)
            worker.signals.started.connect(self._on_animation_started)
            worker.signals.failed.connect(self._on_animation_failed)
            worker.signals.finished.connect(self._on_animation_finished)
            self._animation_workers[index] = (token, worker)
            worker.start()
        self._emit_animation_state()

    def _animation_worker_matches(self, index, token):
        current = self._animation_workers.get(int(index))
        return current is not None and current[0] == int(token)

    def _on_animation_started(self, index, token):
        if self._closed or not self._animation_worker_matches(index, token):
            return
        self._animation_started.add(int(index))
        self._emit_animation_state()

    def _on_animation_frame(self, index, qimg, token):
        if (
            self._closed
            or int(index) not in self._animation_desired
            or not self._animation_worker_matches(index, token)
            or qimg.isNull()
        ):
            return
        self.animation_frame_ready.emit(int(index), QPixmap.fromImage(qimg))

    def _on_animation_failed(self, index, _error, token):
        # O primeiro quadro estático já foi carregado pelo caminho normal; uma
        # falha de codec de animação não deve quebrar a leitura da página.
        if self._animation_worker_matches(index, token):
            self._animation_started.discard(int(index))

    def _on_animation_finished(self, index, token, animated, canceled):
        if not self._animation_worker_matches(index, token):
            return
        self._animation_workers.pop(int(index), None)
        self._animation_started.discard(int(index))
        if not canceled and not animated:
            # PNG/WebP/TIFF estático: lembra durante esta sessão para nunca
            # tentar sondá-lo novamente ao voltar à página.
            self._animation_static.add(int(index))
        self._emit_animation_state()

    def has_animation_activity(self):
        """True enquanto a página atual é animada ou está sendo sondada."""
        return bool(self._animation_workers)

    def has_running_animation(self):
        return bool(self._animation_started)

    def animation_paused(self):
        return bool(self._animation_paused)

    def toggle_animation_pause(self):
        if not self._animation_workers:
            return None
        self._animation_paused = not self._animation_paused
        for _token, worker in list(self._animation_workers.values()):
            worker.set_paused(self._animation_paused)
        self._emit_animation_state()
        return self._animation_paused

    def set_animation_speed(self, speed):
        try:
            speed = float(speed)
        except (TypeError, ValueError):
            speed = 1.0
        self._animation_speed = speed if speed in (0.5, 1.0, 2.0) else 1.0
        for _token, worker in list(self._animation_workers.values()):
            worker.set_speed(self._animation_speed)

    def step_animation(self, direction=1):
        if not self._animation_workers:
            return False
        self._animation_paused = True
        for _token, worker in list(self._animation_workers.values()):
            worker.set_paused(True)
            worker.request_step(direction)
        self._emit_animation_state()
        return True

    def stop_animations(self, wait=False):
        workers = list(self._animation_workers.values())
        self._animation_workers.clear()
        self._animation_desired.clear()
        self._animation_started.clear()
        for _token, worker in workers:
            worker.stop()
        if wait:
            for _token, worker in workers:
                worker.join(0.20)
        self._emit_animation_state()

    # ------------------------------------------------------------ display --
    def set_adjustments(self, *values):
        """Atualiza ajustes sem invalidar nem reler as páginas-base em cache."""
        new_adj = normalize_adjustments(values)
        if new_adj == self.adjustments:
            return
        self.adjustments = new_adj
        self._display_cache.clear()
        self.adjustments_changed.emit()

    def set_filter(self, name):
        new_name = normalize_filter_name(name)
        if new_name == self.filter_name:
            return
        self.filter_name = new_name
        self._display_cache.clear()
        self.adjustments_changed.emit()

    def rotation_for(self, index):
        return int(self._rotations.get(int(index), 0)) % 360

    def flip_for(self, index):
        value = self._flips.get(int(index), (False, False))
        return bool(value[0]), bool(value[1])

    def flip_pages(self, indices, *, horizontal=False, vertical=False):
        changed = set()
        for raw_index in indices:
            if raw_index is None:
                continue
            index = int(raw_index)
            if index < 0 or index >= self.archive.count():
                continue
            if hasattr(self.archive, "is_text_page") and self.archive.is_text_page(index):
                continue
            h, v = self.flip_for(index)
            if horizontal:
                h = not h
            if vertical:
                v = not v
            if h or v:
                self._flips[index] = (h, v)
            else:
                self._flips.pop(index, None)
            changed.add(index)
        if changed:
            for index in changed:
                self._drop_display_for_index(index)
            self.adjustments_changed.emit()

    def rotate_pages(self, indices, clockwise_degrees):
        changed_indices = set()
        for raw_index in indices:
            if raw_index is None:
                continue
            index = int(raw_index)
            if index < 0 or index >= self.archive.count():
                continue
            if hasattr(self.archive, "is_text_page") and self.archive.is_text_page(index):
                continue
            current = self.rotation_for(index)
            new_value = (current + int(clockwise_degrees)) % 360
            if new_value:
                self._rotations[index] = new_value
            else:
                self._rotations.pop(index, None)
            changed_indices.add(index)
        if changed_indices:
            for index in changed_indices:
                self._drop_display_for_index(index)
            self.adjustments_changed.emit()

    # ------------------------------------------------------------ lifecycle --
    def shutdown(self):
        """Cancela trabalho ainda não iniciado e libera caches imediatamente."""
        if self._closed:
            return
        # Garante que nenhum decoder de GIF/WebM continue usando o arquivo
        # enquanto o ComicArchive é fechado logo em seguida.
        self.stop_animations(wait=True)
        self._closed = True
        self._generation += 1
        try:
            self.pool.clear()
        except Exception:
            pass
        try:
            self.prefetch_pool.clear()
        except Exception:
            pass
        self._pending.clear()
        # Não apagamos à força bridges cujo QRunnable já está executando; eles
        # permanecem filhos deste provider até o sinal final e ignoram o
        # callback porque ``_closed`` já está ativo.
        self.cache.clear()
        self._display_cache.clear()
        self._cache_bytes = 0
