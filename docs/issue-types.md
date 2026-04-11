# Issue Types Reference

Every issue filed by llm-wiki lands in `wiki/.issues/` as a markdown file.
Each file has YAML frontmatter with `id`, `type`, `status`, `severity`, `page`,
`detected_by`, and `metadata`. The body is human-readable.

There are two independent systems that file issues: the **structural auditor**
(periodic lint) and the **compliance reviewer** (triggered on file change).

---

## Structural Auditor (`audit/checks.py` via `Auditor.audit()`)

Runs periodically on the full vault. Each check is pure Python, no LLM calls.
Triggered by the daemon's `IntervalScheduler` and on-demand via `wiki_lint`.

### orphan

A wiki page that no other page links to. Skips entry-point pages (index, etc).

- **Severity:** minor
- **Detected by:** `find_orphans`
- **Resolution:** Link to it from a related page, or delete it if obsolete.

### broken-link

A `[[target]]` wikilink in a page body that does not match any known page slug.
The page parser already strips links to non-page files (PDFs, images), so this
only catches references to pages that should exist but don't.

- **Severity:** moderate
- **Detected by:** `find_broken_wikilinks`
- **Resolution:** Create the missing page, fix the wikilink slug (case mismatch,
  typo), or remove the link.

### broken-citation

A `[[raw/filename]]` reference in page frontmatter or body that points to a file
that does not exist under `vault_root/raw/`.

- **Severity:** moderate
- **Detected by:** `find_broken_citations`
- **Resolution:** Restore the missing source file or fix the citation path.

### uncited-source

A page has a `source:` frontmatter field (or was `created_by: ingest/proposal`)
but its body contains zero inline `[[raw/...]]` citations. The adversary cannot
verify any claims on the page because it finds no citation-backed sentences.

- **Severity:** moderate
- **Detected by:** `find_uncited_sourced_pages`
- **Resolution:** Add inline `[[raw/<filename>]]` citations to the body text.

### missing-frontmatter

A page file does not start with `---` YAML frontmatter, or is missing required
fields (title, type, status, created).

- **Severity:** minor
- **Detected by:** `find_missing_frontmatter`
- **Resolution:** Add frontmatter with the required fields.

### missing-markers

A heading in a page body that is not preceded by a `%% section: slug %%` marker.
Section markers are used for token counting and structural navigation.

- **Severity:** minor
- **Detected by:** `find_missing_markers`
- **Resolution:** Insert `%% section: slug %%` above the orphan heading.

### source-gaps

Scans `raw/` for sources with missing or stale metadata. Four sub-types:

| Sub-type | Severity | Condition |
|---|---|---|
| `bare-source` | minor | Binary file (PDF) with no companion `.md` metadata file |
| `missing-reading-status` | minor | `.md` companion exists but has no `reading_status` field |
| `unread-source` | minor | Source unread for longer than `auditor_unread_source_days` |
| `in-progress-no-plan` | moderate | `reading_status: in_progress` with no matching `inbox/` plan file |

- **Detected by:** `find_source_gaps`
- **Resolution:** Run `wiki_ingest` on the source, or call `wiki_source_mark`
  to register it.

### stale-resonance

An open resonance talk entry (type `resonance`) older than
`resonance_stale_weeks * 7` days. The entry was flagged by the resonance matcher
but never reviewed.

- **Severity:** minor
- **Detected by:** `find_stale_resonance`
- **Resolution:** Review the resonance entry — promote to main content, add a
  cross-reference, or resolve as a false match.

### synthesis-without-resonance

A synthesis page (`type: synthesis`) older than `synthesis_lint_months` that has
no resonance talk entries at all. Gated by `maintenance.synthesis_lint_enabled`
(default: False).

- **Severity:** minor
- **Detected by:** `find_synthesis_without_resonance`
- **Resolution:** Run the resonance matcher on the page, or resolve if intentional.

### inbox-in-progress

An `inbox/` plan file with `status: in-progress`. Acts as a nudge to complete
stalled ingest sessions.

- **Severity:** minor
- **Detected by:** `find_inbox_staleness`
- **Resolution:** Complete the ingest, or mark the plan as completed/cancelled.

### proposal / merge-ready / proposal-verification-failed

Classifies pending proposals in `inbox/proposals/`:

| Sub-type | Condition |
|---|---|
| `merge-ready` | `action: update` and all verifiable grounding scores >= `auto_merge_threshold` |
| `proposal` | `action: create` (requires human review) or target page missing |
| `proposal-verification-failed` | Any verifiable grounding score < `flag_threshold` |

- **Severity:** moderate
- **Detected by:** `find_pending_proposals`
- **Resolution:** Review and approve/reject the proposal, or fix grounding issues.

### index-out-of-sync

Drift between `wiki/index.md` and the vault manifest. Two failure modes:

| Sub-type | Severity | Condition |
|---|---|---|
| missing | minor | Page slug exists in vault but absent from index body |
| broken-link | moderate | `[[target]]` in index does not match any known page slug |

- **Detected by:** `find_index_out_of_sync`
- **Resolution:** Add the missing `[[slug]]` to index, or fix the broken link.

---

## Compliance Reviewer (`audit/compliance.py` via `ComplianceReviewer`)

Triggered by the file watcher when a `.md` file under the vault root changes
(after a debounce window). Runs three checks per changed file. No LLM calls.

### compliance (missing-citation)

A sentence in the page body does not contain any `[[...]]` wikilink citation.
For first-time-seen pages (no prior snapshot), every body sentence is checked.
For edits, only newly-added sentences are checked.

Skipped for synthesis pages (`type: synthesis` in frontmatter) — the analysis
session itself is the source.

- **Severity:** moderate
- **Detected by:** `_check_missing_citation`
- **Resolution:** Add a `[[source]]` wikilink to the sentence, or revise it.

**Known bug:** The file watcher scans the entire `vault_root` (not just `wiki/`),
so changes to `raw/*.md` files also trigger compliance review. Raw paper
transcripts contain thousands of uncited sentences, generating massive amounts
of phantom issues. The fix is to scope compliance review to `wiki/` only in
`server.py:on_file_change`.

### compliance (structural-drift)

A heading in the page body lacks a `%% section: slug %%` marker directly above
it. Auto-fixed in-place by inserting the missing marker — the file is rewritten
on disk. Headings inside fenced code blocks are correctly skipped.

- **Severity:** auto-fix (no issue filed)
- **Detected by:** `_check_structural_drift`

### new-idea

A paragraph >=200 chars was added by an edit (not a creation). Flagged for the
librarian to review — should it be integrated, sourced, or moved to the talk
page? Skipped for first-time-seen pages where `old_content` is None.

- **Severity:** moderate
- **Detected by:** `_check_new_idea`
- **Resolution:** Review the new paragraph. Integrate into existing content,
  add citations, or move to the talk page if speculative.

---

## Issue Lifecycle

| Status | Meaning |
|---|---|
| `open` | Active, needs attention |
| `resolved` | Addressed (manually or automatically) |

Issues are idempotent: filing the same issue twice is a no-op (deduplication by
`Issue.make_id`). Resolved issues can be re-filed if the underlying condition
recurs on the next audit run.

## Issue Storage

```
wiki/
  .issues/
    broken-link-boltz-2-95b26d.md
    orphan-full-atom-mpnn-7dc011.md
    compliance-proteindj-0003e0.md
    new-idea-how-proteindj-uses-boltz-2-a47add.md
    ...
```

Filename format: `{type}-{page}-{random-hex}.md`. The `.issues` directory is
treated as hidden (skipped by the file watcher and vault scanner).
