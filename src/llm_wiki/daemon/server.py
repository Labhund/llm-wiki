from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from llm_wiki.config import WikiConfig
from llm_wiki.daemon.dispatcher import ChangeDispatcher
from llm_wiki.daemon.llm_queue import LLMQueue
from llm_wiki.daemon.protocol import read_message, write_message
from llm_wiki.daemon.scheduler import IntervalScheduler, ScheduledWorker, parse_interval
from llm_wiki.daemon.snapshot import PageSnapshotStore
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
        self._scheduler: IntervalScheduler | None = None
        self._snapshot_store: PageSnapshotStore | None = None
        self._compliance_reviewer = None  # type: ignore[assignment]  # set in start()
        self._dispatcher: ChangeDispatcher | None = None

    async def start(self) -> None:
        """Scan vault, construct maintenance substrate, start listening."""
        self._vault = Vault.scan(self._vault_root)

        # Phase 5b substrate
        from llm_wiki.audit.compliance import ComplianceReviewer
        from llm_wiki.issues.queue import IssueQueue
        state_dir = _state_dir_for(self._vault_root)
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        self._snapshot_store = PageSnapshotStore(state_dir)
        self._compliance_reviewer = ComplianceReviewer(
            self._vault_root, IssueQueue(wiki_dir), self._config
        )
        self._dispatcher = ChangeDispatcher(
            debounce_secs=float(self._config.maintenance.compliance_debounce_secs),
            on_settled=self._handle_settled_change,
        )

        self._scheduler = IntervalScheduler()
        self._register_maintenance_workers()
        await self._scheduler.start()

        if self._socket_path.exists():
            self._socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )
        logger.info(
            "Daemon started: %d pages, socket %s, workers=%s",
            self._vault.page_count, self._socket_path,
            self._scheduler.worker_names,
        )

    async def serve_forever(self) -> None:
        """Block until the server is stopped."""
        if self._server:
            async with self._server:
                await self._server.serve_forever()

    async def stop(self) -> None:
        """Shut down the maintenance substrate, then the server."""
        if self._scheduler is not None:
            await self._scheduler.stop()
            self._scheduler = None
        if self._dispatcher is not None:
            await self._dispatcher.stop()
            self._dispatcher = None
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

    def _register_maintenance_workers(self) -> None:
        """Register all maintenance workers with the scheduler.

        Sub-phase 5b registers only the auditor. Sub-phases 5c (librarian)
        and 5d (adversary) extend this method to register additional workers.
        """
        assert self._scheduler is not None

        async def run_auditor() -> None:
            from llm_wiki.audit.auditor import Auditor
            from llm_wiki.issues.queue import IssueQueue
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            auditor = Auditor(self._vault, queue, self._vault_root)
            report = auditor.audit()
            logger.info(
                "Auditor: %d new issues, %d existing",
                len(report.new_issue_ids), len(report.existing_issue_ids),
            )

        self._scheduler.register(
            ScheduledWorker(
                name="auditor",
                interval_seconds=parse_interval(self._config.maintenance.auditor_interval),
                coro_factory=run_auditor,
            )
        )

    async def handle_file_changes(
        self, changed: list[Path], removed: list[Path]
    ) -> None:
        """File-watcher callback. Replaces __main__.on_file_change.

        Rescans the vault, then queues each changed page for compliance review
        via the debouncer. Removed pages purge their snapshot.
        """
        await self.rescan()
        for path in changed:
            try:
                rel = path.relative_to(self._vault_root)
            except ValueError:
                continue
            if any(p.startswith(".") for p in rel.parts):
                continue  # skip hidden dirs (e.g. .issues)
            if self._dispatcher is not None:
                self._dispatcher.submit(path)
        for path in removed:
            if self._snapshot_store is not None:
                self._snapshot_store.remove(path.stem)

    async def _handle_settled_change(self, path: Path) -> None:
        """Called by ChangeDispatcher after a path has settled past the debounce window."""
        if self._compliance_reviewer is None or self._snapshot_store is None:
            return
        if not path.exists():
            self._snapshot_store.remove(path.stem)
            return
        try:
            new_content = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("Failed to read %s for compliance review", path)
            return
        old_content = self._snapshot_store.get(path.stem)
        result = self._compliance_reviewer.review_change(path, old_content, new_content)
        # Re-read in case the reviewer auto-fixed the file
        try:
            self._snapshot_store.set(path.stem, path.read_text(encoding="utf-8"))
        except OSError:
            logger.exception("Failed to update snapshot for %s", path)
        logger.info(
            "Compliance review %s: auto_approved=%s reasons=%s issues=%d",
            path.stem, result.auto_approved, result.reasons, len(result.issues_filed),
        )

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
            case "lint":
                return self._handle_lint()
            case "issues-list":
                return self._handle_issues_list(request)
            case "issues-get":
                return self._handle_issues_get(request)
            case "issues-update":
                return self._handle_issues_update(request)
            case "scheduler-status":
                return self._handle_scheduler_status()
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

    def _handle_scheduler_status(self) -> dict:
        if self._scheduler is None:
            return {"status": "ok", "workers": []}
        workers = []
        for worker in self._scheduler._workers:  # noqa: SLF001 — internal access is fine, same module family
            workers.append({
                "name": worker.name,
                "interval_seconds": worker.interval_seconds,
                "last_run": self._scheduler.last_run_iso(worker.name),
            })
        return {"status": "ok", "workers": workers}

    def _handle_lint(self) -> dict:
        from llm_wiki.audit.auditor import Auditor

        queue = self._issue_queue()
        auditor = Auditor(self._vault, queue, self._vault_root)
        report = auditor.audit()
        return {"status": "ok", **report.to_dict()}

    def _issue_queue(self) -> "IssueQueue":
        from llm_wiki.issues.queue import IssueQueue
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        return IssueQueue(wiki_dir)

    def _handle_issues_list(self, request: dict) -> dict:
        queue = self._issue_queue()
        issues = queue.list(
            status=request.get("status_filter"),
            type=request.get("type_filter"),
        )
        return {
            "status": "ok",
            "issues": [_serialize_issue(i) for i in issues],
        }

    def _handle_issues_get(self, request: dict) -> dict:
        if "id" not in request:
            return {"status": "error", "message": "Missing required field: id"}
        queue = self._issue_queue()
        try:
            issue = queue.get(request["id"])
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        if issue is None:
            return {"status": "error", "message": f"Issue not found: {request['id']}"}
        return {"status": "ok", "issue": _serialize_issue(issue, include_body=True)}

    def _handle_issues_update(self, request: dict) -> dict:
        if "id" not in request or "status" not in request:
            return {"status": "error", "message": "Missing required fields: id, status"}
        queue = self._issue_queue()
        try:
            ok = queue.update_status(request["id"], request["status"])
        except ValueError as exc:
            return {"status": "error", "message": str(exc)}
        if not ok:
            return {"status": "error", "message": f"Issue not found: {request['id']}"}
        return {"status": "ok"}

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


def _serialize_issue(issue: "Issue", include_body: bool = False) -> dict:
    data = {
        "id": issue.id,
        "type": issue.type,
        "status": issue.status,
        "title": issue.title,
        "page": issue.page,
        "created": issue.created,
        "detected_by": issue.detected_by,
        "metadata": issue.metadata,
    }
    if include_body:
        data["body"] = issue.body
    return data
