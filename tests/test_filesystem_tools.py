from pathlib import Path

import pytest

from cdy_agent.tools.filesystem import ReadFileTool, resolve_workspace


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
