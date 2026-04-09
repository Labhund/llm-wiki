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
class LLMConfig:
    default: str = "openai/local-instruct"
    embeddings: str = "openai/text-embedding-3-small"
    # Set api_base/api_key when using the litellm proxy or any non-default endpoint.
    # Example for local litellm proxy: api_base="http://localhost:4000", api_key="sk-fake"
    api_base: Optional[str] = None
    api_key: Optional[str] = None


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
    auditor_interval: str = "24h"
    authority_recalc: str = "12h"
    compliance_debounce_secs: float = 30.0
    talk_pages_enabled: bool = True
    talk_summary_min_new_entries: int = 5
    talk_summary_min_interval_seconds: int = 3600


@dataclass
class VaultConfig:
    mode: str = "vault"
    raw_dir: str = "raw/"
    wiki_dir: str = "wiki/"
    watch: bool = True


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
        return _merge(cls, data)
