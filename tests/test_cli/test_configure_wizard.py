"""Tests for configure wizard helper functions."""
from pathlib import Path


def test_skills_source_returns_directory():
    """Skills must be locatable from the installed package."""
    from llm_wiki.cli.configure import _skills_source
    src = _skills_source()
    assert src.is_dir(), f"Skills dir not found at {src}"
    md_files = list(src.rglob("*.md"))
    assert len(md_files) >= 5, f"Expected at least 5 skill files, found {len(md_files)}"


import hashlib
from pathlib import Path


def test_parse_skill_name_extracts_name(tmp_path):
    from llm_wiki.cli.configure import _parse_skill_name
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: llm-wiki/research\ndescription: test\n---\n\n# Body\n")
    assert _parse_skill_name(skill) == "llm-wiki/research"


def test_parse_skill_name_returns_none_for_missing(tmp_path):
    from llm_wiki.cli.configure import _parse_skill_name
    skill = tmp_path / "SKILL.md"
    skill.write_text("# No frontmatter\n")
    assert _parse_skill_name(skill) is None


def test_skill_dest_maps_slash_to_path(tmp_path):
    from llm_wiki.cli.configure import _skill_dest
    hermes = tmp_path / ".hermes"
    assert _skill_dest("llm-wiki/research", hermes) == hermes / "skills" / "llm-wiki" / "research" / "SKILL.md"
    assert _skill_dest("llm-wiki", hermes) == hermes / "skills" / "llm-wiki" / "SKILL.md"


def test_update_manifest_writes_entry(tmp_path):
    from llm_wiki.cli.configure import _update_manifest
    manifest = tmp_path / ".bundled_manifest"
    content = b"hello world"
    _update_manifest(manifest, "llm-wiki/research", content)
    expected_hash = hashlib.md5(content).hexdigest()
    lines = manifest.read_text().splitlines()
    assert f"llm-wiki/research:{expected_hash}" in lines


def test_update_manifest_replaces_existing_entry(tmp_path):
    from llm_wiki.cli.configure import _update_manifest
    manifest = tmp_path / ".bundled_manifest"
    manifest.write_text("llm-wiki/research:oldhash\nother:abc\n")
    _update_manifest(manifest, "llm-wiki/research", b"new content")
    lines = manifest.read_text().splitlines()
    assert not any("oldhash" in l for l in lines)
    assert any("llm-wiki/research:" in l for l in lines)
    assert "other:abc" in lines


def test_patch_legacy_skill_adds_banner(tmp_path):
    from llm_wiki.cli.configure import _patch_legacy_skill, _MCP_BANNER
    skill = tmp_path / "SKILL.md"
    skill.write_text("---\nname: llm-wiki\n---\n\n# Body text\n")
    patched = _patch_legacy_skill(skill)
    assert patched is True
    content = skill.read_text()
    assert _MCP_BANNER in content
    # Banner must be after frontmatter
    assert content.index(_MCP_BANNER) > content.index("---\n\n")


def test_patch_legacy_skill_idempotent(tmp_path):
    from llm_wiki.cli.configure import _patch_legacy_skill, _MCP_BANNER
    skill = tmp_path / "SKILL.md"
    skill.write_text(f"---\nname: llm-wiki\n---\n\n{_MCP_BANNER}\n# Body\n")
    patched = _patch_legacy_skill(skill)
    assert patched is False  # already patched


def test_install_skills_to_hermes(tmp_path):
    """Skills are copied and manifest is updated."""
    from llm_wiki.cli.configure import _install_skills_to_hermes
    hermes_home = tmp_path / ".hermes"
    (hermes_home / "skills").mkdir(parents=True)
    count = _install_skills_to_hermes(hermes_home)
    assert count > 0
    # At minimum the index skill should be installed
    assert (hermes_home / "skills" / "llm-wiki" / "SKILL.md").exists()
    assert (hermes_home / "skills" / ".bundled_manifest").exists()


def test_patch_legacy_skills_in_hermes(tmp_path):
    """Legacy llm-wiki* skills in research/ get the MCP banner."""
    from llm_wiki.cli.configure import _patch_legacy_skills, _MCP_BANNER
    hermes_home = tmp_path / ".hermes"
    legacy_dir = hermes_home / "skills" / "research" / "llm-wiki-legacy"
    legacy_dir.mkdir(parents=True)
    skill_file = legacy_dir / "SKILL.md"
    skill_file.write_text("---\nname: llm-wiki-legacy\n---\n\n# Old skill\n")
    patched = _patch_legacy_skills(hermes_home)
    assert patched == 1
    assert _MCP_BANNER in skill_file.read_text()


def test_merge_hermes_mcp_config(tmp_path):
    """MCP server block is merged into Hermes config without losing other keys."""
    import yaml
    from llm_wiki.cli.configure import _merge_hermes_mcp
    config_path = tmp_path / "config.yaml"
    config_path.write_text("agent:\n  max_turns: 90\nmcp_servers:\n  other-tool:\n    command: foo\n")
    vault_path = Path("/home/user/wiki")
    _merge_hermes_mcp(config_path, vault_path)
    content = yaml.safe_load(config_path.read_text())
    assert "llm-wiki" in content["mcp_servers"]
    assert content["mcp_servers"]["llm-wiki"]["command"] == "llm-wiki"
    assert content["mcp_servers"]["llm-wiki"]["env"]["LLM_WIKI_VAULT"] == str(vault_path)
    assert "other-tool" in content["mcp_servers"]   # existing entry preserved
    assert content["agent"]["max_turns"] == 90       # unrelated key preserved
    assert content["mcp_servers"]["llm-wiki"]["args"] == ["mcp"]
    assert content["mcp_servers"]["llm-wiki"]["timeout"] == 120
    assert content["mcp_servers"]["llm-wiki"]["connect_timeout"] == 30
