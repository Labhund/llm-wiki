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
    api_base: Optional[str] = "http://localhost:4000"
    api_key: Optional[str] = "sk-fake"  # litellm proxy doesn't validate


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
    compliance_debounce_secs: int = 30
    talk_pages_enabled: bool = True


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
class WikiConfig:
    llm: LLMConfig = field(default_factory=LLMConfig)
    llm_queue: LLMQueueConfig = field(default_factory=LLMQueueConfig)
    search: SearchConfig = field(default_factory=SearchConfig)
    budgets: BudgetConfig = field(default_factory=BudgetConfig)
    maintenance: MaintenanceConfig = field(default_factory=MaintenanceConfig)
    vault: VaultConfig = field(default_factory=VaultConfig)
    honcho: HonchoConfig = field(default_factory=HonchoConfig)

    @classmethod
    def load(cls, path: Path) -> "WikiConfig":
        if not path.exists():
            return cls()
        with open(path) as f:
            data = yaml.safe_load(f)
        if not data:
            return cls()
        return _merge(cls, data)
