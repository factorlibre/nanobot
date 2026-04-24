"""Tests for per-user private context injection via ContextBuilder."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.context import ContextBuilder


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True)
    return ws


def _write_user(ws: Path, folder: str, filename: str, content: str) -> None:
    d = ws / "users" / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(content, encoding="utf-8")


def _write_map(ws: Path, mapping: dict[str, str]) -> None:
    users_dir = ws / "users"
    users_dir.mkdir(exist_ok=True)
    lines = [f'"{k}": {v}' for k, v in mapping.items()]
    (users_dir / "_map.yaml").write_text("\n".join(lines), encoding="utf-8")


def test_system_prompt_includes_user_profile_when_mapped(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    _write_map(ws, {"telegram:123": "contable"})
    _write_user(ws, "contable", "USER.md", "soy el contable, me llaman Juan")

    builder = ContextBuilder(ws)
    prompt = builder.build_system_prompt(channel="telegram", sender_id="123")

    assert "Private context: contable" in prompt
    assert "soy el contable" in prompt


def test_system_prompt_omits_user_profile_without_mapping(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    _write_user(ws, "contable", "USER.md", "NOT_REACHABLE")

    builder = ContextBuilder(ws)
    prompt = builder.build_system_prompt(channel="telegram", sender_id="999")

    assert "Private context" not in prompt
    assert "NOT_REACHABLE" not in prompt


def test_other_employees_profile_not_leaked(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    _write_map(ws, {
        "telegram:111": "contable",
        "telegram:222": "almacen",
    })
    _write_user(ws, "contable", "USER.md", "SECRET_CONTABLE")
    _write_user(ws, "almacen", "USER.md", "SECRET_ALMACEN")

    builder = ContextBuilder(ws)
    prompt_contable = builder.build_system_prompt(channel="telegram", sender_id="111")
    prompt_almacen = builder.build_system_prompt(channel="telegram", sender_id="222")

    assert "SECRET_CONTABLE" in prompt_contable
    assert "SECRET_ALMACEN" not in prompt_contable
    assert "SECRET_ALMACEN" in prompt_almacen
    assert "SECRET_CONTABLE" not in prompt_almacen


def test_runtime_context_includes_sender(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    builder = ContextBuilder(ws)

    messages = builder.build_messages(
        history=[],
        current_message="hola",
        channel="telegram",
        chat_id="123",
        sender_id="123|juan",
    )

    user_content = messages[-1]["content"]
    text = user_content if isinstance(user_content, str) else user_content[0].get("text", "")
    assert "Sender: 123|juan" in text
