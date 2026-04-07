from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.llm_queue import LLMQueue
from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.search.backend import SearchResult
from llm_wiki.vault import Vault, _state_dir_for

logger = logging.getLogger(__name__)


class DaemonServer:
    """Async Unix socket server wrapping a Vault instance."""

    def __init__(
        self,
        vault_root: Path,
        socket_path: Path,
        config: WikiConfig | None = None,
    ) -> None:
        self._vault_root = vault_root
        self._socket_path = socket_path
        self._config = config or WikiConfig()
        self._vault: Vault | None = None
        self._server: asyncio.Server | None = None
        self._llm_queue = LLMQueue(self._config.llm_queue.max_concurrent)

    async def start(self) -> None:
        """Scan vault and start listening on Unix socket."""
        self._vault = Vault.scan(self._vault_root)
        if self._socket_path.exists():
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )
        logger.info(
            "Daemon started: %d pages, socket %s",
            self._vault.page_count, self._socket_path,
        )

    async def serve_forever(self) -> None:
        """Block until the server is stopped."""
        if self._server:
            async with self._server:
                await self._server.serve_forever()

    async def stop(self) -> None:
        """Shut down the server and clean up socket."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if self._socket_path.exists():
            self._socket_path.unlink()
        logger.info("Daemon stopped")

    async def rescan(self) -> None:
        """Re-scan the vault (called by file watcher)."""
        self._vault = Vault.scan(self._vault_root)
        logger.info("Rescanned: %d pages", self._vault.page_count)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await read_message(reader)
            response = await self._route(request)
            await write_message(writer, response)
        except Exception as exc:
            try:
                await write_message(writer, {"status": "error", "message": str(exc)})
            except Exception:
                pass
            logger.exception("Error handling request")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _route(self, request: dict) -> dict:
        req_type = request.get("type", "")
        match req_type:
            case "search":
                return self._handle_search(request)
            case "read":
                return self._handle_read(request)
            case "manifest":
                return self._handle_manifest(request)
            case "status":
                return self._handle_status()
            case "rescan":
                await self.rescan()
                return {"status": "ok", "page_count": self._vault.page_count}
            case "query":
                return await self._handle_query(request)
            case "ingest":
                return await self._handle_ingest(request)
            case _:
                return {"status": "error", "message": f"Unknown request type: {req_type}"}

    def _handle_search(self, request: dict) -> dict:
        results = self._vault.search(
            request["query"], limit=request.get("limit", 10)
        )
        return {
            "status": "ok",
            "results": [_serialize_result(r) for r in results],
        }

    def _handle_read(self, request: dict) -> dict:
        content = self._vault.read_viewport(
            request["page_name"],
            viewport=request.get("viewport", "top"),
            section=request.get("section"),
            grep=request.get("grep"),
            budget=request.get("budget"),
        )
        if content is None:
            return {"status": "error", "message": f"Page not found: {request['page_name']}"}
        return {"status": "ok", "content": content}

    def _handle_manifest(self, request: dict) -> dict:
        text = self._vault.manifest_text(budget=request.get("budget", 16000))
        return {"status": "ok", "content": text}

    def _handle_status(self) -> dict:
        info = self._vault.status()
        return {"status": "ok", **info}

    async def _handle_query(self, request: dict) -> dict:
        if "question" not in request:
            return {"status": "error", "message": "Missing required field: question"}

        logger.debug("Loading traverse modules (first query incurs litellm import cost)")
        from llm_wiki.traverse.engine import TraversalEngine
        from llm_wiki.traverse.llm_client import LLMClient

        llm = LLMClient(
            self._llm_queue,
            model=self._config.llm.default,
            api_base=self._config.llm.api_base,
            api_key=self._config.llm.api_key,
        )
        log_dir = _state_dir_for(self._vault_root) / "traversal_logs"
        engine = TraversalEngine(
            self._vault, llm, self._config,
            vault_root=self._vault_root,
            log_dir=log_dir,
        )
        result = await engine.query(
            request["question"],
            budget=request.get("budget"),
        )
        return {
            "status": "ok",
            "answer": result.answer,
            "citations": result.citations,
            "outcome": result.outcome,
            "needs_more_budget": result.needs_more_budget,
            "log": result.log.to_dict(),
        }

    async def _handle_ingest(self, request: dict) -> dict:
        if "source_path" not in request:
            return {"status": "error", "message": "Missing required field: source_path"}

        from llm_wiki.ingest.agent import IngestAgent
        from llm_wiki.traverse.llm_client import LLMClient

        source_path = Path(request["source_path"])
        llm = LLMClient(
            self._llm_queue,
            model=self._config.llm.default,
            api_base=self._config.llm.api_base,
            api_key=self._config.llm.api_key,
        )
        agent = IngestAgent(llm, self._config)
        try:
            result = await agent.ingest(source_path, self._vault_root)
        finally:
            try:
                await self.rescan()
            except Exception:
                logger.warning("Failed to rescan vault after ingest")

        return {
            "status": "ok",
            "pages_created": result.pages_created,
            "pages_updated": result.pages_updated,
            "concepts_found": result.concepts_found,
        }


def _serialize_result(r: SearchResult) -> dict:
    return {
        "name": r.name,
        "score": r.score,
        "manifest": r.entry.to_manifest_text(),
    }
