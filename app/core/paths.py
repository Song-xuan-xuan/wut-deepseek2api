"""数据目录：token、凭据、用量库都放这里，避免污染项目目录。"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def data_dir() -> Path:
    """默认 ~/.whut-2api，可用 WHUT_DATA_DIR 覆盖。"""
    raw = os.environ.get("WHUT_DATA_DIR") or "~/.whut-2api"
    path = Path(os.path.expanduser(raw))
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_parent(path: str) -> str:
    target = Path(os.path.expanduser(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)
