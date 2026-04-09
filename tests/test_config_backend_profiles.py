from pathlib import Path

import pytest
import yaml

from llm_wiki.config import LLMBackend, LLMConfig


class TestLLMBackend:
    def test_from_dict_all_fields(self):
        b = LLMBackend.from_dict({
            "model": "openai/gpt-4",
            "api_base": "http://localhost:4000/v1",
            "api_key": "sk-fake",
        })
        assert b.model == "openai/gpt-4"
        assert b.api_base == "http://localhost:4000/v1"
        assert b.api_key == "sk-fake"

    def test_from_dict_extra_keys_ignored(self):
        b = LLMBackend.from_dict({
            "model": "openai/gpt-4",
            "garbage": "ignored",
        })
        assert b.model == "openai/gpt-4"
        assert not hasattr(b, "garbage")

    def test_from_dict_model_required(self):
        with pytest.raises(TypeError):
            LLMBackend.from_dict({"api_base": "http://localhost"})


class TestLLMConfigResolve:
    def _make_config(self, **overrides):
        defaults = {
            "backends": {
                "fast": LLMBackend(model="openai/gemma"),
                "deep": LLMBackend(model="openai/qwen35"),
            },
            "default_backend": "fast",
        }
        defaults.update(overrides)
        return LLMConfig(**defaults)

    def test_resolve_default_no_role(self):
        cfg = self._make_config()
        backend = cfg.resolve()
        assert backend.model == "openai/gemma"

    def test_resolve_role_override(self):
        cfg = self._make_config(adversary="deep")
        backend = cfg.resolve("adversary")
        assert backend.model == "openai/qwen35"

    def test_resolve_falls_back_on_unknown_role(self):
        cfg = self._make_config()
        backend = cfg.resolve("nonexistent_role")
        assert backend.model == "openai/gemma"

    def test_resolve_falls_back_on_none_role_value(self):
        cfg = self._make_config(adversary=None)
        backend = cfg.resolve("adversary")
        assert backend.model == "openai/gemma"

    def test_resolve_raises_on_missing_backend(self):
        cfg = self._make_config(default_backend="nonexistent")
        with pytest.raises(ValueError, match="backend 'nonexistent' not found"):
            cfg.resolve()

    def test_resolve_error_lists_available(self):
        cfg = self._make_config(default_backend="missing")
        with pytest.raises(ValueError, match="fast.*deep"):
            cfg.resolve()


class TestLLMConfigPostInit:
    def test_dicts_converted_to_llm_backend(self):
        cfg = LLMConfig(
            backends={
                "fast": {"model": "openai/gemma", "api_base": "http://localhost:8004/v1"},
            },
            default_backend="fast",
        )
        assert isinstance(cfg.backends["fast"], LLMBackend)
        assert cfg.backends["fast"].model == "openai/gemma"
        assert cfg.backends["fast"].api_base == "http://localhost:8004/v1"

    def test_already_llm_backend_passthrough(self):
        b = LLMBackend(model="openai/gemma")
        cfg = LLMConfig(backends={"fast": b}, default_backend="fast")
        assert cfg.backends["fast"] is b


class TestLLMConfigLegacyCompat:
    def test_legacy_synthesizes_default_backend(self):
        cfg = LLMConfig(
            _default_model="openai/local-instruct",
            _default_api_base="http://localhost:4000",
            _default_api_key="sk-fake",
        )
        assert "default" in cfg.backends
        assert cfg.backends["default"].model == "openai/local-instruct"
        assert cfg.backends["default"].api_base == "http://localhost:4000"
        assert cfg.default_backend == "default"

    def test_legacy_resolve(self):
        cfg = LLMConfig(_default_model="openai/local-instruct")
        backend = cfg.resolve()
        assert backend.model == "openai/local-instruct"

    def test_no_backends_no_legacy_raises(self):
        cfg = LLMConfig()
        with pytest.raises(ValueError):
            cfg.resolve()


class TestWikiConfigLoadMigration:
    def test_legacy_yaml_migrated(self, tmp_path: Path):
        """Old-style YAML with default/api_base/api_key is migrated to internal fields."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "llm:\n"
            "  default: ollama/llama3\n"
            "  api_base: http://localhost:11434\n"
            "  api_key: sk-123\n"
        )
        from llm_wiki.config import WikiConfig
        cfg = WikiConfig.load(cfg_file)
        assert cfg.llm.default_backend == "default"
        assert cfg.llm.backends["default"].model == "ollama/llama3"
        assert cfg.llm.backends["default"].api_base == "http://localhost:11434"
        assert cfg.llm.backends["default"].api_key == "sk-123"
        # resolve works through legacy backend
        backend = cfg.llm.resolve()
        assert backend.model == "ollama/llama3"

    def test_new_style_yaml_with_backends(self, tmp_path: Path):
        """New-style YAML with backends dict works directly."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "llm": {
                "backends": {
                    "fast": {"model": "openai/gemma"},
                    "deep": {"model": "openai/qwen35", "api_base": "http://localhost:4000/v1"},
                },
                "default_backend": "fast",
                "adversary": "deep",
            }
        }))
        from llm_wiki.config import WikiConfig
        cfg = WikiConfig.load(cfg_file)
        assert isinstance(cfg.llm.backends["fast"], LLMBackend)
        assert cfg.llm.backends["deep"].model == "openai/qwen35"
        assert cfg.llm.resolve("adversary").model == "openai/qwen35"
        assert cfg.llm.resolve().model == "openai/gemma"

    def test_missing_file_still_works(self):
        """No config file = default LLMConfig (no backends, resolve raises)."""
        from llm_wiki.config import WikiConfig
        cfg = WikiConfig.load(Path("/nonexistent/config.yaml"))
        with pytest.raises(ValueError):
            cfg.llm.resolve()
