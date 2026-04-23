"""Tests for UserProfileLoader: per-sender private context loading."""

from pathlib import Path

import pytest

from nanobot.agent.user_profiles import UserProfileLoader


def _write_map(workspace: Path, mapping: dict[str, str]) -> None:
    users_dir = workspace / "users"
    users_dir.mkdir(exist_ok=True)
    lines = [f'"{k}": {v}' for k, v in mapping.items()]
    (users_dir / "_map.yaml").write_text("\n".join(lines), encoding="utf-8")


def _write_profile(workspace: Path, folder: str, filename: str, content: str) -> None:
    d = workspace / "users" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(content, encoding="utf-8")


class TestResolve:
    def test_resolve_hits_mapping(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:123|juan": "contable"})
        loader = UserProfileLoader(tmp_path)
        assert loader.resolve("telegram", "123|juan") == "contable"

    def test_resolve_missing_map_returns_none(self, tmp_path: Path) -> None:
        loader = UserProfileLoader(tmp_path)
        assert loader.resolve("telegram", "123") is None

    def test_resolve_unmapped_sender_returns_none(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:123": "contable"})
        loader = UserProfileLoader(tmp_path)
        assert loader.resolve("telegram", "999") is None

    def test_resolve_requires_channel_and_sender(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:123": "contable"})
        loader = UserProfileLoader(tmp_path)
        assert loader.resolve(None, "123") is None
        assert loader.resolve("telegram", None) is None

    def test_resolve_cross_channel_same_folder(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {
            "telegram:123|juan": "contable",
            "slack:U045": "contable",
        })
        loader = UserProfileLoader(tmp_path)
        assert loader.resolve("telegram", "123|juan") == "contable"
        assert loader.resolve("slack", "U045") == "contable"


class TestLoad:
    def test_load_concatenates_all_md_files(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:123": "contable"})
        _write_profile(tmp_path, "contable", "USER.md", "# Cuentas\npreferencias")
        _write_profile(tmp_path, "contable", "NOTES.md", "# Notas")
        loader = UserProfileLoader(tmp_path)
        out = loader.load("telegram", "123")
        assert "Private context: contable" in out
        assert "USER.md" in out and "NOTES.md" in out
        assert "preferencias" in out

    def test_load_sorts_files_alphabetically(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:1": "emp"})
        _write_profile(tmp_path, "emp", "z.md", "last")
        _write_profile(tmp_path, "emp", "a.md", "first")
        loader = UserProfileLoader(tmp_path)
        out = loader.load("telegram", "1")
        assert out.index("first") < out.index("last")

    def test_load_no_mapping_returns_empty(self, tmp_path: Path) -> None:
        loader = UserProfileLoader(tmp_path)
        assert loader.load("telegram", "123") == ""

    def test_load_missing_folder_returns_empty(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:1": "ghost"})
        loader = UserProfileLoader(tmp_path)
        assert loader.load("telegram", "1") == ""

    def test_load_empty_folder_returns_empty(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:1": "empty"})
        (tmp_path / "users" / "empty").mkdir(parents=True)
        loader = UserProfileLoader(tmp_path)
        assert loader.load("telegram", "1") == ""

    def test_load_skips_non_md_files(self, tmp_path: Path) -> None:
        _write_map(tmp_path, {"telegram:1": "emp"})
        _write_profile(tmp_path, "emp", "USER.md", "yes")
        (tmp_path / "users" / "emp" / "ignore.txt").write_text("no", encoding="utf-8")
        loader = UserProfileLoader(tmp_path)
        out = loader.load("telegram", "1")
        assert "yes" in out and "no" not in out

    def test_load_ignores_invalid_yaml(self, tmp_path: Path) -> None:
        users_dir = tmp_path / "users"
        users_dir.mkdir()
        (users_dir / "_map.yaml").write_text(": : :\nnot valid", encoding="utf-8")
        loader = UserProfileLoader(tmp_path)
        assert loader.load("telegram", "1") == ""
