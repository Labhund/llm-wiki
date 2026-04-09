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

        Sub-phase 5b registers the auditor.
        Sub-phase 5c adds librarian + authority_recalc.
        Sub-phase 5d will add the adversary.
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

        async def run_librarian() -> None:
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.librarian.agent import LibrarianAgent
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            result = await agent.run()
            logger.info(
                "Librarian: refined=%d authorities=%d issues=%d",
                len(result.pages_refined), result.authorities_updated, len(result.issues_filed),
            )

        async def run_authority_recalc() -> None:
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.librarian.agent import LibrarianAgent
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            count = await agent.recalc_authority()
            logger.info("Authority recalc: %d entries updated", count)

        async def run_adversary() -> None:
            from llm_wiki.adversary.agent import AdversaryAgent
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = AdversaryAgent(self._vault, self._vault_root, llm, queue, self._config)
            result = await agent.run()
            logger.info(
                "Adversary: checked=%d validated=%d failed=%d talk=%d issues=%d",
                result.claims_checked, len(result.validated), len(result.failed),
                len(result.talk_posts), len(result.issues_filed),
            )

        async def run_talk_summary() -> None:
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.librarian.agent import LibrarianAgent
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            llm = LLMClient(
                self._llm_queue,
                model=self._config.llm.default,
                api_base=self._config.llm.api_base,
                api_key=self._config.llm.api_key,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            count = await agent.refresh_talk_summaries()
            logger.info("Talk summary: refreshed=%d", count)

        self._scheduler.register(
            ScheduledWorker(
                name="auditor",
                interval_seconds=parse_interval(self._config.maintenance.auditor_interval),
                coro_factory=run_auditor,
            )
        )
        self._scheduler.register(
            ScheduledWorker(
                name="librarian",
                interval_seconds=parse_interval(self._config.maintenance.librarian_interval),
                coro_factory=run_librarian,
            )
        )
        self._scheduler.register(
            ScheduledWorker(
                name="authority_recalc",
                interval_seconds=parse_interval(self._config.maintenance.authority_recalc),
                coro_factory=run_authority_recalc,
            )
        )
        self._scheduler.register(
            ScheduledWorker(
                name="adversary",
                interval_seconds=parse_interval(self._config.maintenance.adversary_interval),
                coro_factory=run_adversary,
            )
        )
        self._scheduler.register(
            ScheduledWorker(
                name="talk_summary",
                interval_seconds=parse_interval(self._config.maintenance.librarian_interval),
                coro_factory=run_talk_summary,
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
            case "talk-read":
                return self._handle_talk_read(request)
            case "talk-append":
                return self._handle_talk_append(request)
            case "talk-list":
                return self._handle_talk_list()
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

        page_name = request["page_name"]
        return {
            "status": "ok",
            "content": content,
            "issues": self._read_issues_block(page_name),
            "talk": self._read_talk_block(page_name),
        }

    def _read_issues_block(self, page_name: str) -> dict:
        """Build the per-page issues digest folded into wiki_read responses."""
        queue = self._issue_queue()
        all_issues = queue.list(status="open")
        page_issues = [i for i in all_issues if i.page == page_name]

        by_severity: dict[str, int] = {"critical": 0, "moderate": 0, "minor": 0}
        for issue in page_issues:
            by_severity[issue.severity] = by_severity.get(issue.severity, 0) + 1

        items = [
            {
                "id": issue.id,
                "severity": issue.severity,
                "title": issue.title,
                "body": issue.body,
            }
            for issue in page_issues
        ]
        return {
            "open_count": len(page_issues),
            "by_severity": by_severity,
            "items": items,
        }

    def _read_talk_block(self, page_name: str) -> dict:
        """Build the per-page talk-page digest folded into wiki_read responses.

        Critical and moderate open entries are inlined verbatim under
        `recent_critical` / `recent_moderate`. Everything else collapses
        into counts + the librarian's stored 2-sentence summary.
        Resolved entries are excluded from counts and `recent_*`.
        """
        from llm_wiki.librarian.talk_summary import TalkSummaryStore
        from llm_wiki.talk.page import TalkPage, compute_open_set

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        # Find the page file (may be nested) so we can derive the talk path
        page_path = None
        for candidate in wiki_dir.rglob(f"{page_name}.md"):
            rel = candidate.relative_to(wiki_dir)
            if any(p.startswith(".") for p in rel.parts):
                continue
            page_path = candidate
            break

        empty = {
            "entry_count": 0,
            "open_count": 0,
            "by_severity": {
                "critical": 0, "moderate": 0, "minor": 0,
                "suggestion": 0, "new_connection": 0,
            },
            "summary": "",
            "recent_critical": [],
            "recent_moderate": [],
        }
        if page_path is None:
            return empty

        talk = TalkPage.for_page(page_path)
        if not talk.exists:
            return empty

        all_entries = talk.load()
        open_entries = compute_open_set(all_entries)

        by_severity = {
            "critical": 0, "moderate": 0, "minor": 0,
            "suggestion": 0, "new_connection": 0,
        }
        for e in open_entries:
            by_severity[e.severity] = by_severity.get(e.severity, 0) + 1

        recent_critical = [
            {"index": e.index, "ts": e.timestamp, "author": e.author, "body": e.body}
            for e in open_entries if e.severity == "critical"
        ]
        recent_moderate = [
            {"index": e.index, "ts": e.timestamp, "author": e.author, "body": e.body}
            for e in open_entries if e.severity == "moderate"
        ]

        # Pull the librarian's stored summary if present
        summary_store = TalkSummaryStore.load(
            _state_dir_for(self._vault_root) / "talk_summaries.json"
        )
        record = summary_store.get(page_name)
        summary_text = record.summary if record is not None else ""

        return {
            "entry_count": len(all_entries),
            "open_count": len(open_entries),
            "by_severity": by_severity,
            "summary": summary_text,
            "recent_critical": recent_critical,
            "recent_moderate": recent_moderate,
        }

    def _handle_manifest(self, request: dict) -> dict:
        text = self._vault.manifest_text(budget=request.get("budget", 16000))
        return {"status": "ok", "content": text}

    def _handle_status(self) -> dict:
        info = self._vault.status()
        return {"status": "ok", **info}

    def _handle_scheduler_status(self) -> dict:
        if self._scheduler is None:
            return {"status": "ok", "workers": []}
        workers = [
            {
                "name": name,
                "interval_seconds": interval_seconds,
                "last_run": last_run,
            }
            for name, interval_seconds, last_run in self._scheduler.workers_info()
        ]
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

    def _handle_talk_read(self, request: dict) -> dict:
        from llm_wiki.talk.page import TalkPage
        if "page" not in request:
            return {"status": "error", "message": "Missing required field: page"}
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        page_path = wiki_dir / f"{request['page']}.md"
        talk = TalkPage.for_page(page_path)
        entries = [
            {"timestamp": e.timestamp, "author": e.author, "body": e.body}
            for e in talk.load()
        ]
        return {"status": "ok", "entries": entries}

    def _handle_talk_append(self, request: dict) -> dict:
        import datetime as _dt
        from llm_wiki.talk.discovery import ensure_talk_marker
        from llm_wiki.talk.page import TalkEntry, TalkPage

        for field_name in ("page", "author", "body"):
            if field_name not in request:
                return {"status": "error", "message": f"Missing required field: {field_name}"}

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        page_path = wiki_dir / f"{request['page']}.md"
        if not page_path.exists():
            return {"status": "error", "message": f"Page not found: {request['page']}"}

        severity = request.get("severity", "suggestion")
        resolves_raw = request.get("resolves", [])
        # Defensive: coerce to list[int] in case JSON delivers strings.
        try:
            resolves = [int(x) for x in resolves_raw]
        except (TypeError, ValueError):
            return {"status": "error", "message": "resolves must be a list of integers"}

        talk = TalkPage.for_page(page_path)
        entry = TalkEntry(
            index=0,
            timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
            author=request["author"],
            body=request["body"],
            severity=severity,
            resolves=resolves,
        )
        talk.append(entry)
        ensure_talk_marker(page_path)
        return {"status": "ok"}

    def _handle_talk_list(self) -> dict:
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        pages: list[str] = []
        if wiki_dir.exists():
            for talk_file in sorted(wiki_dir.rglob("*.talk.md")):
                # Skip files inside hidden directories
                rel = talk_file.relative_to(wiki_dir)
                if any(p.startswith(".") for p in rel.parts):
                    continue
                stem = talk_file.stem  # foo.talk
                if stem.endswith(".talk"):
                    pages.append(stem[: -len(".talk")])
        return {"status": "ok", "pages": pages}

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
