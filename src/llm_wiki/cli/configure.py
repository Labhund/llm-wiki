"""Interactive setup wizard for llm-wiki — guides users through LLM backend and API key configuration."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml


# ── ANSI colors ───────────────────────────────────────────────────────────────

class _C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[31m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    CYAN   = "\033[36m"


def _col(text: str, *codes: str) -> str:
    if not sys.stdout.isatty():
        return text
    return "".join(codes) + text + _C.RESET


def _header(title: str) -> None:
    print()
    print(_col(f"◆ {title}", _C.CYAN, _C.BOLD))
    print()


def _info(text: str) -> None:
    print(_col(f"  {text}", _C.DIM))


def _ok(text: str) -> None:
    print(_col(f"  ✓ {text}", _C.GREEN))


def _warn(text: str) -> None:
    print(_col(f"  ⚠ {text}", _C.YELLOW))


def _err(text: str) -> None:
    print(_col(f"  ✗ {text}", _C.RED))


# ── Prompts ───────────────────────────────────────────────────────────────────

def _prompt(question: str, default: str = "", password: bool = False) -> str:
    display = f"{question} [{default}]: " if default else f"{question}: "
    try:
        if password:
            import getpass
            value = getpass.getpass(_col(display, _C.YELLOW))
        else:
            value = input(_col(display, _C.YELLOW))
        return value.strip() or default
    except (KeyboardInterrupt, EOFError):
        print()
        sys.exit(0)


def _yes_no(question: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            raw = input(_col(f"{question} [{hint}]: ", _C.YELLOW)).strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        _err("Please enter 'y' or 'n'")


def _curses_menu(question: str, choices: list[str], default: int = 0) -> int:
    """Arrow-key menu via stdlib curses. Returns index, or -1 on error."""
    try:
        import curses
        result = [default]

        def _draw(stdscr: Any) -> None:
            curses.curs_set(0)
            if curses.has_colors():
                curses.start_color()
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_GREEN, -1)
                curses.init_pair(2, curses.COLOR_YELLOW, -1)
            cursor = default
            scroll = 0
            while True:
                stdscr.clear()
                max_y, max_x = stdscr.getmaxyx()
                visible = max(1, max_y - 3)
                if cursor < scroll:
                    scroll = cursor
                elif cursor >= scroll + visible:
                    scroll = cursor - visible + 1
                scroll = max(0, min(scroll, max(0, len(choices) - visible)))
                try:
                    attr = curses.A_BOLD | (curses.color_pair(2) if curses.has_colors() else 0)
                    stdscr.addnstr(0, 0, question, max_x - 1, attr)
                except curses.error:
                    pass
                for row, i in enumerate(range(scroll, min(scroll + visible, len(choices)))):
                    y = row + 2
                    if y >= max_y - 1:
                        break
                    arrow = "→" if i == cursor else " "
                    line = f" {arrow}  {choices[i]}"
                    attr = curses.A_NORMAL
                    if i == cursor:
                        attr = curses.A_BOLD | (curses.color_pair(1) if curses.has_colors() else 0)
                    try:
                        stdscr.addnstr(y, 0, line, max_x - 1, attr)
                    except curses.error:
                        pass
                stdscr.refresh()
                key = stdscr.getch()
                if key in (curses.KEY_UP, ord("k")):
                    cursor = (cursor - 1) % len(choices)
                elif key in (curses.KEY_DOWN, ord("j")):
                    cursor = (cursor + 1) % len(choices)
                elif key in (curses.KEY_ENTER, 10, 13):
                    result[0] = cursor
                    return
                elif key in (27, ord("q")):
                    return

        curses.wrapper(_draw)
        return result[0]
    except Exception:
        return -1


def _choice(question: str, choices: list[str], default: int = 0) -> int:
    """Arrow-key menu with numbered fallback."""
    idx = _curses_menu(question, choices, default)
    if idx >= 0:
        print(_col(f"  → {choices[idx]}", _C.GREEN))
        print()
        return idx
    # Fallback: numbered list
    print(_col(question, _C.YELLOW))
    for i, c in enumerate(choices):
        marker = "●" if i == default else "○"
        line = f"  {marker} {c}"
        print(_col(line, _C.GREEN) if i == default else line)
    _info(f"  Enter for default ({default + 1})  Ctrl+C to exit")
    while True:
        try:
            raw = input(_col(f"  Select [1-{len(choices)}] ({default + 1}): ", _C.DIM))
            if not raw:
                return default
            n = int(raw) - 1
            if 0 <= n < len(choices):
                return n
            _err(f"Enter a number between 1 and {len(choices)}")
        except ValueError:
            _err("Please enter a number")
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)


# ── Provider setup ────────────────────────────────────────────────────────────

_LOCAL_MODELS = ["llama3.2", "llama3.1:70b", "qwen2.5-coder:32b", "mistral", "other (type manually)"]
_OPENAI_MODELS = ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "o3-mini", "other (type manually)"]
_ANTHROPIC_MODELS = [
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-opus-4-6",
    "anthropic/claude-haiku-4-5-20251001",
    "other (type manually)",
]
_OPENROUTER_MODELS = [
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-opus-4-6",
    "google/gemini-2.5-pro",
    "deepseek/deepseek-r1",
    "other (type manually)",
]

_PROVIDERS = [
    "Local  (ollama, vllm, LiteLLM proxy — no API key needed)",
    "OpenAI",
    "Anthropic  (Claude)",
    "OpenRouter  (many models, one key)",
    "Custom  (any OpenAI-compatible endpoint)",
]

_PROVIDER_NAMES = ["local", "openai", "anthropic", "openrouter", "custom"]


def _pick_or_type(choices: list[str], default: int = 0) -> str:
    """Choose from list; last entry is always 'other (type manually)'."""
    idx = _choice("Select model:", choices, default)
    if idx == len(choices) - 1:
        return _prompt("Model name")
    return choices[idx]


def _setup_local() -> tuple[str, dict[str, Any]]:
    _info("Common base URLs:")
    _info("  ollama:       http://localhost:11434/v1")
    _info("  vllm:         http://localhost:8000/v1")
    _info("  LiteLLM proxy: http://localhost:4000/v1")
    print()
    api_base = _prompt("Base URL", "http://localhost:11434/v1")
    raw_model = _pick_or_type(_LOCAL_MODELS)
    # Strip any openai/ prefix the user might have typed; we'll add it
    model = raw_model.lstrip("openai/") if raw_model.startswith("openai/") else raw_model
    model_str = f"openai/{model}"
    api_key = _prompt("API key (press Enter to skip)")
    backend: dict[str, Any] = {"model": model_str, "api_base": api_base}
    if api_key:
        backend["api_key"] = api_key
    return "local", backend


def _setup_openai() -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://platform.openai.com/api-keys")
    print()
    api_key = _prompt("OpenAI API key", password=True)
    model = _pick_or_type(_OPENAI_MODELS)
    return "openai", {"model": model, "api_key": api_key}


def _setup_anthropic() -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://console.anthropic.com/")
    print()
    api_key = _prompt("Anthropic API key", password=True)
    model = _pick_or_type(_ANTHROPIC_MODELS)
    return "anthropic", {"model": model, "api_key": api_key}


def _setup_openrouter() -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://openrouter.ai/keys")
    _info("OpenRouter gives you access to 200+ models with a single key.")
    print()
    api_key = _prompt("OpenRouter API key", password=True)
    model_short = _pick_or_type(_OPENROUTER_MODELS)
    # Prefix with openrouter/ for LiteLLM routing unless user already did
    if not model_short.startswith("openrouter/"):
        model_str = f"openrouter/{model_short}"
    else:
        model_str = model_short
    return "openrouter", {
        "model": model_str,
        "api_base": "https://openrouter.ai/api/v1",
        "api_key": api_key,
    }


def _setup_custom() -> tuple[str, dict[str, Any]]:
    _info("Any OpenAI-compatible endpoint works (vLLM, Together AI, Groq, etc.)")
    print()
    api_base = _prompt("Base URL (e.g. https://api.groq.com/openai/v1)")
    model = _prompt("Model name (e.g. llama-3.3-70b-versatile)")
    api_key = _prompt("API key (press Enter to skip)", password=True)
    backend: dict[str, Any] = {"model": model, "api_base": api_base}
    if api_key:
        backend["api_key"] = api_key
    return "custom", backend


_PROVIDER_SETUP = [_setup_local, _setup_openai, _setup_anthropic, _setup_openrouter, _setup_custom]


# ── Task role overrides ───────────────────────────────────────────────────────

_ROLES = [
    ("query",       "Answering user questions (your main interactive model)"),
    ("ingest",      "Extracting concepts from documents"),
    ("librarian",   "Background maintenance (linking, clustering)"),
    ("adversary",   "Fact-checking claims in the wiki"),
    ("compliance",  "Checking write quality and citations"),
    ("commit",      "Summarising session commits"),
]


def _setup_role_overrides(backends: dict[str, dict], default_backend: str) -> dict[str, str]:
    """Optionally assign different backends to specific roles."""
    _info("By default, all tasks use the same backend you just configured.")
    _info("You can optionally route specific tasks to a different backend")
    _info("(e.g. a cheaper/faster model for background maintenance).")
    print()
    if not _yes_no("Configure per-task model overrides?", default=False):
        return {}

    overrides: dict[str, str] = {}
    backend_names = list(backends.keys())

    for role, description in _ROLES:
        print()
        _info(f"  {role}: {description}")
        choices = [f"{n}  (current default)" if n == default_backend else n for n in backend_names]
        choices += ["Add a new backend for this role", "Skip (use default)"]

        idx = _choice(f"  Backend for '{role}':", choices, default=len(backend_names))
        if idx < len(backend_names):
            overrides[role] = backend_names[idx]
        elif idx == len(backend_names):
            # Add new backend
            print()
            _header("New Backend")
            provider_idx = _choice("Provider:", _PROVIDERS, default=0)
            name, backend_cfg = _PROVIDER_SETUP[provider_idx]()
            # Pick a unique name
            base_name = _PROVIDER_NAMES[provider_idx]
            new_name = base_name
            suffix = 2
            while new_name in backends:
                new_name = f"{base_name}{suffix}"
                suffix += 1
            backends[new_name] = backend_cfg
            overrides[role] = new_name
            _ok(f"Added backend '{new_name}' and assigned to '{role}'")
        # else: skip — use default

    return overrides


# ── Embeddings ────────────────────────────────────────────────────────────────

_EMBED_CHOICES = [
    "OpenAI  (text-embedding-3-small) — best quality, requires OpenAI API key",
    "Disable embeddings  (keyword-only search, no API key needed)",
    "Custom  (any OpenAI-compatible embedding endpoint)",
]


def _setup_embeddings(openai_key: str = "") -> tuple[str, bool, str | None]:
    """Returns (model_str, enabled, optional_api_key)."""
    _info("Embeddings power semantic (meaning-based) search in your wiki.")
    print()
    idx = _choice("Embedding backend:", _EMBED_CHOICES, default=0)

    if idx == 0:
        if openai_key:
            _info("Using your OpenAI API key for embeddings.")
        else:
            _info("Get your key at: https://platform.openai.com/api-keys")
            openai_key = _prompt("OpenAI API key for embeddings", password=True)
        return "openai/text-embedding-3-small", True, openai_key or None

    if idx == 1:
        _info("Embeddings disabled — the wiki will use keyword search only.")
        return "openai/text-embedding-3-small", False, None

    # Custom
    _info("Provide an OpenAI-compatible embedding endpoint.")
    print()
    api_base = _prompt("Base URL (e.g. http://localhost:11434/v1)")
    model = _prompt("Embedding model name (e.g. nomic-embed-text)")
    api_key = _prompt("API key (press Enter to skip)", password=True)
    model_str = f"openai/{model}" if not model.startswith("openai/") else model
    return model_str, True, api_key or None


# ── Main wizard ───────────────────────────────────────────────────────────────

def run_wizard(vault_path: Path) -> None:
    """Run the interactive configuration wizard."""
    if not sys.stdin.isatty():
        print()
        print(_col("⚕ llm-wiki Configure — Non-interactive mode", _C.CYAN, _C.BOLD))
        print()
        _info("This wizard requires an interactive terminal.")
        _info("Configure manually by editing:")
        _info(f"  {vault_path}/schema/config.yaml")
        _info("")
        _info("Or set environment variables and re-run in a terminal:")
        _info("  OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.")
        print()
        return

    # Banner
    print()
    print(_col("┌─────────────────────────────────────────────────────────┐", _C.CYAN))
    print(_col("│           llm-wiki Setup Wizard                         │", _C.CYAN, _C.BOLD))
    print(_col("└─────────────────────────────────────────────────────────┘", _C.CYAN))
    print()
    _info("This wizard sets up your LLM backends and API keys.")

    config_path = vault_path / "schema" / "config.yaml"
    _info(f"Config location: {config_path}")
    print()

    # Load existing config
    existing: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            existing = yaml.safe_load(f) or {}
        _warn("Existing config found — wizard will update it.")
        if not _yes_no("Continue?", default=True):
            _info("Aborted.")
            return
        print()

    # ── LLM Backend ──────────────────────────────────────────────────────────
    _header("LLM Backend")
    _info("llm-wiki uses LiteLLM to route requests to any model provider.")
    _info("Which provider do you want to use?")
    print()

    provider_idx = _choice("Provider:", _PROVIDERS, default=0)
    _header(_PROVIDERS[provider_idx].split("  ")[0].strip())

    backend_name, backend_cfg = _PROVIDER_SETUP[provider_idx]()
    backends: dict[str, dict] = {backend_name: backend_cfg}
    default_backend = backend_name

    _ok(f"Backend '{backend_name}' configured")

    # Capture OpenAI key for embeddings reuse
    openai_key = ""
    if backend_name == "openai":
        openai_key = backend_cfg.get("api_key", "")

    # ── Per-task overrides ───────────────────────────────────────────────────
    print()
    _header("Per-task Model Overrides  (optional)")
    role_overrides = _setup_role_overrides(backends, default_backend)

    # ── Embeddings ────────────────────────────────────────────────────────────
    print()
    _header("Embeddings")
    embed_model, embed_enabled, embed_key = _setup_embeddings(openai_key)

    # ── Build config dict ─────────────────────────────────────────────────────
    config: dict[str, Any] = existing.copy()

    llm_section: dict[str, Any] = {
        "backends": backends,
        "default_backend": default_backend,
        "embeddings": embed_model,
    }
    llm_section.update(role_overrides)
    config["llm"] = llm_section

    search_section = config.get("search", {})
    search_section["embeddings_enabled"] = embed_enabled
    config["search"] = search_section

    # If we got a separate OpenAI key for embeddings (and it differs from the
    # main backend key), store it under an "embeddings" backend so litellm can
    # pick it up when the embeddings model is called.
    if embed_key and embed_key != openai_key:
        backends["embeddings"] = {
            "model": embed_model,
            "api_key": embed_key,
        }

    # ── Write config ──────────────────────────────────────────────────────────
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    # ── Summary ───────────────────────────────────────────────────────────────
    _header("Setup Complete")

    features = [
        (f"LLM backend ({backend_name})", True, None),
        ("Embeddings / semantic search", embed_enabled, "disable with search.embeddings_enabled: false"),
        ("Per-task model overrides", bool(role_overrides),
         "run wizard again to configure"),
    ]

    enabled = sum(1 for _, ok, _ in features if ok)
    _info(f"{enabled}/{len(features)} features configured:")
    print()
    for name, ok, hint in features:
        if ok:
            print(f"   {_col('✓', _C.GREEN)} {name}")
        else:
            dim = f"  {_col(f'({hint})', _C.DIM)}" if hint else ""
            print(f"   {_col('✗', _C.RED)} {name}{dim}")

    print()
    _ok(f"Config written to {config_path}")
    print()
    print(_col("  Next steps:", _C.CYAN, _C.BOLD))
    _info(f"  llm-wiki serve {vault_path}    Start the wiki daemon")
    _info(f"  llm-wiki query \"...\"           Ask a question")
    _info(f"  llm-wiki ingest <file>         Add a document")
    print()
