"""Helpers leves para detectar mudanças externas sem polling contínuo."""
from __future__ import annotations

import os
from pathlib import Path

# Este helper é importado pela janela principal no startup. Usar apenas as
# definições leves evita puxar Pillow/archive/py7zr antes de abrir conteúdo.
from app.format_defs import SUPPORTED_FILE_EXTS, is_container_path


def file_signature(path):
    """Assinatura O(1) de um arquivo para detectar substituição/modificação."""
    try:
        st = Path(path).stat()
        return (
            int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
            int(st.st_size),
        )
    except OSError:
        return None


def folder_snapshot(folder):
    """Mapeia arquivos suportados diretos para (mtime_ns, tamanho).

    Mantida para testes/ferramentas auxiliares. O leitor principal não cria
    mais este mapa durante a abertura de uma pasta: QFileSystemWatcher já
    informa quando há alteração, evitando milhares de stat() no caminho crítico.
    """
    folder = Path(folder)
    result = {}
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                path = Path(entry.name)
                if path.suffix.lower() not in SUPPORTED_FILE_EXTS and not is_container_path(entry.name):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                result[entry.name] = (
                    int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                    int(st.st_size),
                )
    except OSError:
        return {}
    return result
