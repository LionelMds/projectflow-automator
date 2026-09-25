from __future__ import annotations

import unicodedata
from pathlib import Path


def folder_key(name: str) -> str:
    """Compare folder names like Windows users type them: without accents or case."""
    decomposed = unicodedata.normalize("NFKD", name.strip())
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def find_child_directory(parent: Path, name: str) -> Path | None:
    """Find ``Plan d'exécution`` when looking for ``Plan d'execution`` (or another case)."""
    wanted = folder_key(name)
    try:
        children = sorted(parent.iterdir())
    except OSError:
        return None
    return next(
        (child for child in children if child.is_dir() and folder_key(child.name) == wanted),
        None,
    )
