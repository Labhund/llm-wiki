from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Optional

import yaml


def _merge(dc_class, data: dict):
    """Create a dataclass instance, merging dict values over defaults."""
    kwargs = {}
    for f in fields(dc_class):
        if f.name in data:
            val = data[f.name]
            # Recurse into nested dataclasses
            if hasattr(f.type, "__dataclass_fields__") and isinstance(val, dict):
                kwargs[f.name] = _merge(f.type, val)
            else:
                kwargs[f.name] = val
    return dc_class(**kwargs)


@dataclass
class LLMBackend:
    model: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict) -> "LLMBackend":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class LLMConfig:
    # New-style: named backend profiles
    backends: dict[str, LLMBackend] = field(default_factory=dict)
    default_backend: str = "local"  # backend name, not model string

    # Legacy fields — kept for backward compat, used only when backends is empty
    _default_model: Optional[str] = field(default=None, repr=False)
    _default_api_base: Optional[str] = field(default=None, repr=False)
    _default_api_key: Optional[str] = field(default=None, repr=False)

    embeddings: str = "openai/text-embedding-3-small"

    # Per-task role overrides (each resolves to a backend name, falls back to default_backend)
    adversary: Optional[str] = None
    ingest: Optional[str] = None
    librarian: Optional[str] = None
    compliance: Optional[str] = None
    talk_summary: Optional[str] = None
    query: Optional[str] = None
    commit: Optional[str] = None

    def __post_init__(self):
        """Build LLMBackend objects from raw dicts (YAML loads everything as dicts).
        If backends is empty and legacy fields are present, synthesize a single backend.
        If backends is still empty after that, provide a default 'local' backend so
        resolve() never raises on a bare WikiConfig() — tests and unconfigured vaults
        get a no-op backend matching the old openai/local-instruct default."""
        if self.backends:
            self.backends = {
                name: LLMBackend.from_dict(v) if isinstance(v, dict) else v
                for name, v in self.backends.items()
            }
        elif self._default_model:
            self.backends = {
                "default": LLMBackend(
                    model=self._default_model,
                    api_base=self._default_api_base,
                    api_key=self._default_api_key,
                )
            }
            self.default_backend = "default"
        else:
            # Bare WikiConfig() — give it a default backend so resolve() works
            self.backends["local"] = LLMBackend(model="openai/local-instruct")

    def resolve(self, role: Optional[str] = None) -> LLMBackend:
        """Resolve a role to its backend config.
        Falls back to default_backend if role is unset, unknown, or backend name missing."""
        backend_name = getattr(self, role, None) if role else None
        if not backend_name or backend_name not in self.backends:
            backend_name = self.default_backend
        if backend_name not in self.backends:
            raise ValueError(
                f"LLMConfig: backend '{backend_name}' not found. "
                f"Available: {list(self.backends.keys())}"
            )
        return self.backends[backend_name]


@dataclass
class LLMQueueConfig:
    max_concurrent: int = 2
    priority_order: list[str] = field(
        default_factory=lambda: ["query", "ingest", "maintenance"]
    )
    cloud_daily_limit: Optional[int] = None
    cloud_hourly_limit: Optional[int] = None


@dataclass
class SearchConfig:
    backend: str = "tantivy"
    embeddings_enabled: bool = True
    hybrid_weight: float = 0.6


@dataclass
class BudgetConfig:
    default_query: int = 16000
    default_ingest: int = 32000
    manifest_page_size: int = 20
    manifest_refresh_after_traversals: int = 10
    page_viewport_default: str = "top"
    hard_ceiling_pct: float = 0.8
    max_traversal_turns: int = 10


@dataclass
class MaintenanceConfig:
    librarian_interval: str = "6h"
    adversary_interval: str = "12h"
    adversary_claims_per_run: int = 5
    adversary_unread_weight: float = 1.5
    auditor_interval: str = "24h"
    auditor_unread_source_days: int = 30
    authority_recalc: str = "12h"
    compliance_debounce_secs: float = 30.0
    talk_pages_enabled: bool = True
    talk_summary_min_new_entries: int = 5
    talk_summary_min_interval_seconds: int = 3600
    failure_escalation_threshold: int = 3
    maintenance_llm_timeout: int = 120  # seconds per LLM attempt; None disables
    # Cluster D — synthesis + resonance
    synthesis_lint_enabled: bool = False
    synthesis_lint_months: int = 6
    resonance_matching: bool = False
    resonance_candidates_per_claim: int = 3
    resonance_stale_weeks: int = 4
    # Synthesis authority boost — multiplier applied to synthesis pages in
    # compute_authority(). >1.0 boosts, <1.0 penalises. 1.0 = no effect.
    synthesis_authority_boost: float = 1.5
    # Adversary idle guard — bypass mtime check after this many days without a real run
    adversary_force_recheck_days: int = 30


@dataclass
class VaultConfig:
    mode: str = "vault"
    raw_dir: str = "raw/"
    wiki_dir: str = "wiki/"
    inbox_dir: str = "inbox/"    # ← new
    watch: bool = True


@dataclass
class IngestConfig:
    pdf_extractor: str = "pdftotext"              # pdftotext | local-ocr | marker | nougat
    local_ocr_endpoint: str = "http://localhost:8006/v1"
    local_ocr_model: str = "qianfan-ocr"
    chunk_tokens: int = 6000                      # tokens per extraction chunk
    chunk_overlap: float = 0.15                   # fractional overlap between chunks
    max_passages_per_concept: int = 6             # ceiling on passages fed to synthesis
    grounding_auto_merge: float = 0.75            # passage score >= this → auto-merge updates
    grounding_flag: float = 0.50                  # passage score < this → create issue
    auto_copy_to_raw: bool = True                 # copy source to raw/ if outside vault
    # Deep-read synthesis
    synthesis_temperature: float = 0.7            # temperature for synthesis LLM calls
    full_context_chars: int = 800_000             # if paper is within this many chars,
    #   pass full text directly to synthesis.  If larger, run the rolling-digest loop.


@dataclass
class HonchoConfig:
    enabled: bool = False
    endpoint: str = "http://localhost:8000"


@dataclass
class MCPConfig:
    transport: str = "stdio"
    ingest_response_max_pages: int = 15


@dataclass
class SessionsConfig:
    namespace_by_connection: bool = True
    inactivity_timeout_seconds: int = 300
    write_count_cap: int = 30
    cap_warn_ratio: float = 0.6
    auto_commit_user_edits: bool = False
    user_edit_settle_interval_seconds: int = 600


@dataclass
class WriteConfig:
    require_citations_on_create: bool = True
    require_citations_on_append: bool = True
    patch_fuzzy_match_threshold: float = 0.85
    name_jaccard_threshold: float = 0.5
    name_levenshtein_threshold: float = 0.85


@dataclass
class WikiConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_queue: LLMQueueConfig = field(default_factory=LLMQueueConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)
    honcho: HonchoConfig = field(default_factory=HonchoConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    sessions: SessionsConfig = field(default_factory=SessionsConfig)
    write: WriteConfig = field(default_factory=WriteConfig)

    @classmethod
    def load(cls, path: Path) -> "WikiConfig":
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f)
        if not data:
            return cls()
        # Migrate old LLM config keys to new internal names
        if "llm" in data and isinstance(data["llm"], dict):
            llm = data["llm"]
            if "backends" not in llm and "default" in llm:
                llm["_default_model"] = llm.pop("default")
                if "api_base" in llm:
                    llm["_default_api_base"] = llm.pop("api_base")
                if "api_key" in llm:
                    llm["_default_api_key"] = llm.pop("api_key")
        return _merge(cls, data)
