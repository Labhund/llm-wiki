"""Entry point: python -m llm_wiki.daemon <vault_root>"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.lifecycle import (
    cleanup_stale,
    pidfile_path_for,
    socket_path_for,
    write_pidfile,
)
from llm_wiki.daemon.server import DaemonServer
from llm_wiki.daemon.watcher import FileWatcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("llm-wiki-daemon")


async def run(vault_root: Path) -> None:
    sock_path = socket_path_for(vault_root)
    pid_path = pidfile_path_for(vault_root)

    cleanup_stale(sock_path, pid_path)

    config = WikiConfig.load(vault_root / "schema" / "config.yaml")
    server = DaemonServer(vault_root, sock_path, config=config)
    await server.start()
    write_pidfile(pid_path, os.getpid())

    async def on_file_change(changed, removed):
        logger.info("Files changed, rescanning vault...")
        await server.rescan()

    watcher = FileWatcher(vault_root, on_file_change, poll_interval=2.0)
    await watcher.start()

    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Received shutdown signal")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, handle_signal)

    logger.info("Daemon ready (PID %d, vault %s)", os.getpid(), vault_root)

    await stop_event.wait()

    await watcher.stop()
    await server.stop()
    cleanup_stale(sock_path, pid_path)
    logger.info("Daemon shut down cleanly")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m llm_wiki.daemon <vault_root>", file=sys.stderr)
        sys.exit(1)
    vault_root = Path(sys.argv[1]).resolve()
    if not vault_root.is_dir():
        print(f"Not a directory: {vault_root}", file=sys.stderr)
        sys.exit(1)
    asyncio.run(run(vault_root))


if __name__ == "__main__":
    main()
