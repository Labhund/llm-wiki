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
        enabled_workers: set[str] | None = None,
    ) -> None:
        self._vault_root = vault_root
        self._socket_path = socket_path
        self._config = config or WikiConfig()
        # `enabled_workers=None` means "register all workers" (production
        # default). An explicit set narrows registration; an empty set
        # registers nothing — useful for tests that want a quiescent daemon
        # without racing the scheduler. Unknown names raise at start().
        self._enabled_workers = enabled_workers
        self._vault: Vault | None = None
        self._server: asyncio.Server | None = None
        self._llm_queue = LLMQueue(
            max_concurrent=self._config.llm_queue.max_concurrent,
            hourly_limit=self._config.llm_queue.cloud_hourly_limit,
            daily_limit=self._config.llm_queue.cloud_daily_limit,
        )
        self._scheduler: IntervalScheduler | None = None
        self._snapshot_store: PageSnapshotStore | None = None
        self._compliance_reviewer = None  # type: ignore[assignment]  # set in start()
        self._dispatcher: ChangeDispatcher | None = None
        # Phase 6b write surface — populated in start()
        self._commit_lock = asyncio.Lock()
        self._commit_service = None  # type: ignore[assignment]
        self._session_registry = None  # type: ignore[assignment]
        self._page_write_service = None  # type: ignore[assignment]
        self._write_coordinator = None  # type: ignore[assignment]
        self._inactivity_task: asyncio.Task | None = None
        self._title_to_slug: dict[str, str] = {}

    async def start(self) -> None:
        """Scan vault, construct maintenance substrate, start listening."""
        self._vault = Vault.scan(self._vault_root)
        self._title_to_slug = {
            e.title: e.name
            for e in self._vault.manifest_entries().values()
            if e.title
        }

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

        self._scheduler = IntervalScheduler(
            issue_queue=IssueQueue(wiki_dir),
            escalation_threshold=self._config.maintenance.failure_escalation_threshold,
        )
        self._register_maintenance_workers()
        await self._scheduler.start()

        # Phase 6b: write surface (commit pipeline + page writes)
        from llm_wiki.daemon.commit import CommitService
        from llm_wiki.daemon.sessions import SessionRegistry, recover_sessions
        from llm_wiki.daemon.writer import WriteCoordinator
        from llm_wiki.daemon.writes import PageWriteService
        from llm_wiki.traverse.llm_client import LLMClient

        self._write_coordinator = WriteCoordinator()
        self._session_registry = SessionRegistry(self._config.sessions)

        backend = self._config.llm.resolve("commit")
        commit_llm = LLMClient(
            self._llm_queue,
            model=backend.model,
            api_base=backend.api_base,
            api_key=backend.api_key,
        )
        self._commit_service = CommitService(
            vault_root=self._vault_root,
            llm=commit_llm,
            lock=self._commit_lock,
        )
        self._page_write_service = PageWriteService(
            vault=self._vault,
            vault_root=self._vault_root,
            config=self._config,
            write_coordinator=self._write_coordinator,
            registry=self._session_registry,
            commit_service=self._commit_service,
        )

        # Recovery: settle any orphaned journals from a prior crash
        await recover_sessions(state_dir=state_dir, commit_service=self._commit_service)

        # Inactivity timer: settle quiet sessions after `inactivity_timeout_seconds`
        self._inactivity_task = asyncio.create_task(self._inactivity_loop())

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

    async def _inactivity_loop(self) -> None:
        """Settle sessions whose last_write_at is older than the timeout.

        KNOWN RACE WINDOW (low-probability, documented for honesty):
        Between `load_journal(sess.journal_path)` reading the entries and
        `settle_with_fallback` archiving the file, a concurrent write to
        the same session could append a new entry to the journal. That
        new entry would be on disk in the to-be-archived file but never
        included in the commit. After settle, `_session_registry.close(sess)`
        removes the session, so the next write from the same author opens
        a fresh session — leaving the orphan entry as a permanent gap.

        This is extremely unlikely in practice: the inactivity loop only
        fires after `inactivity_timeout_seconds` of zero activity, so by
        definition the agent has been quiet. The window between
        `load_journal` and the journal-archive step inside settle is also
        narrow (single-digit milliseconds in normal conditions).

        If this ever bites in practice, the fix is to acquire the per-page
        write lock around the journal-read + settle + close sequence so a
        concurrent write blocks until settle completes. Not done now to
        keep this hot path simple.
        """
        import datetime as _dt
        from llm_wiki.daemon.sessions import Session, load_journal

        timeout = self._config.sessions.inactivity_timeout_seconds
        poll_interval = max(0.5, timeout / 2)
        try:
            while True:
                await asyncio.sleep(poll_interval)
                if self._session_registry is None or self._commit_service is None:
                    continue
                now = _dt.datetime.now(_dt.timezone.utc)
                stale: list[Session] = []
                for sess in list(self._session_registry.all_sessions()):
                    try:
                        last = _dt.datetime.fromisoformat(sess.last_write_at)
                    except ValueError:
                        continue
                    if (now - last).total_seconds() >= timeout:
                        stale.append(sess)
                for sess in stale:
                    try:
                        entries = load_journal(sess.journal_path)
                        if entries:
                            await self._commit_service.settle_with_fallback(sess, entries)
                    except Exception:
                        logger.exception(
                            "Inactivity settle failed for session %s", sess.id,
                        )
                    finally:
                        self._session_registry.close(sess)
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        """Shut down the maintenance substrate, then the server."""
        # Phase 6b: cancel the inactivity timer first so it doesn't race the settle
        if self._inactivity_task is not None:
            self._inactivity_task.cancel()
            try:
                await self._inactivity_task
            except asyncio.CancelledError:
                pass
            self._inactivity_task = None

        # Phase 6b: settle every open session before tearing down anything else
        if self._session_registry is not None and self._commit_service is not None:
            from llm_wiki.daemon.sessions import load_journal

            for sess in list(self._session_registry.all_sessions()):
                try:
                    entries = load_journal(sess.journal_path)
                    if entries:
                        await self._commit_service.settle_with_fallback(sess, entries)
                except Exception:
                    logger.exception(
                        "Failed to settle session %s on shutdown", sess.id,
                    )
                finally:
                    self._session_registry.close(sess)

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
        self._title_to_slug = {
            e.title: e.name
            for e in self._vault.manifest_entries().values()
            if e.title
        }
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
            from llm_wiki.audit.checks import execute_proposal_merges
            from llm_wiki.issues.queue import IssueQueue
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            auditor = Auditor(self._vault, queue, self._vault_root, self._config)
            report = auditor.audit()
            logger.info(
                "Auditor: %d new issues, %d existing",
                len(report.new_issue_ids), len(report.existing_issue_ids),
            )
            # Apply merge-ready proposals after audit — write phase only
            try:
                merged = execute_proposal_merges(
                    self._vault_root,
                    auto_merge_threshold=self._config.ingest.grounding_auto_merge,
                )
                if merged:
                    logger.info("Auto-merged %d proposal(s): %s", len(merged), merged)
            except Exception:
                logger.exception("execute_proposal_merges failed — audit result unaffected")

        _maint_timeout = float(self._config.maintenance.maintenance_llm_timeout)

        async def run_librarian() -> None:
            from llm_wiki.issues.queue import IssueQueue
            from llm_wiki.librarian.agent import LibrarianAgent
            from llm_wiki.traverse.llm_client import LLMClient
            wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
            queue = IssueQueue(wiki_dir)
            backend = self._config.llm.resolve("librarian")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
                timeout=_maint_timeout,
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
            backend = self._config.llm.resolve("librarian")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
                timeout=_maint_timeout,
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
            backend = self._config.llm.resolve("adversary")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
                timeout=_maint_timeout,
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
            backend = self._config.llm.resolve("talk_summary")
            llm = LLMClient(
                self._llm_queue,
                model=backend.model,
                api_base=backend.api_base,
                api_key=backend.api_key,
                timeout=_maint_timeout,
            )
            agent = LibrarianAgent(self._vault, self._vault_root, llm, queue, self._config)
            count = await agent.refresh_talk_summaries()
            logger.info("Talk summary: refreshed=%d", count)

        def _get_probe_url(role: str) -> str | None:
            try:
                backend = self._config.llm.resolve(role)
                return backend.api_base
            except Exception:
                return None

        all_workers = [
            ScheduledWorker(
                name="auditor",
                interval_seconds=parse_interval(self._config.maintenance.auditor_interval),
                coro_factory=run_auditor,
                health_probe_url=None,
            ),
            ScheduledWorker(
                name="librarian",
                interval_seconds=parse_interval(self._config.maintenance.librarian_interval),
                coro_factory=run_librarian,
                health_probe_url=_get_probe_url("librarian"),
            ),
            ScheduledWorker(
                name="authority_recalc",
                interval_seconds=parse_interval(self._config.maintenance.authority_recalc),
                coro_factory=run_authority_recalc,
                health_probe_url=None,
            ),
            ScheduledWorker(
                name="adversary",
                interval_seconds=parse_interval(self._config.maintenance.adversary_interval),
                coro_factory=run_adversary,
                health_probe_url=_get_probe_url("adversary"),
            ),
            ScheduledWorker(
                name="talk_summary",
                interval_seconds=parse_interval(self._config.maintenance.librarian_interval),
                coro_factory=run_talk_summary,
                health_probe_url=_get_probe_url("talk_summary"),
            ),
        ]
        # `enabled_workers=None` → register all. Otherwise filter, and
        # validate up front so a typo in the test fixture (or an obsolete
        # name in a config) fails loudly instead of silently dropping work.
        if self._enabled_workers is not None:
            known = {w.name for w in all_workers}
            unknown = self._enabled_workers - known
            if unknown:
                raise ValueError(
                    f"Unknown worker name(s) in enabled_workers: {sorted(unknown)}. "
                    f"Known workers: {sorted(known)}."
                )
        for worker in all_workers:
            if (
                self._enabled_workers is None
                or worker.name in self._enabled_workers
            ):
                self._scheduler.register(worker)

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
        await self._run_wikilink_audit(path)

    async def _run_wikilink_audit(self, path: Path) -> None:
        """Add [[wikilinks]] to unlinked title occurrences in `path`.

        Only runs on pages inside wiki_dir. Skips pages with an active write
        lock. Commits directly via CommitService (no session, no LLM call).
        """
        from llm_wiki.audit.wikilink_audit import apply_wikilinks, build_link_pattern

        # Only audit pages inside wiki/
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        try:
            path.relative_to(wiki_dir)
        except ValueError:
            return

        if not path.exists():
            return

        # Conflict guard: skip if a write is in progress for this page
        if self._write_coordinator is not None:
            if self._write_coordinator.lock_for(path.stem).locked():
                logger.debug(
                    "Wikilink audit: skipping %s — write in progress", path.stem
                )
                return

        if not self._title_to_slug:
            return

        pattern = build_link_pattern(self._title_to_slug)
        if pattern is None:
            return

        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("Wikilink audit: failed to read %s", path)
            return

        new_text, count = apply_wikilinks(text, self._title_to_slug, path.stem, pattern)

        # Three guards before write
        if count == 0:
            return
        if len(new_text) < len(text):
            logger.warning(
                "Wikilink audit: aborting — new_text shorter than original for %s",
                path.stem,
            )
            return
        if new_text.count("[[") < text.count("[["):
            logger.warning(
                "Wikilink audit: aborting — wikilink count shrank for %s", path.stem
            )
            return

        try:
            path.write_text(new_text, encoding="utf-8")
        except OSError:
            logger.exception("Wikilink audit: failed to write %s", path)
            return

        if self._commit_service is not None:
            rel_path = str(path.relative_to(self._vault_root))
            msg = f"audit: add {count} wikilink(s) to {path.stem}"
            try:
                sha = await self._commit_service.commit_direct([rel_path], msg)
                if sha:
                    logger.info(
                        "Wikilink audit: %s — %d link(s) → %s",
                        path.stem, count, sha[:8],
                    )
            except Exception:
                logger.exception(
                    "Wikilink audit: commit failed for %s", path.stem
                )

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            request = await read_message(reader)
            if request.get("type") == "ingest" and request.get("stream"):
                await self._handle_ingest_stream(request, writer)
                return
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
            case "read-many":
                return self._handle_read_many(request)
            case "read-cluster":
                return self._handle_read_cluster(request)
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
            case "process-list":
                return self._handle_process_list()
            case "talk-read":
                return self._handle_talk_read(request)
            case "talk-append":
                return self._handle_talk_append(request)
            case "talk-list":
                return self._handle_talk_list()
            case "page-create":
                return await self._handle_page_create(request)
            case "page-update":
                return await self._handle_page_update(request)
            case "page-append":
                return await self._handle_page_append(request)
            case "session-close":
                return await self._handle_session_close(request)
            case "source-mark":
                return await self._handle_source_mark(request)
            case "inbox-create":
                return await self._handle_inbox_create(request)
            case "inbox-get":
                return await self._handle_inbox_get(request)
            case "inbox-write":
                return await self._handle_inbox_write(request)
            case "inbox-list":
                return await self._handle_inbox_list(request)
            case "proposals-list":
                return self._handle_proposals_list()
            case "proposals-approve":
                return self._handle_proposals_approve(request)
            case "proposals-reject":
                return self._handle_proposals_reject(request)
            case _:
                return {"status": "error", "message": f"Unknown request type: {req_type}"}

    async def _handle_session_close(self, request: dict) -> dict:
        for f in ("author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        if self._session_registry is None or self._commit_service is None:
            return {"status": "error", "message": "Session machinery not initialized"}

        from llm_wiki.daemon.sessions import load_journal

        sess = self._session_registry.get_active(
            request["author"], request["connection_id"],
        )
        if sess is None:
            # Idempotent: closing a session that was never opened (or already
            # settled) is not an error. The caller may be the inactivity timer
            # racing with an explicit close, or a swarm orchestrator closing
            # eagerly.
            return {"status": "ok", "settled": False}

        entries = load_journal(sess.journal_path)
        result = await self._commit_service.settle_with_fallback(sess, entries)
        self._session_registry.close(sess)
        return {
            "status": "ok",
            "settled": True,
            "commit_sha": result.commit_sha,
        }

    async def _handle_source_mark(self, request: dict) -> dict:
        source_path_str = request.get("source_path")
        status = request.get("status")
        author = request.get("author", "cli")

        if not source_path_str:
            return {"status": "error", "message": "Missing required field: source_path"}
        if status not in ("unread", "in_progress", "read"):
            return {"status": "error", "message": "status must be unread|in_progress|read"}

        path = Path(source_path_str)
        raw_dir = self._vault_root / "raw"
        try:
            path.relative_to(raw_dir)
        except ValueError:
            return {"status": "error", "message": "source_path must be under raw/"}

        if not path.exists():
            return {"status": "error", "message": f"Source not found: {source_path_str}"}

        from llm_wiki.ingest.source_meta import read_frontmatter, write_frontmatter

        old_status = read_frontmatter(path).get("reading_status", "unknown")
        try:
            write_frontmatter(path, {"reading_status": status})
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        # Direct git commit — outside the session/journal pipeline
        rel_path = str(path.relative_to(self._vault_root))
        commit_message = (
            f"meta: mark {path.name} {status}\n\n"
            f"Source-Status: {old_status}\u2192{status}\n"
            f"Author: {author}"
        )
        async with self._commit_lock:
            import subprocess
            subprocess.run(
                ["git", "add", rel_path],
                cwd=self._vault_root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "commit", "-m", commit_message],
                cwd=self._vault_root,
                check=True,
                capture_output=True,
            )

        return {
            "status": "ok",
            "path": source_path_str,
            "old_status": old_status,
            "new_status": status,
        }

    async def _handle_inbox_create(self, request: dict) -> dict:
        source_path_str = request.get("source_path", "")
        title = request.get("title", "")
        claims = request.get("claims", [])
        author = request.get("author", "cli")

        if not title:
            return {"status": "error", "message": "Missing required field: title"}
        if not source_path_str:
            return {"status": "error", "message": "Missing required field: source_path"}

        # Normalize to relative raw/<filename>
        source_path = Path(source_path_str)
        if source_path.is_absolute():
            try:
                source_path_str = str(source_path.relative_to(self._vault_root))
            except ValueError:
                return {"status": "error", "message": "source_path must be under the vault root"}
        if not source_path_str.startswith("raw/"):
            return {"status": "error", "message": "source_path must be under raw/"}

        from llm_wiki.ingest.plan import create_plan_file
        try:
            plan_path = create_plan_file(
                self._vault_root, source_path_str, title, claims,
                inbox_dir=self._config.vault.inbox_dir.rstrip("/"),
            )
        except FileExistsError as e:
            return {"status": "error", "message": str(e)}

        import subprocess as _sp  # noqa: PLC0415

        rel_path = str(plan_path.relative_to(self._vault_root))
        commit_msg = (
            f"plan: create inbox plan for {Path(source_path_str).name}\n\n"
            f"Agent: {author}"
        )
        # TODO(async): subprocess.run blocks the event loop; migrate to
        # asyncio.create_subprocess_exec when CommitService is also migrated.
        async with self._commit_lock:
            _sp.run(["git", "add", rel_path], cwd=self._vault_root, check=True, capture_output=True)
            _sp.run(["git", "commit", "-m", commit_msg], cwd=self._vault_root, check=True, capture_output=True)

        return {
            "status": "ok",
            "plan_path": rel_path,
            "source": source_path_str,
        }

    async def _handle_inbox_get(self, request: dict) -> dict:
        plan_path_str = request.get("plan_path", "")
        if not plan_path_str:
            return {"status": "error", "message": "Missing required field: plan_path"}

        inbox_dir = self._vault_root / self._config.vault.inbox_dir.rstrip("/")
        plan_path = (self._vault_root / plan_path_str).resolve()
        try:
            plan_path.relative_to(inbox_dir.resolve())
        except ValueError:
            return {"status": "error", "message": "plan_path must be under inbox/"}

        if not plan_path.exists():
            return {"status": "error", "message": f"Plan file not found: {plan_path_str}"}

        content = plan_path.read_text(encoding="utf-8")
        from llm_wiki.ingest.plan import read_plan_frontmatter
        fm = read_plan_frontmatter(plan_path)
        return {"status": "ok", "content": content, "frontmatter": fm}

    async def _handle_inbox_write(self, request: dict) -> dict:
        plan_path_str = request.get("plan_path", "")
        content = request.get("content", "")
        author = request.get("author", "cli")

        if not plan_path_str:
            return {"status": "error", "message": "Missing required field: plan_path"}
        if not content:
            return {"status": "error", "message": "content must not be empty"}

        inbox_dir = self._vault_root / self._config.vault.inbox_dir.rstrip("/")
        plan_path = (self._vault_root / plan_path_str).resolve()
        try:
            plan_path.relative_to(inbox_dir.resolve())
        except ValueError:
            return {"status": "error", "message": "plan_path must be under inbox/"}

        import subprocess as _sp  # noqa: PLC0415

        if not plan_path.exists():
            return {"status": "error", "message": f"Plan file not found: {plan_path_str}"}
        # Caller is responsible for not having uncommitted local edits to this
        # file — no dirty-check is performed. The attended workflow assumes the
        # agent is the sole writer between wiki_inbox_get and wiki_inbox_write.
        plan_path.write_text(content, encoding="utf-8")

        rel_path = str(plan_path.relative_to(self._vault_root))
        commit_msg = (
            f"plan: checkpoint {plan_path.name}\n\n"
            f"Agent: {author}"
        )
        # TODO(async): subprocess.run blocks the event loop; migrate to
        # asyncio.create_subprocess_exec when CommitService is also migrated.
        async with self._commit_lock:
            _sp.run(["git", "add", rel_path], cwd=self._vault_root, check=True, capture_output=True)
            _sp.run(["git", "commit", "-m", commit_msg], cwd=self._vault_root, check=True, capture_output=True)

        return {"status": "ok", "plan_path": rel_path}

    async def _handle_inbox_list(self, request: dict) -> dict:
        inbox_dir = self._vault_root / self._config.vault.inbox_dir.rstrip("/")
        if not inbox_dir.is_dir():
            return {"status": "ok", "plans": []}

        from llm_wiki.ingest.plan import read_plan_frontmatter, count_unchecked_claims
        plans = []
        for f in sorted(inbox_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in (".md", ".markdown"):
                continue
            fm = read_plan_frontmatter(f)
            content = f.read_text(encoding="utf-8")
            plans.append({
                "path": str(f.relative_to(self._vault_root)),
                "source": fm.get("source", ""),
                "started": fm.get("started", ""),
                "status": fm.get("status", ""),
                "sessions": fm.get("sessions", 0),
                "unchecked_claims": count_unchecked_claims(content),
            })
        return {"status": "ok", "plans": plans}

    def _handle_proposals_list(self) -> dict:
        from llm_wiki.ingest.proposals import list_pending_proposals, read_proposal_meta
        proposals_dir = self._vault_root / "inbox" / "proposals"
        items = []
        for p in list_pending_proposals(proposals_dir):
            meta = read_proposal_meta(p)
            items.append({
                "path": str(p.relative_to(self._vault_root)),
                "target_page": meta.get("target_page", ""),
                "action": meta.get("action", ""),
                "status": meta.get("status", ""),
                "source": meta.get("source", ""),
            })
        return {"status": "ok", "proposals": items}

    def _handle_proposals_approve(self, request: dict) -> dict:
        if "path" not in request:
            return {"status": "error", "message": "Missing required field: path"}
        from llm_wiki.ingest.proposals import (
            read_proposal_meta, read_proposal_body, update_proposal_status, find_wiki_page,
        )
        from llm_wiki.ingest.page_writer import patch_token_estimates
        import yaml as _yaml
        p = self._vault_root / request["path"]
        if not p.exists():
            return {"status": "error", "message": f"Proposal not found: {request['path']}"}
        meta = read_proposal_meta(p)
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        target_page = meta["target_page"]
        target_cluster = meta.get("target_cluster") or ""
        body = read_proposal_body(p)
        target = find_wiki_page(wiki_dir, target_page)
        if target is not None and body:
            existing = target.read_text(encoding="utf-8")
            if body not in existing:
                target.write_text(existing.rstrip() + "\n\n" + body + "\n", encoding="utf-8")
                patch_token_estimates(target)
        elif target is None and meta.get("action") == "create":
            cluster_dir = wiki_dir / target_cluster if target_cluster else wiki_dir
            cluster_dir.mkdir(parents=True, exist_ok=True)
            new_path = cluster_dir / f"{target_page}.md"
            import datetime as _dt
            _today = _dt.date.today().isoformat()
            fm = {
                "title": meta["target_page"],
                "created": _today,
                "updated": _today,
                "type": "concept",
                "status": "stub",
                "source": f"[[{meta['source']}]]",
                "created_by": "ingest",
            }
            fm_text = "---\n" + _yaml.dump(fm, default_flow_style=False, sort_keys=False).strip() + "\n---\n\n"
            new_path.write_text(fm_text + body + "\n", encoding="utf-8")
            patch_token_estimates(new_path)
        update_proposal_status(p, "merged")
        return {"status": "ok", "merged": str(request["path"])}

    def _handle_proposals_reject(self, request: dict) -> dict:
        if "path" not in request:
            return {"status": "error", "message": "Missing required field: path"}
        from llm_wiki.ingest.proposals import update_proposal_status
        p = self._vault_root / request["path"]
        if not p.exists():
            return {"status": "error", "message": f"Proposal not found: {request['path']}"}
        update_proposal_status(p, "rejected")
        return {"status": "ok", "rejected": str(request["path"])}

    async def _handle_page_create(self, request: dict) -> dict:
        if self._page_write_service is None:
            return {"status": "error", "message": "Page write service not initialized"}
        for f in ("title", "body", "citations", "author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        result = await self._page_write_service.create(
            title=request["title"],
            body=request["body"],
            citations=list(request["citations"]),
            tags=list(request.get("tags", [])),
            author=request["author"],
            connection_id=request["connection_id"],
            intent=request.get("intent"),
            force=bool(request.get("force", False)),
        )
        return _serialize_write_result(result)

    async def _handle_page_update(self, request: dict) -> dict:
        if self._page_write_service is None:
            return {"status": "error", "message": "Page write service not initialized"}
        for f in ("page", "patch", "author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        result = await self._page_write_service.update(
            page=request["page"],
            patch=request["patch"],
            author=request["author"],
            connection_id=request["connection_id"],
            intent=request.get("intent"),
        )
        return _serialize_write_result(result)

    async def _handle_page_append(self, request: dict) -> dict:
        if self._page_write_service is None:
            return {"status": "error", "message": "Page write service not initialized"}
        for f in ("page", "section_heading", "body", "citations", "author", "connection_id"):
            if f not in request:
                return {"status": "error", "message": f"Missing required field: {f}"}
        result = await self._page_write_service.append(
            page=request["page"],
            section_heading=request["section_heading"],
            body=request["body"],
            citations=list(request["citations"]),
            author=request["author"],
            connection_id=request["connection_id"],
            after_heading=request.get("after_heading"),
            intent=request.get("intent"),
        )
        return _serialize_write_result(result)

    def _handle_search(self, request: dict) -> dict:
        results = self._vault.search_with_snippets(
            request["query"],
            limit=request.get("limit", 10),
        )
        return {
            "status": "ok",
            "results": [_serialize_snippet_result(r) for r in results],
        }

    def _handle_read(self, request: dict) -> dict:
        page_name = request["page_name"]
        viewport = request.get("viewport", "top")

        if viewport == "sections":
            sections = request.get("sections") or []
            result = self._vault.read_multi_sections(
                page_name, sections, request.get("budget")
            )
            if result is None:
                return {"status": "error", "message": f"Page not found: {page_name}"}
            return {
                "status": "ok",
                "content": result["content"],
                "missing_sections": result["missing_sections"],
                "issues": self._read_issues_block(page_name),
                "talk": self._read_talk_block(page_name),
            }

        content = self._vault.read_viewport(
            page_name,
            viewport=viewport,
            section=request.get("section"),
            grep=request.get("grep"),
            budget=request.get("budget"),
        )
        if content is None:
            return {"status": "error", "message": f"Page not found: {page_name}"}
        return {
            "status": "ok",
            "content": content,
            "issues": self._read_issues_block(page_name),
            "talk": self._read_talk_block(page_name),
        }

    def _handle_read_many(self, request: dict) -> dict:
        specs = request.get("pages", [])
        results = []
        for spec in specs:
            name = spec.get("name")
            if not name:
                results.append({"name": None, "status": "error", "message": "Missing page name"})
                continue
            r = self._handle_read({
                "type": "read",
                "page_name": name,
                "viewport": spec.get("viewport", "top"),
                "section": spec.get("section"),
                "sections": spec.get("sections"),
                "grep": spec.get("grep"),
                "budget": spec.get("budget"),
            })
            r["name"] = name
            results.append(r)
        return {"status": "ok", "pages": results}

    def _handle_read_cluster(self, request: dict) -> dict:
        cluster_name = request.get("cluster")
        if not cluster_name:
            return {"status": "error", "message": "Missing required field: cluster"}
        page_names = self._vault.pages_in_cluster(cluster_name)
        if not page_names:
            return {"status": "error", "message": f"No cluster found: {cluster_name}"}
        viewport = request.get("viewport", "top")
        result = self._handle_read_many({
            "pages": [{"name": n, "viewport": viewport} for n in page_names]
        })
        # Sum token counts from content lengths (rough, avoids import overhead)
        cluster_tokens = sum(
            len(r.get("content", "")) // 4
            for r in result["pages"]
            if r.get("status") == "ok"
        )
        result["cluster"] = cluster_name
        result["cluster_tokens"] = cluster_tokens
        return result

    def _read_issues_block(self, page_name: str) -> dict:
        """Build the per-page issues digest folded into wiki_read responses."""
        queue = self._issue_queue()
        all_issues = queue.list(status="open")
        page_issues = [i for i in all_issues if i.page == page_name]

        by_severity = _empty_severity_counts(_ISSUE_SEVERITIES)
        for issue in page_issues:
            sev = _clamp_severity(issue.severity, _ISSUE_SEVERITIES, "minor")
            by_severity[sev] += 1

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
            "n": len(page_issues),
            "sev": by_severity,
            "items": items,
        }

    def _read_talk_block(self, page_name: str) -> dict:
        """Build the per-page talk-page digest folded into wiki_read responses.

        Critical and moderate open entries are inlined verbatim under
        `crit` / `mod`. Everything else collapses into counts + the
        librarian's stored 2-sentence summary. Resolved entries are
        excluded from counts and `crit`/`mod`.
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
            "cnt": 0,
            "open": 0,
            "sev": _empty_severity_counts(_TALK_SEVERITIES),
            "summary": "",
            "crit": [],
            "mod": [],
        }
        if page_path is None:
            return empty

        talk = TalkPage.for_page(page_path)
        if not talk.exists:
            return empty

        all_entries = talk.load()
        open_entries = compute_open_set(all_entries)

        by_severity = _empty_severity_counts(_TALK_SEVERITIES)
        for e in open_entries:
            sev = _clamp_severity(e.severity, _TALK_SEVERITIES, "suggestion")
            by_severity[sev] += 1

        crit = [
            {"index": e.index, "ts": e.timestamp, "author": e.author, "body": e.body}
            for e in open_entries if e.severity == "critical"
        ]
        mod = [
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
            "cnt": len(all_entries),
            "open": len(open_entries),
            "sev": by_severity,
            "summary": summary_text,
            "crit": crit,
            "mod": mod,
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
        health = self._scheduler.health_info()
        workers = [
            {
                "name": name,
                "interval_seconds": interval_seconds,
                "last_run": last_run,
                "last_attempt": health.get(name, {}).get("last_attempt"),
                "consecutive_failures": health.get(name, {}).get("consecutive_failures", 0),
                "backend_reachable": health.get(name, {}).get("backend_reachable"),
            }
            for name, interval_seconds, last_run in self._scheduler.workers_info()
        ]
        return {"status": "ok", "workers": workers}

    def _handle_process_list(self) -> dict:
        jobs = []
        pending = 0
        slots_total = 0
        tokens_used = 0

        if self._llm_queue is not None:
            for job in self._llm_queue.active_jobs:
                jobs.append({
                    "id": job.id,
                    "label": job.label,
                    "priority": job.priority,
                    "elapsed_s": round(job.elapsed_s, 1),
                })
            pending = self._llm_queue.pending_count
            slots_total = self._llm_queue.slots_total
            tokens_used = self._llm_queue.tokens_used

        workers = []
        if self._scheduler is not None:
            running = self._scheduler.running_workers
            health = self._scheduler.health_info()
            for name, _interval_s, last_run in self._scheduler.workers_info():
                workers.append({
                    "name": name,
                    "state": "running" if name in running else "idle",
                    "last_run": last_run,
                    "consecutive_failures": health.get(name, {}).get(
                        "consecutive_failures", 0
                    ),
                    "running_elapsed_s": self._scheduler.running_elapsed_s(name),
                })

        return {
            "status": "ok",
            "jobs": jobs,
            "pending": pending,
            "slots_total": slots_total,
            "tokens_used": tokens_used,
            "workers": workers,
        }

    def _handle_lint(self) -> dict:
        from llm_wiki.audit.auditor import Auditor

        queue = self._issue_queue()
        auditor = Auditor(self._vault, queue, self._vault_root)
        report = auditor.audit()
        attention_map = self._build_attention_map(queue)
        return {
            "status": "ok",
            "attention_map": attention_map,
            **report.to_dict(),
        }

    def _build_attention_map(self, queue: "IssueQueue") -> dict:
        """Aggregate issue and talk severities across the vault.

        Issue counts come from the queue (already filtered by status='open').
        Talk counts come from walking every *.talk.md and computing the
        open set per page. Resolved entries are excluded.
        """
        from llm_wiki.talk.page import compute_open_set, iter_talk_pages

        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")

        totals_issues = _empty_severity_counts(_ISSUE_SEVERITIES)
        totals_talk = _empty_severity_counts(_TALK_SEVERITIES)
        by_page: dict[str, dict] = {}

        # Issues
        for issue in queue.list(status="open"):
            sev = _clamp_severity(issue.severity, _ISSUE_SEVERITIES, "minor")
            totals_issues[sev] += 1
            page = issue.page or "<vault>"
            page_entry = by_page.setdefault(
                page,
                {
                    "issues": _empty_severity_counts(_ISSUE_SEVERITIES),
                    "talk": _empty_severity_counts(_TALK_SEVERITIES),
                },
            )
            page_entry["issues"][sev] += 1

        # Talk pages — same shape, swap vocabularies
        for page_name, talk in iter_talk_pages(wiki_dir):
            entries = talk.load()
            open_entries = compute_open_set(entries)
            for e in open_entries:
                sev = _clamp_severity(e.severity, _TALK_SEVERITIES, "suggestion")
                totals_talk[sev] += 1
                page_entry = by_page.setdefault(
                    page_name,
                    {
                        "issues": _empty_severity_counts(_ISSUE_SEVERITIES),
                        "talk": _empty_severity_counts(_TALK_SEVERITIES),
                    },
                )
                page_entry["talk"][sev] += 1

        return {
            "pages_needing_attention": sorted(by_page.keys()),
            "totals": {"issues": totals_issues, "talk": totals_talk},
            "by_page": by_page,
        }

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
        from llm_wiki.talk.page import iter_talk_pages
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        pages = [name for name, _ in iter_talk_pages(wiki_dir)]
        return {"status": "ok", "pages": pages}

    async def _handle_query(self, request: dict) -> dict:
        if "question" not in request:
            return {"status": "error", "message": "Missing required field: question"}

        logger.debug("Loading traverse modules (first query incurs litellm import cost)")
        from llm_wiki.traverse.engine import TraversalEngine
        from llm_wiki.traverse.llm_client import LLMClient

        # Trace mode: collect all LLM call events and return with the response.
        trace_events: list[dict] = []
        trace_fn = None
        if request.get("trace"):
            async def trace_fn(event: dict) -> None:  # noqa: E731
                trace_events.append(event)

        backend = self._config.llm.resolve("query")
        llm = LLMClient(
            self._llm_queue,
            model=backend.model,
            api_base=backend.api_base,
            api_key=backend.api_key,
            trace_fn=trace_fn,
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
        resp: dict = {
            "status": "ok",
            "answer": result.answer,
            "citations": result.citations,
            "outcome": result.outcome,
            "needs_more_budget": result.needs_more_budget,
            "log": result.log.to_dict(),
        }
        if trace_events:
            resp["trace_events"] = trace_events

        # Synthesis cache: write, update, or accept existing page.
        try:
            await self._dispatch_synthesis_action(request["question"], result, resp)
        except Exception:
            logger.warning("Synthesis write failed — returning answer without caching", exc_info=True)

        return resp

    async def _write_synthesis_page(
        self,
        *,
        query: str,
        title: str,
        answer: str,
        sources: list[str],
    ) -> None:
        """Write a new synthesis page to wiki/. Rescans vault on success."""
        from llm_wiki.traverse.synthesis import build_synthesis_page_content, slug_from_query
        slug = slug_from_query(title or query)
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        wiki_dir.mkdir(parents=True, exist_ok=True)
        candidate = wiki_dir / f"{slug}.md"
        suffix = 2
        while candidate.exists():
            candidate = wiki_dir / f"{slug}-{suffix}.md"
            suffix += 1
        content = build_synthesis_page_content(title, query, answer, sources)
        candidate.write_text(content, encoding="utf-8")
        logger.info("Wrote synthesis page: %s", candidate.name)
        try:
            await self.rescan()
        except Exception:
            logger.warning("Rescan failed after synthesis write", exc_info=True)

    async def _update_synthesis_page(
        self,
        *,
        slug: str,
        query: str,
        title: str,
        answer: str,
        sources: list[str],
        created_at: str | None = None,
    ) -> None:
        """Overwrite an existing synthesis page with updated content."""
        from llm_wiki.traverse.synthesis import build_synthesis_page_content
        wiki_dir = self._vault_root / self._config.vault.wiki_dir.rstrip("/")
        page_path = wiki_dir / f"{slug}.md"
        if not page_path.exists():
            await self._write_synthesis_page(
                query=query, title=title, answer=answer, sources=sources
            )
            return
        content = build_synthesis_page_content(
            title, query, answer, sources, created_at=created_at
        )
        page_path.write_text(content, encoding="utf-8")
        logger.info("Updated synthesis page: %s", page_path.name)
        try:
            await self.rescan()
        except Exception:
            logger.warning("Rescan failed after synthesis update", exc_info=True)

    async def _dispatch_synthesis_action(
        self, question: str, result: "TraversalResult", resp: dict
    ) -> None:
        """Handle synthesis cache write/update/accept from TraversalResult.synthesis_action.

        Mutates resp["answer"] for the accept case (returns existing page content).
        No-op if no action or no citations in the answer.
        """
        action = result.synthesis_action
        if not action:
            return
        act = action.get("action")

        if act == "create":
            if not result.citations:
                return  # No wiki backing — don't cache
            await self._write_synthesis_page(
                query=question,
                title=action.get("title", question),
                answer=result.answer,
                sources=action.get("sources", [f"wiki/{c}.md" for c in result.citations]),
            )

        elif act == "update":
            slug = action.get("page", "")
            if not slug:
                return
            page = self._vault.read_page(slug)
            created_at = page.frontmatter.get("created_at") if page else None
            await self._update_synthesis_page(
                slug=slug,
                query=question,
                title=action.get("title", question),
                answer=result.answer,
                sources=action.get("sources", [f"wiki/{c}.md" for c in result.citations]),
                created_at=created_at,
            )

        elif act == "accept":
            slug = action.get("page", "")
            if not slug:
                return
            page = self._vault.read_page(slug)
            if page is None:
                return  # Page deleted since search — answer remains as-is
            answer_sections = [s for s in page.sections if s.name == "answer"]
            resp["answer"] = answer_sections[0].content if answer_sections else page.raw_content
            resp["synthesis_cache_hit"] = slug

    async def _handle_ingest(self, request: dict) -> dict:
        if request.get("proposal_mode"):
            return await self._handle_ingest_proposals(request)
        if "source_path" not in request:
            return {"status": "error", "message": "Missing required field: source_path"}
        if "connection_id" not in request:
            return {"status": "error", "message": "Missing required field: connection_id"}

        from llm_wiki.ingest.agent import IngestAgent
        from llm_wiki.traverse.llm_client import LLMClient

        author = request.get("author", "cli")
        if author == "cli":
            logger.info("ingest route called without author; defaulting to 'cli'")

        connection_id = request["connection_id"]
        source_path = Path(request["source_path"])
        dry_run = request.get("dry_run", False)
        source_type = request.get("source_type", "paper")
        backend = self._config.llm.resolve("ingest")
        llm = LLMClient(
            self._llm_queue,
            model=backend.model,
            api_base=backend.api_base,
            api_key=backend.api_key,
        )
        agent = IngestAgent(llm, self._config)
        try:
            result = await agent.ingest(
                source_path, self._vault_root,
                author=author,
                connection_id=connection_id,
                write_service=self._page_write_service,
                dry_run=dry_run,
                source_type=source_type,
            )
        finally:
            if not dry_run:
                try:
                    await self.rescan()
                except Exception:
                    logger.warning("Failed to rescan vault after ingest")

        # Dry-run response
        if dry_run:
            concepts = []
            for cp in result.concepts_planned:
                concepts.append({
                    "name": cp.name,
                    "title": cp.title,
                    "action": "update" if cp.is_update else "create",
                    "passage_count": len(cp.passages),
                })
            return {
                "status": "ok",
                "dry_run": True,
                "source_path": str(source_path),
                "source_chars": result.source_chars,
                "concepts_found": result.concepts_found,
                "extraction_warning": result.extraction_warning,
                "concepts": concepts,
                "message": "DRY RUN — no pages written",
            }

        # Live ingest response
        return self._ingest_result_to_response(result)

    def _ingest_result_to_response(self, result: "IngestResult") -> dict:
        """Build the live-ingest response dict from an IngestResult.

        Shared by _handle_ingest (sync response) and _handle_ingest_stream
        (done frame). The streaming path adds "type": "done" on top.
        """
        cap = self._config.mcp.ingest_response_max_pages
        all_pages = result.pages_created + result.pages_updated
        truncated = len(all_pages) > cap
        shown = set(all_pages[:cap]) if truncated else set(all_pages)
        warnings = []
        if truncated:
            warnings.append({
                "code": "response-truncated",
                "total_affected": len(all_pages),
                "shown": cap,
                "message": (
                    f"{len(all_pages)} pages affected, showing the first {cap}. "
                    f"Use wiki_lint to see the full attention map."
                ),
            })
        if result.extraction_warning:
            warnings.append({
                "code": "extraction-quality",
                "message": result.extraction_warning,
            })
        response = {
            "status": "ok",
            "pages_created": len(result.pages_created),
            "pages_updated": len(result.pages_updated),
            "created": [n for n in result.pages_created if n in shown],
            "updated": [n for n in result.pages_updated if n in shown],
            "concepts_found": result.concepts_found,
        }
        if truncated:
            response["truncated"] = True
            response["shown"] = cap
        if warnings:
            response["warnings"] = warnings
        return response

    async def _handle_ingest_proposals(self, request: dict) -> dict:
        """Route ingest to the proposal pipeline (proposal_mode=True)."""
        if "source_path" not in request:
            return {"status": "error", "message": "Missing required field: source_path"}

        from llm_wiki.ingest.agent import IngestAgent
        from llm_wiki.traverse.llm_client import LLMClient
        from llm_wiki.vault import Vault

        source_path = Path(request["source_path"])
        author = request.get("author", "cli")
        dry_run = request.get("dry_run", False)
        proposals_dir = self._vault_root / "inbox" / "proposals"

        backend = self._config.llm.resolve("ingest")
        llm = LLMClient(
            self._llm_queue,
            model=backend.model,
            api_base=backend.api_base,
            api_key=backend.api_key,
        )

        vault = Vault.scan(self._vault_root, self._config)
        manifest_lines = [
            f"{name}  '{entry.title}'"
            for name, entry in vault.manifest_entries().items()
        ]

        agent = IngestAgent(llm, self._config)
        result = await agent.ingest_as_proposals(
            source_path=source_path,
            vault_root=self._vault_root,
            proposals_dir=proposals_dir,
            manifest_lines=manifest_lines,
            author=author,
            dry_run=dry_run,
        )

        if dry_run:
            concepts = [
                {
                    "name": cp.name,
                    "title": cp.title,
                    "action": "update" if cp.is_update else "create",
                    "passage_count": len(cp.passages),
                }
                for cp in result.concepts_planned
            ]
            return {
                "status": "ok",
                "dry_run": True,
                "source_path": str(source_path),
                "source_chars": result.source_chars,
                "concepts_found": result.concepts_found,
                "extraction_warning": result.extraction_warning,
                "concepts": concepts,
                "proposal_mode": True,
            }

        return {
            "status": "ok",
            "concepts_found": result.concepts_found,
            "created": result.pages_created,
            "updated": result.pages_updated,
            "extraction_warning": result.extraction_warning,
            "proposal_mode": True,
        }

    async def _handle_ingest_stream(
        self,
        request: dict,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a streaming ingest request, writing frames directly to writer.

        Uses the deep-read proposals pipeline (ingest_as_proposals) with
        write_service wired in for direct page writes — same synthesis quality
        as the proposals path, none of the old shallow extraction path.
        """
        if "source_path" not in request:
            await write_message(writer, {
                "type": "error", "status": "error",
                "message": "Missing required field: source_path",
            })
            return
        if "connection_id" not in request:
            await write_message(writer, {
                "type": "error", "status": "error",
                "message": "Missing required field: connection_id",
            })
            return

        from llm_wiki.ingest.agent import IngestAgent
        from llm_wiki.traverse.llm_client import LLMClient
        from llm_wiki.vault import Vault

        author = request.get("author", "cli")
        connection_id = request["connection_id"]
        source_path = Path(request["source_path"])
        backend = self._config.llm.resolve("ingest")

        # Trace mode: emit {"type": "trace", ...} frames for every LLM call.
        trace_fn = None
        if request.get("trace"):
            async def trace_fn(event: dict) -> None:  # noqa: E731
                await write_message(writer, {"type": "trace", **event})

        llm = LLMClient(
            self._llm_queue,
            model=backend.model,
            api_base=backend.api_base,
            api_key=backend.api_key,
            trace_fn=trace_fn,
        )
        agent = IngestAgent(llm, self._config)

        vault = Vault.scan(self._vault_root, self._config)
        manifest_lines = [
            f"{name}  '{entry.title}'"
            for name, entry in vault.manifest_entries().items()
        ]

        concepts_written = 0

        async def on_progress(frame: dict) -> None:
            nonlocal concepts_written
            await write_message(writer, {"type": "progress", **frame})
            if frame.get("stage") == "concept_done":
                concepts_written += 1

        try:
            result = await agent.ingest_as_proposals(
                source_path=source_path,
                vault_root=self._vault_root,
                proposals_dir=None,
                manifest_lines=manifest_lines,
                author=author,
                connection_id=connection_id,
                write_service=self._page_write_service,
                on_progress=on_progress,
            )
        except Exception as exc:
            logger.exception("Streaming ingest failed after %d concepts", concepts_written)
            await write_message(writer, {
                "type": "error",
                "status": "error",
                "message": str(exc),
                "concepts_written": concepts_written,
            })
            return
        finally:
            try:
                await self.rescan()
            except Exception:
                logger.warning("Failed to rescan vault after streaming ingest")

        done_frame = self._ingest_result_to_response(result)
        done_frame["type"] = "done"
        await write_message(writer, done_frame)


