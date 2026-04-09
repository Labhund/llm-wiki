import os
from pathlib import Path

import pytest

from llm_wiki.daemon.lifecycle import (
    socket_path_for,
    pidfile_path_for,
    write_pidfile,
    read_pidfile,
    is_process_alive,
    cleanup_stale,
)
from llm_wiki.vault import _state_dir_for


def test_paths_derived_from_vault(tmp_path: Path):
    sock = socket_path_for(tmp_path)
    pid = pidfile_path_for(tmp_path)
    state = _state_dir_for(tmp_path)
    assert sock.parent == state
    assert pid.parent == state
    assert sock.name == "daemon.sock"
    assert pid.name == "daemon.pid"


def test_write_read_pidfile(tmp_path: Path):
    pidfile = tmp_path / "test.pid"
    write_pidfile(pidfile, 12345)
    assert read_pidfile(pidfile) == 12345


def test_read_missing_pidfile(tmp_path: Path):
    assert read_pidfile(tmp_path / "nope.pid") is None


def test_is_process_alive():
    assert is_process_alive(os.getpid())
    assert not is_process_alive(9999999)


def test_cleanup_stale(tmp_path: Path):
    sock = tmp_path / "daemon.sock"
    pid = tmp_path / "daemon.pid"
    sock.touch()
    pid.write_text("99999")
    cleanup_stale(sock, pid)
    assert not sock.exists()
    assert not pid.exists()


def test_cleanup_missing_files(tmp_path: Path):
    cleanup_stale(tmp_path / "nope.sock", tmp_path / "nope.pid")


@pytest.mark.asyncio
async def test_run_refuses_to_start_when_daemon_already_running(tmp_path):
    """daemon.__main__.run() exits with a clear error if a live daemon holds the pidfile."""
    (tmp_path / "wiki").mkdir()
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "config.yaml").write_text("")

    from llm_wiki.daemon.lifecycle import pidfile_path_for, write_pidfile
    import os

    pid_path = pidfile_path_for(tmp_path)
    # Write our own PID — we are definitely alive
    write_pidfile(pid_path, os.getpid())

    try:
        from llm_wiki.daemon.__main__ import run
        with pytest.raises(SystemExit) as exc_info:
            await run(tmp_path)
        assert exc_info.value.code != 0
    finally:
        pid_path.unlink(missing_ok=True)
