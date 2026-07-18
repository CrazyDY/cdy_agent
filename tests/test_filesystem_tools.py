from pathlib import Path

import pytest

from cdy_agent.tools.filesystem import ReadFileTool, WriteFileTool, resolve_workspace


def test_resolve_workspace_requires_directory(tmp_path: Path) -> None:
    assert resolve_workspace(tmp_path) == tmp_path.resolve()
    with pytest.raises(ValueError, match="workspace"):
        resolve_workspace(tmp_path / "missing")


def test_read_file_reads_utf8_and_truncates(tmp_path: Path) -> None:
    (tmp_path / "short.txt").write_text("你好", encoding="utf-8")
    (tmp_path / "large.txt").write_bytes(b"a" * (1024 * 1024 + 1))
    tool = ReadFileTool(tmp_path)
    assert tool.execute({"path": "short.txt"}).data == {
        "path": str((tmp_path / "short.txt").resolve()),
        "content": "你好",
        "truncated": False,
    }
    large = tool.execute({"path": "large.txt"})
    assert large.ok is True
    assert large.data["truncated"] is True
    assert len(large.data["content"]) == 1024 * 1024


def test_read_file_drops_incomplete_utf8_code_point_when_truncated(
    tmp_path: Path,
) -> None:
    prefix = b"a" * (1024 * 1024 - 1)
    (tmp_path / "large.txt").write_bytes(prefix + "你".encode() + b"z")

    result = ReadFileTool(tmp_path).execute({"path": "large.txt"})

    assert result.ok is True
    assert result.data["content"] == prefix.decode()
    assert result.data["truncated"] is True
    assert len(result.data["content"].encode()) <= 1024 * 1024


