"""Cache leve dos índices de páginas para coleções muito grandes.

O cache guarda apenas nomes/ordem, nunca pixels.  Ele acelera reaberturas de
pastas e CBZ/CBR enormes sem manter uma base de dados pesada.  A assinatura usa
mtime/tamanho; quando a coleção muda, o índice é descartado automaticamente.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from pathlib import Path

_CACHE_VERSION = 3
_WRITE_LOCK = threading.Lock()
_QUEUE_LOCK = threading.Lock()
_WRITE_QUEUE = queue.Queue()
_PENDING_WRITES = {}
_WRITER_THREAD = None


def _cache_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "Quaint" / "indexes"


def _signature(path: Path, kind: str):
    try:
        st = path.stat()
    except OSError:
        return None
    # Em uma pasta, mtime muda quando arquivos são adicionados/removidos ou
    # renomeados. Alterar apenas o conteúdo de uma imagem não muda a ordem.
    return {
        "version": _CACHE_VERSION,
        "kind": str(kind),
        "path": str(path.resolve()),
        "mtime_ns": int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
        "size": int(st.st_size) if path.is_file() else None,
    }


def _cache_path(path: Path, kind: str) -> Path:
    key = f"{kind}\0{path.resolve()}".encode("utf-8", "surrogatepass")
    return _cache_root() / (hashlib.sha1(key).hexdigest() + ".json")


def load_index(path, kind: str):
    path = Path(path)
    sig = _signature(path, kind)
    if sig is None:
        return None
    target = _cache_path(path, kind)
    try:
        with target.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if payload.get("signature") != sig:
            return None
        pages = payload.get("pages")
        if not isinstance(pages, list) or not pages:
            return None
        # Uma verificação barata protege contra sistemas de arquivos que não
        # atualizem mtime de diretório como esperado.
        if kind == "dir":
            sample = (pages[0], pages[-1]) if len(pages) > 1 else (pages[0],)
            if any(not (path / name).exists() for name in sample):
                return None
        return pages
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _ensure_writer_thread():
    global _WRITER_THREAD
    with _QUEUE_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        _WRITER_THREAD = threading.Thread(
            target=_writer_loop, name="QuaintIndexCache", daemon=True
        )
        _WRITER_THREAD.start()


def _writer_loop():
    """Um único daemon grava todos os índices atrasados.

    A versão anterior criava uma thread por índice. Navegar rapidamente por
    muitos CBZs/contêineres podia acumular dezenas de threads dormindo ao
    mesmo tempo. A fila coalesce gravações repetidas do mesmo caminho e mantém
    apenas um worker de baixa prioridade lógica.
    """
    while True:
        target = _WRITE_QUEUE.get()
        try:
            # Uma gravação pode ser atualizada enquanto aguarda. Releia a
            # entrada até o prazo da versão mais recente ter vencido.
            while True:
                with _QUEUE_LOCK:
                    item = _PENDING_WRITES.get(target)
                if item is None:
                    break
                sig, snapshot, due = item
                delay = due - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, 0.25))
                    continue
                with _QUEUE_LOCK:
                    latest = _PENDING_WRITES.get(target)
                    if latest is not item:
                        continue
                    _PENDING_WRITES.pop(target, None)
                try:
                    root = target.parent
                    root.mkdir(parents=True, exist_ok=True)
                    tmp = target.with_suffix(".tmp")
                    payload = {"signature": sig, "pages": snapshot}
                    with _WRITE_LOCK:
                        with tmp.open("w", encoding="utf-8") as fh:
                            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
                        os.replace(tmp, target)
                        _trim_cache(root)
                except OSError:
                    pass
                break
        finally:
            _WRITE_QUEUE.task_done()


def save_index_async(path, kind: str, pages):
    """Agenda persistência sem atrasar a primeira página.

    Há um único daemon compartilhado e gravações do mesmo índice são
    coalescidas, reduzindo threads/handles quando o usuário navega depressa.
    """
    path = Path(path)
    sig = _signature(path, kind)
    if sig is None:
        return
    # Copia somente a lista de referências (não as strings). Pastas podem ser
    # reordenadas logo depois da abertura; o worker atrasado precisa persistir
    # exatamente a ordem natural indexada, não a lista já mutada pela UI.
    snapshot = list(pages)
    if not snapshot:
        return
    target = _cache_path(path, kind)
    due = time.monotonic() + 1.0
    with _QUEUE_LOCK:
        first = target not in _PENDING_WRITES
        _PENDING_WRITES[target] = (sig, snapshot, due)
    if first:
        _WRITE_QUEUE.put(target)
    _ensure_writer_thread()

def _trim_cache(root: Path, max_files=96, max_bytes=96 * 1024 * 1024):
    """Limpeza oportunista e barata; só roda depois de uma gravação."""
    try:
        files = [p for p in root.glob("*.json") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if len(files) <= max_files and total <= max_bytes:
            return
        files.sort(key=lambda p: p.stat().st_mtime_ns)
        while files and (len(files) > max_files or total > max_bytes):
            victim = files.pop(0)
            try:
                size = victim.stat().st_size
                victim.unlink()
                total -= size
            except OSError:
                pass
    except OSError:
        pass
