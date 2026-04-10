from pathlib import Path

import pytest
import yaml

from llm_wiki.config import WikiConfig, IngestConfig


def test_default_config():
    config = WikiConfig()
    # Bare WikiConfig() gets a default 'local' backend
    backend = config.llm.resolve()
    assert backend.model == "openai/local-instruct"
    assert config.llm.embeddings == "openai/text-embedding-3-small"
    assert config.llm.default_backend == "local"
    assert config.search.backend == "tantivy"
    assert config.budgets.default_query == 16000
    assert config.budgets.hard_ceiling_pct == 0.8
    assert config.vault.mode == "vault"


def test_load_from_yaml(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "llm:\n"
        "  default: ollama/llama3\n"
        "budgets:\n"
        "  default_query: 8192\n"
        "vault:\n"
        "  mode: managed\n"
    )
    config = WikiConfig.load(config_file)
    # Old-style "default" is migrated to legacy backend
    assert config.llm.backends["default"].model == "ollama/llama3"
    assert config.budgets.default_query == 8192
    assert config.vault.mode == "managed"
    assert config.llm.embeddings == "openai/text-embedding-3-small"
    assert config.search.backend == "tantivy"


def test_load_missing_file():
    config = WikiConfig.load(Path("/nonexistent/config.yaml"))
    backend = config.llm.resolve()
    assert backend.model == "openai/local-instruct"


def test_load_empty_file(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    config = WikiConfig.load(config_file)
    backend = config.llm.resolve()
    assert backend.model == "openai/local-instruct"


def test_maintenance_config_has_talk_summary_defaults():
    """Phase 6a adds talk-page summary refresh fields with safe defaults."""
    cfg = WikiConfig()
    assert cfg.maintenance.talk_summary_min_new_entries == 5
    assert cfg.maintenance.talk_summary_min_interval_seconds == 3600


def test_maintenance_config_loads_talk_summary_overrides(tmp_path: Path):
    """A config file can override the talk-summary defaults."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "maintenance": {
            "talk_summary_min_new_entries": 3,
            "talk_summary_min_interval_seconds": 1800,
        }
    }))
    cfg = WikiConfig.load(cfg_file)
    assert cfg.maintenance.talk_summary_min_new_entries == 3
    assert cfg.maintenance.talk_summary_min_interval_seconds == 1800


def test_mcp_config_defaults():
    cfg = WikiConfig()
    assert cfg.mcp.transport == "stdio"
    assert cfg.mcp.ingest_response_max_pages == 15


def test_sessions_config_defaults():
    cfg = WikiConfig()
    assert cfg.sessions.namespace_by_connection is True
    assert cfg.sessions.inactivity_timeout_seconds == 300
    assert cfg.sessions.write_count_cap == 30
    assert cfg.sessions.cap_warn_ratio == 0.6
    assert cfg.sessions.auto_commit_user_edits is False
    assert cfg.sessions.user_edit_settle_interval_seconds == 600


def test_write_config_defaults():
    cfg = WikiConfig()
    assert cfg.write.require_citations_on_create is True
    assert cfg.write.require_citations_on_append is True
    assert cfg.write.patch_fuzzy_match_threshold == 0.85
    assert cfg.write.name_jaccard_threshold == 0.5
    assert cfg.write.name_levenshtein_threshold == 0.85


def test_phase6b_config_loads_overrides(tmp_path: Path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(yaml.dump({
        "mcp": {"ingest_response_max_pages": 30},
        "sessions": {
            "inactivity_timeout_seconds": 60,
            "write_count_cap": 10,
            "namespace_by_connection": False,
        },
        "write": {
            "require_citations_on_create": False,
            "name_jaccard_threshold": 0.4,
        },
    }))
    cfg = WikiConfig.load(cfg_file)
    assert cfg.mcp.ingest_response_max_pages == 30
    assert cfg.sessions.inactivity_timeout_seconds == 60
    assert cfg.sessions.write_count_cap == 10
    assert cfg.sessions.namespace_by_connection is False
    assert cfg.write.require_citations_on_create is False
    assert cfg.write.name_jaccard_threshold == 0.4


def test_maintenance_config_has_synthesis_defaults():
    cfg = WikiConfig()
    assert cfg.maintenance.synthesis_lint_enabled is False
    assert cfg.maintenance.synthesis_lint_months == 6


def test_maintenance_config_has_resonance_defaults():
    cfg = WikiConfig()
    assert cfg.maintenance.resonance_matching is False
    assert cfg.maintenance.resonance_candidates_per_claim == 3
    assert cfg.maintenance.resonance_stale_weeks == 4


def test_ingest_config_defaults():
    c = WikiConfig()
    assert c.ingest.pdf_extractor == "pdftotext"
    assert c.ingest.local_ocr_endpoint == "http://localhost:8006/v1"
    assert c.ingest.local_ocr_model == "qianfan-ocr"


def test_ingest_config_loads_from_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "ingest:\n"
        "  pdf_extractor: local-ocr\n"
        "  local_ocr_endpoint: http://gpu-box:8006/v1\n"
        "  local_ocr_model: my-ocr-model\n"
    )
    c = WikiConfig.load(cfg_file)
    assert c.ingest.pdf_extractor == "local-ocr"
    assert c.ingest.local_ocr_endpoint == "http://gpu-box:8006/v1"
    assert c.ingest.local_ocr_model == "my-ocr-model"


def test_maintenance_config_synthesis_authority_boost_default():
    """synthesis_authority_boost defaults to 1.5."""
    cfg = WikiConfig()
    assert cfg.maintenance.synthesis_authority_boost == 1.5


def test_ingest_config_new_defaults():
    cfg = IngestConfig()
    assert cfg.chunk_tokens == 6000
    assert cfg.chunk_overlap == 0.15
    assert cfg.max_passages_per_concept == 6
    assert cfg.grounding_auto_merge == 0.75
    assert cfg.grounding_flag == 0.50
    assert cfg.auto_copy_to_raw is True


def test_ingest_config_loads_new_fields_from_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "ingest:\n"
        "  chunk_tokens: 4000\n"
        "  grounding_auto_merge: 0.8\n"
        "  auto_copy_to_raw: false\n"
    )
    c = WikiConfig.load(cfg_file)
    assert c.ingest.chunk_tokens == 4000
    assert c.ingest.grounding_auto_merge == 0.8
    assert c.ingest.auto_copy_to_raw is False
    assert c.ingest.grounding_flag == 0.50  # default preserved