@pytest.mark.parametrize("arguments", [{}, {"path": 1}, {"path": "a", "extra": 1}])
def test_read_file_rejects_invalid_arguments(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    assert ReadFileTool(tmp_path).execute(arguments).code == "invalid_arguments"


def test_read_file_rejects_escape_directory_and_binary(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    (tmp_path / "binary").write_bytes(b"\xff")
    tool = ReadFileTool(tmp_path)
    assert tool.execute({"path": "../outside.txt"}).code == "path_outside_workspace"
    assert tool.execute({"path": "folder"}).code == "not_a_file"
    assert tool.execute({"path": "binary"}).code == "unsupported_encoding"


def test_read_file_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError):
        pytest.skip("platform cannot create symlinks")

    assert ReadFileTool(tmp_path).execute({"path": "link.txt"}).code == (
        "path_outside_workspace"
    )


def test_read_file_maps_resolution_error_to_file_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tool = ReadFileTool(tmp_path)

    def fail_resolution(path: Path) -> Path:
        raise OSError("resolution failed")

    monkeypatch.setattr(Path, "resolve", fail_resolution)

    result = tool.execute({"path": "file.txt"})

    assert result.code == "file_error"


def test_write_file_creates_and_explicitly_overwrites(tmp_path: Path) -> None:
    tool = WriteFileTool(tmp_path)

    created = tool.execute({"path": "note.txt", "content": "hello"})
    assert created.ok is True
    assert created.data == {
        "path": str((tmp_path / "note.txt").resolve()),
        "bytes": 5,
        "overwritten": False,
    }
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"

    denied = tool.execute({"path": "note.txt", "content": "new"})
    assert denied.code == "overwrite_not_allowed"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello"

    replaced = tool.execute(
        {"path": "note.txt", "content": "new", "overwrite": True}
    )
    assert replaced.ok is True
    assert replaced.data["overwritten"] is True
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "new"


def test_write_file_requires_existing_parent_and_stays_in_workspace(
    tmp_path: Path,
) -> None:
    tool = WriteFileTool(tmp_path)

    assert tool.execute({"path": "missing/note.txt", "content": "x"}).code == (
        "parent_not_found"
    )
    outside_name = f"{tmp_path.name}-write-outside.txt"
    assert tool.execute({"path": f"../{outside_name}", "content": "x"}).code == (
        "path_outside_workspace"
    )
    assert not (tmp_path.parent / outside_name).exists()


def test_write_file_rejects_directory_target(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()

    result = WriteFileTool(tmp_path).execute(
        {"path": "folder", "content": "x", "overwrite": True}
    )

    assert result.code == "not_a_file"


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": "note.txt"},
        {"content": "x"},
        {"path": 1, "content": "x"},
        {"path": "note.txt", "content": 1},
        {"path": "note.txt", "content": "x", "overwrite": "yes"},
        {"path": "note.txt", "content": "x", "extra": True},
    ],
)
def test_write_file_rejects_invalid_arguments_before_mutation(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    target = tmp_path / "note.txt"
    target.write_text("original", encoding="utf-8")

    result = WriteFileTool(tmp_path).execute(arguments)

    assert result.code == "invalid_arguments"
    assert target.read_text(encoding="utf-8") == "original"


def test_write_description_identifies_create_or_overwrite(tmp_path: Path) -> None:
    tool = WriteFileTool(tmp_path)
    create = tool.confirmation_description({"path": "note.txt", "content": "你好"})
    assert "create" in create.lower()
    assert str((tmp_path / "note.txt").resolve()) in create
    assert "6 bytes" in create

    (tmp_path / "note.txt").write_text("old", encoding="utf-8")
    overwrite = tool.confirmation_description(
        {
            "path": str((tmp_path / "note.txt").resolve()),
            "content": "新",
            "overwrite": True,
        }
    )
    assert "overwrite" in overwrite.lower()
    assert str((tmp_path / "note.txt").resolve()) in overwrite
    assert "3 bytes" in overwrite


def test_write_description_is_pure(tmp_path: Path) -> None:
    target = tmp_path / "note.txt"

    WriteFileTool(tmp_path).confirmation_description(
        {"path": "note.txt", "content": "content"}
    )

    assert not target.exists()


def test_write_symlinks_stay_in_workspace_or_are_rejected(tmp_path: Path) -> None:
    inside = tmp_path / "inside"
    inside.mkdir()
    parent_link = tmp_path / "parent-link"
    file_link = tmp_path / "file-link"
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    escape = tmp_path / "escape"
    try:
        parent_link.symlink_to(inside, target_is_directory=True)
        file_link.symlink_to(inside / "target.txt")
        escape.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("platform cannot create symlinks")
    tool = WriteFileTool(tmp_path)
    assert tool.execute({"path": "parent-link/new.txt", "content": "ok"}).ok
    assert tool.execute({"path": "file-link", "content": "one"}).ok
    assert tool.execute({"path": "file-link", "content": "two", "overwrite": True}).ok
    assert tool.execute({"path": "escape/new.txt", "content": "no"}).code == "path_outside_workspace"


def test_registry_rejects_bad_writes_before_confirmation(tmp_path: Path) -> None:
    from cdy_agent.tools.base import ToolCall
    from cdy_agent.tools.registry import ToolRegistry

    existing = tmp_path / "existing.txt"
    existing.write_text("old")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    calls: list[object] = []
    registry = ToolRegistry([WriteFileTool(tmp_path)])
    for payload, code in [
        ('{"path":1,"content":"x"}', "invalid_arguments"),
        ('{"path":"existing.txt","content":"x"}', "overwrite_not_allowed"),
        (f'{{"path":"{outside}","content":"x"}}', "path_outside_workspace"),
    ]:
        result = registry.execute(
            ToolCall("1", "write_file", payload),
            lambda request: calls.append(request) or True,
        )
        assert result.code == code
    assert calls == []


def test_registry_confirms_real_write_create_and_overwrite(tmp_path: Path) -> None:
    from cdy_agent.tools.base import ToolCall
    from cdy_agent.tools.registry import ToolRegistry

    target = tmp_path / "note.txt"
    requests: list[object] = []
    registry = ToolRegistry([WriteFileTool(tmp_path)])
    created = registry.execute(
        ToolCall("1", "write_file", '{"path":"note.txt","content":"old"}'),
        lambda request: requests.append(request) or True,
    )
    assert created.ok and len(requests) == 1
    denied = registry.execute(
        ToolCall(
            "2", "write_file",
            '{"path":"note.txt","content":"denied","overwrite":true}',
        ),
        lambda request: requests.append(request) or False,
    )
    assert denied.code == "approval_denied"
    assert target.read_text() == "old"
    approved = registry.execute(
        ToolCall(
            "3", "write_file",
            '{"path":"note.txt","content":"new","overwrite":true}',
        ),
        lambda request: requests.append(request) or True,
    )
    assert approved.ok
    assert target.read_text() == "new"


def test_write_file_maps_oserror_to_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(path: Path, content: str, encoding: str) -> int:
        raise OSError("write failed")

    monkeypatch.setattr(Path, "write_text", fail_write)

    result = WriteFileTool(tmp_path).execute(
        {"path": "note.txt", "content": "content"}
    )

    assert result.code == "file_error"
    assert "write failed" in result.message
