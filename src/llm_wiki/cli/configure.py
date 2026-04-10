"""Interactive setup wizard for llm-wiki — guides users through LLM backend and API key configuration."""
from __future__ import annotations

import hashlib as _hashlib
import sys
from pathlib import Path
from typing import Any

import yaml


def _skills_source() -> Path:
    """Locate the bundled skills/llm-wiki/ directory.

    Works for both editable (pip install -e .) and non-editable installs.
    Raises RuntimeError with a clear message if the package is broken.
    """
    # Editable install: src/llm_wiki/cli/configure.py → ../../skills/llm-wiki
    candidate = Path(__file__).parent.parent / "skills" / "llm-wiki"
    if candidate.is_dir():
        return candidate
    # Non-editable install: use importlib.resources
    try:
        import importlib.resources
        ref = importlib.resources.files("llm_wiki") / "skills" / "llm-wiki"
        p = Path(str(ref))
        if p.is_dir():
            return p
    except Exception:
        pass
    raise RuntimeError(
        "Could not locate bundled skills directory.\n"
        "Run: pip install -e . to ensure the package is properly installed."
    )


_MCP_BANNER = (
    "> **MCP supersedes this skill.** If `wiki_search`, `wiki_read`, `wiki_query` tools are\n"
    "> available (llm-wiki MCP server connected), use those instead. This skill is retained\n"
    "> as conceptual reference only.\n"
)


def _parse_skill_name(md_path: Path) -> str | None:
    """Extract the 'name' field from YAML frontmatter. Returns None if absent."""
    content = md_path.read_text()
    if not content.startswith("---"):
        return None
    end = content.find("---", 3)
    if end < 0:
        return None
    try:
        meta = yaml.safe_load(content[3:end].strip())
        if not isinstance(meta, dict):
            return None
        name = meta.get("name")
        return name if isinstance(name, str) else None
    except yaml.YAMLError:
        return None


def _skill_dest(name: str, hermes_home: Path) -> Path:
    """Map a skill name (slash-separated) to its SKILL.md path under hermes_home/skills/."""
    parts = name.split("/")
    return hermes_home / "skills" / Path(*parts) / "SKILL.md"


def _update_manifest(manifest_path: Path, skill_name: str, content: bytes) -> None:
    """Upsert a skillname:md5 entry in the Hermes bundled manifest."""
    md5 = _hashlib.md5(content).hexdigest()
    entry = f"{skill_name}:{md5}"
    if manifest_path.exists():
        lines = [l for l in manifest_path.read_text().splitlines()
                 if l.strip() and not l.startswith(f"{skill_name}:")]
    else:
        lines = []
    lines.append(entry)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("\n".join(lines) + "\n")


def _patch_legacy_skill(skill_path: Path) -> bool:
    """Prepend MCP supersession banner after frontmatter. Returns True if patched."""
    content = skill_path.read_text()
    if _MCP_BANNER in content:
        return False
    if not content.startswith("---"):
        return False
    end = content.find("---", 3)
    if end < 0:
        return False
    insert_at = end + 3
    new_content = content[:insert_at] + "\n\n" + _MCP_BANNER + "\n" + content[insert_at:].lstrip("\n\r")
    skill_path.write_text(new_content)
    return True


