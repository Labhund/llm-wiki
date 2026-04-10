"""Tests for configure wizard helper functions."""
from pathlib import Path


def test_skills_source_returns_directory():
    """Skills must be locatable from the installed package."""
    from llm_wiki.cli.configure import _skills_source
    src = _skills_source()
    assert src.is_dir(), f"Skills dir not found at {src}"
    md_files = list(src.rglob("*.md"))
    assert len(md_files) >= 5, f"Expected at least 5 skill files, found {len(md_files)}"
