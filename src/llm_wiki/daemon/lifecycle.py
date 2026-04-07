from __future__ import annotations

import os
from pathlib import Path

from llm_wiki.vault import _state_dir_for


def socket_path_for(vault_root: Path) -> Path:
    """Get the daemon socket path for a vault."""
    return _state_dir_for(vault_root) / "daemon.sock"


def pidfile_path_for(vault_root: Path) -> Path:
    """Get the daemon pidfile path for a vault."""
    return _state_dir_for(vault_root) / "daemon.pid"


def write_pidfile(pidfile: Path, pid: int) -> None:
    """Write PID to file."""
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(str(pid))


def read_pidfile(pidfile: Path) -> int | None:
    """Read PID from file. Returns None if missing or invalid."""
    if not pidfile.exists():
        return None
    try:
        return int(pidfile.read_text().strip())
    except (ValueError, OSError):
        return None


def is_process_alive(pid: int) -> bool:
    """Check if a process with given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_daemon_running(vault_root: Path) -> bool:
    """Check if a daemon is running for this vault."""
    pid = read_pidfile(pidfile_path_for(vault_root))
    if pid is None:
        return False
    if not is_process_alive(pid):
        cleanup_stale(socket_path_for(vault_root), pidfile_path_for(vault_root))
        return False
    return True


def cleanup_stale(socket_path: Path, pidfile: Path) -> None:
    """Remove stale socket and pidfile."""
    for f in (socket_path, pidfile):
        try:
            f.unlink()
        except FileNotFoundError:
            pass
