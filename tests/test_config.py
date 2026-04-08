from pathlib import Path
from llm_wiki.config import WikiConfig


def test_default_config():
    config = WikiConfig()
    assert config.llm.default == "openai/local-instruct"
    assert config.llm.embeddings == "openai/text-embedding-3-small"
    assert config.llm.api_base is None
    assert config.llm.api_key is None
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
    assert config.llm.default == "ollama/llama3"
    assert config.budgets.default_query == 8192
    assert config.vault.mode == "managed"
    # Non-specified fields keep defaults
    assert config.llm.embeddings == "openai/text-embedding-3-small"
    assert config.search.backend == "tantivy"


def test_load_missing_file():
    config = WikiConfig.load(Path("/nonexistent/config.yaml"))
    assert config.llm.default == "openai/local-instruct"


def test_load_empty_file(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    config = WikiConfig.load(config_file)
    assert config.llm.default == "openai/local-instruct"


def test_maintenance_config_has_talk_summary_defaults():
    """Phase 6a adds talk-page summary refresh fields with safe defaults."""
    from llm_wiki.config import WikiConfig

    cfg = WikiConfig()
    assert cfg.maintenance.talk_summary_min_new_entries == 5
    assert cfg.maintenance.talk_summary_min_interval_seconds == 3600


def test_maintenance_config_loads_talk_summary_overrides(tmp_path):
    """A config file can override the talk-summary defaults."""
    import yaml
    from llm_wiki.config import WikiConfig

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