def _install_skills_to_hermes(hermes_home: Path) -> int:
    """Copy all bundled skills to hermes_home/skills/. Returns count installed."""
    src_root = _skills_source()
    manifest_path = hermes_home / "skills" / ".bundled_manifest"
    count = 0
    for md_path in sorted(src_root.rglob("*.md")):
        name = _parse_skill_name(md_path)
        if not name:
            continue
        dest = _skill_dest(name, hermes_home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        content = md_path.read_bytes()
        dest.write_bytes(content)
        _update_manifest(manifest_path, name, content)
        count += 1
    return count


def _patch_legacy_skills(hermes_home: Path) -> int:
    """Patch all llm-wiki* skills in hermes_home/skills/research/ with MCP banner.
    Returns count of skills patched (0 if all already patched)."""
    research_dir = hermes_home / "skills" / "research"
    if not research_dir.is_dir():
        return 0
    patched = 0
    for skill_dir in research_dir.iterdir():
        if not skill_dir.name.startswith("llm-wiki"):
            continue
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists() and _patch_legacy_skill(skill_file):
            patched += 1
    return patched


def _merge_hermes_mcp(config_path: Path, vault_path: Path) -> None:
    """Merge llm-wiki MCP server entry into Hermes config.yaml."""
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
    config.setdefault("mcp_servers", {})["llm-wiki"] = {
        "command": "llm-wiki",
        "args": ["mcp"],
        "env": {"LLM_WIKI_VAULT": str(vault_path)},
        "timeout": 120,
        "connect_timeout": 30,
    }
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _setup_hermes() -> dict[str, Any] | None:
    """Interactive Hermes integration setup. Returns result dict or None on abort."""
    import os
    # ── Detect Hermes home ────────────────────────────────────────────────────
    default_hermes = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
    hermes_home_str = _prompt("Hermes home directory", str(default_hermes))
    hermes_home = Path(hermes_home_str).expanduser()
    if not hermes_home.is_dir():
        _err(f"Directory not found: {hermes_home}")
        _info("Is Hermes installed? Check https://github.com/NousResearch/hermes-agent")
        return None

    # ── Vault path ────────────────────────────────────────────────────────────
    env_vault = os.environ.get("LLM_WIKI_VAULT", "")
    if env_vault:
        default_vault = env_vault
        _info("  [from $LLM_WIKI_VAULT — override if stale]")
    else:
        default_vault = str(Path.home() / "wiki")
    vault_str = _prompt("Vault path", default_vault)
    vault_path = Path(vault_str).expanduser()

    # ── Vault initialisation ──────────────────────────────────────────────────
    vault_created = False
    if not vault_path.exists():
        _info(f"Creating vault at {vault_path}…")
        for sub in ("raw", "wiki", "schema", "inbox"):
            (vault_path / sub).mkdir(parents=True, exist_ok=True)
        vault_created = True

    if not (vault_path / "raw").is_dir():
        for sub in ("raw", "wiki", "schema", "inbox"):
            (vault_path / sub).mkdir(parents=True, exist_ok=True)
        vault_created = True

    if vault_created:
        _info("Initialising vault index…")
        from llm_wiki.vault import Vault
        try:
            Vault.scan(vault_path)
            _ok("Vault initialised")
        except Exception as e:
            _warn(f"Vault init warning: {e}")

    # ── Skill installation ────────────────────────────────────────────────────
    _info("Installing companion skills…")
    try:
        count = _install_skills_to_hermes(hermes_home)
        _ok(f"{count} skills installed")
    except RuntimeError as e:
        _err(str(e))
        return None

    # ── Legacy skill patching ─────────────────────────────────────────────────
    patched = _patch_legacy_skills(hermes_home)
    if patched:
        _ok(f"{patched} legacy skill(s) patched with MCP routing banner")

    # ── MCP registration ──────────────────────────────────────────────────────
    hermes_config = hermes_home / "config.yaml"
    if hermes_config.exists():
        _merge_hermes_mcp(hermes_config, vault_path)
        _ok("MCP server registered in Hermes config")
    else:
        _warn("Hermes config.yaml not found — skipping MCP registration")
        _info(f"Add manually under mcp_servers: in {hermes_config}")

    # ── Config check ──────────────────────────────────────────────────────────
    wiki_config = vault_path / "schema" / "config.yaml"
    config_missing = not wiki_config.exists() or wiki_config.stat().st_size == 0

    _ok("Hermes integration complete")
    _info("Restart Hermes to load the new skills.")

    return {
        "framework": "hermes",
        "vault_path": vault_path,
        "skills_installed": count,
        "config_missing": config_missing,
    }


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


def _pick_or_type(choices: list[str], label: str = "Select model:", default: int = 0) -> str:
    """Choose from list; last entry is always 'other (type manually)'."""
    idx = _choice(label, choices, default)
    if idx == len(choices) - 1:
        _info("LiteLLM format examples:")
        _info("  openai/gpt-4o                     (OpenAI)")
        _info("  anthropic/claude-haiku-4-5         (Anthropic)")
        _info("  openrouter/google/gemini-2.5-pro   (OpenRouter)")
        _info("  openai/my-local-model              (local endpoint)")
        print()
        return _prompt("Model name")
    return choices[idx]


def _setup_local(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Common base URLs:")
    _info("  ollama:       http://localhost:11434/v1")
    _info("  vllm:         http://localhost:8000/v1")
    _info("  LiteLLM proxy: http://localhost:4000/v1")
    print()
    api_base = _prompt("Base URL", "http://localhost:11434/v1")
    raw_model = _pick_or_type(_LOCAL_MODELS, label=label)
    # Strip any openai/ prefix the user might have typed; we'll add it
    model = raw_model.lstrip("openai/") if raw_model.startswith("openai/") else raw_model
    model_str = f"openai/{model}"
    api_key = _prompt("API key (press Enter to skip)")
    backend: dict[str, Any] = {"model": model_str, "api_base": api_base}
    if api_key:
        backend["api_key"] = api_key
    return "local", backend


def _setup_openai(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://platform.openai.com/api-keys")
    print()
    api_key = _prompt("OpenAI API key", password=True)
    model = _pick_or_type(_OPENAI_MODELS, label=label)
    return "openai", {"model": model, "api_key": api_key}


def _setup_anthropic(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://console.anthropic.com/")
    print()
    api_key = _prompt("Anthropic API key", password=True)
    model = _pick_or_type(_ANTHROPIC_MODELS, label=label)
    return "anthropic", {"model": model, "api_key": api_key}


def _setup_openrouter(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    _info("Get your key at: https://openrouter.ai/keys")
    _info("OpenRouter gives you access to 200+ models with a single key.")
    print()
    api_key = _prompt("OpenRouter API key", password=True)
    model_short = _pick_or_type(_OPENROUTER_MODELS, label=label)
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


def _setup_custom(label: str = "Choose your smart model:") -> tuple[str, dict[str, Any]]:
    # label accepted for API uniformity with other _setup_* funcs; unused here
    # (_setup_custom uses a free-text prompt, no model picker)
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


# ── Two-tier model setup ──────────────────────────────────────────────────────

# Roles that warrant a smart (expensive) model
_SMART_ROLES = ["query", "ingest"]
# Roles routed to the fast (cheap) model
_FAST_ROLES  = ["librarian", "adversary", "compliance", "commit", "talk_summary"]


def _setup_fast_backend(smart_name: str, smart_cfg: dict) -> tuple[str, dict] | None:
    """Prompt for a second, cheaper backend. Returns (name, cfg) or None to skip."""
    _info("Background tasks (librarian, adversary, compliance, commit, talk_summary)")
    _info("don't need your most powerful model. A cheaper/faster one saves cost.")
    print()
    if not _yes_no("Configure a separate fast/cheap model for background tasks?", default=True):
        return None

    print()
    _header("Fast / Cheap Model")
    provider_idx = _choice("Provider:", _PROVIDERS, default=0)
    _header(_PROVIDERS[provider_idx].split("  ")[0].strip())
    _, backend_cfg = _PROVIDER_SETUP[provider_idx](label="Choose your fast model:")
    return "fast", backend_cfg


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

    # ── Model tier framing ────────────────────────────────────────────────────
    _header("LLM Backends")
    _info("llm-wiki routes tasks across two model tiers:")
    _info("")
    _info("  Smart model — depth work: research queries, document ingestion,")
    _info("                adversarial fact-checking. Use your most capable model.")
    _info("")
    _info("  Fast model  — high-frequency background: librarian, compliance,")
    _info("                commit summaries. Throughput matters more than depth.")
    _info("")
    _info("You can use the same model for both — just skip the fast model step.")
    print()
    _info("Which provider do you want for your smart model?")
    print()

    provider_idx = _choice("Provider:", _PROVIDERS, default=0)
    _header(_PROVIDERS[provider_idx].split("  ")[0].strip())

    backend_name, backend_cfg = _PROVIDER_SETUP[provider_idx]()
    backends: dict[str, dict] = {"smart": backend_cfg}
    default_backend = "smart"

    _ok(f"Smart model configured  ({backend_cfg['model']})")

    # Capture OpenAI key for embeddings reuse
    openai_key = ""
    if backend_name == "openai":
        openai_key = backend_cfg.get("api_key", "")

    # ── Fast / cheap model ────────────────────────────────────────────────────
    print()
    _header("Fast / Cheap Model  (optional)")
    fast_result = _setup_fast_backend("smart", backend_cfg)
    role_overrides: dict[str, str] = {}
    if fast_result:
        fast_name, fast_cfg = fast_result
        backends[fast_name] = fast_cfg
        role_overrides = {role: "smart" for role in _SMART_ROLES}
        role_overrides.update({role: fast_name for role in _FAST_ROLES})
        _ok(f"Fast model configured  ({fast_cfg['model']})")

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

    has_fast = "fast" in backends
    features = [
        (f"Smart model  ({backend_cfg['model']})", True, None),
        (f"Fast model  ({backends['fast']['model']})" if has_fast
         else "Fast model  (using smart model for all tasks)",
         has_fast, "run wizard again to configure"),
        ("Embeddings / semantic search", embed_enabled,
         "disable with search.embeddings_enabled: false"),
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
