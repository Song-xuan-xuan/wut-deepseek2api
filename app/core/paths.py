"""路径解析：所有运行数据都放项目目录下的 data/，可用 WHUT_DATA_DIR 覆盖。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# 旧版本把数据散在 HOME 下，首次启动时自动搬过来
LEGACY_TOKEN = "~/.whut-jwt"
LEGACY_DATA_DIR = "~/.whut-2api"


def data_dir() -> Path:
    """默认 <项目>/data，可用 WHUT_DATA_DIR 覆盖。"""
    raw = os.environ.get("WHUT_DATA_DIR") or str(ROOT / "data")
    path = Path(os.path.expanduser(raw)).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_path(path: str) -> str:
    """展开 ~；相对路径按项目根目录解析，保证"一切都留在项目里"。"""
    if not path:
        return path
    expanded = Path(os.path.expanduser(path))
    if expanded.is_absolute():
        return str(expanded)
    return str((ROOT / expanded).resolve())


def ensure_parent(path: str) -> str:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target)


def migrate_legacy() -> list[str]:
    """把旧位置的 token / 用量库搬进 data/，返回搬运记录（用于日志）。"""
    moved: list[str] = []
    target_dir = data_dir()

    def is_file_quiet(path: Path) -> bool:
        """HOME 不可读（如容器内 /root）时 is_file() 会抛 PermissionError，不能让它崩启动。"""
        try:
            return path.is_file()
        except OSError:
            return False

    token = target_dir / "token"
    legacy_token = Path(os.path.expanduser(LEGACY_TOKEN))
    if not token.exists() and is_file_quiet(legacy_token):
        try:
            shutil.copy2(legacy_token, token)
            token.chmod(0o600)
            moved.append(f"{legacy_token} → {token}")
        except OSError:
            pass

    legacy_db = Path(os.path.expanduser(LEGACY_DATA_DIR)) / "usage.db"
    db = target_dir / "usage.db"
    if not db.exists() and is_file_quiet(legacy_db):
        try:
            shutil.copy2(legacy_db, db)
            for suffix in ("-wal", "-shm"):
                sidecar = legacy_db.with_name(legacy_db.name + suffix)
                if is_file_quiet(sidecar):
                    shutil.copy2(sidecar, db.with_name(db.name + suffix))
            moved.append(f"{legacy_db} → {db}")
        except OSError:
            pass

    return moved


def shorten_path(path: str) -> str:
    """展示用：项目内的路径显示成相对路径（截图/面板更整洁）。"""
    if not path:
        return path
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return path
