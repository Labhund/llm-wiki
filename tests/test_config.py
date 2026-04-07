from pathlib import Path
from llm_wiki.config import WikiConfig


def test_default_config():
    config = WikiConfig()
    assert config.llm.default == "litellm/gemma4"
    assert config.llm.embeddings == "ollama/nomic-embed-text"
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
    assert config.llm.embeddings == "ollama/nomic-embed-text"
    assert config.search.backend == "tantivy"


def test_load_missing_file():
    config = WikiConfig.load(Path("/nonexistent/config.yaml"))
    assert config.llm.default == "litellm/gemma4"


def test_load_empty_file(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("")
    config = WikiConfig.load(config_file)
    assert config.llm.default == "litellm/gemma4"
