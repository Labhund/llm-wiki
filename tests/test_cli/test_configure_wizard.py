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