def _serialize_result(r: SearchResult) -> dict:
    return {
        "name": r.name,
        "score": r.score,
        "manifest": r.entry.to_manifest_text(),
    }


def _serialize_write_result(result) -> dict:
    out = {
        "status": result.status,
        "page_path": result.page_path,
        "journal_id": result.journal_id,
        "session_id": result.session_id,
        "content_hash": result.content_hash,
    }
    if result.warnings:
        out["warnings"] = result.warnings
    if result.code is not None:
        out["code"] = result.code
    if result.details:
        out.update(result.details)
    return out


def _serialize_snippet_result(r) -> dict:
    return {
        "name": r.name,
        "score": r.score,
        "manifest": r.entry.to_manifest_text(),
        "matches": [
            {
                "line": m.line,
                "before": m.before,
                "match": m.match,
                "after": m.after,
            }
            for m in r.matches
        ],
    }


def _serialize_issue(issue: "Issue", include_body: bool = False) -> dict:
    data = {
        "id": issue.id,
        "severity": issue.severity,
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


# Severity vocabularies for the enriched routes. Issues use the strict
# auditor subset; talk entries add suggestion + new_connection. Unknown
# values are clamped to the safest in-vocabulary value rather than
# silently growing the dict shape.
_ISSUE_SEVERITIES: tuple[str, ...] = ("critical", "moderate", "minor")
_TALK_SEVERITIES: tuple[str, ...] = (
    "critical", "moderate", "minor", "suggestion", "new_connection",
)


def _empty_severity_counts(vocabulary: tuple[str, ...]) -> dict[str, int]:
    return {sev: 0 for sev in vocabulary}


def _clamp_severity(sev: str, vocabulary: tuple[str, ...], default: str) -> str:
    """Return `sev` if in `vocabulary`, otherwise `default`."""
    return sev if sev in vocabulary else default
