"""Registro sob demanda de codecs modernos para Pillow.

Os plugins de HEIF/AVIF/JXL carregam DLLs nativas e podem acrescentar tempo e
memória à inicialização. O Quaint só os importa quando encontra uma extensão
que realmente precisa deles. O registro é thread-safe e acontece no máximo
uma vez por família durante a sessão.
"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_LOADED = set()


def _mark_once(key: str) -> bool:
    with _LOCK:
        if key in _LOADED:
            return False
        _LOADED.add(key)
        return True


def ensure_pillow_codec(suffix: str):
    """Carrega apenas o plugin necessário à extensão informada."""
    suffix = str(suffix or "").lower()

    if suffix in {".heic", ".heif"} and _mark_once("heif"):
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        return

    if suffix == ".avif":
        # Pillow recente pode ter AVIF nativo. Se houver plugins instalados,
        # carregá-los aqui continua sendo fallback sem penalizar outros formatos.
        if _mark_once("avif-heif"):
            try:
                import pillow_heif
                register_avif = getattr(pillow_heif, "register_avif_opener", None)
                if register_avif is not None:
                    register_avif()
            except Exception:
                pass
        if _mark_once("avif-plugin"):
            try:
                import pillow_avif  # noqa: F401
            except Exception:
                pass
        return

    if suffix == ".jxl" and _mark_once("jxl"):
        try:
            import pillow_jxl  # noqa: F401
        except Exception:
            pass


def register_optional_pillow_plugins():
    """Compatibilidade: registra todos os codecs explicitamente.

    O fluxo normal do leitor usa :func:`ensure_pillow_codec` e não chama esta
    função na inicialização.
    """
    for suffix in (".heic", ".avif", ".jxl"):
        ensure_pillow_codec(suffix)
