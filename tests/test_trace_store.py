import json
from pathlib import Path

import pytest

from cdy_agent.observability.store import (
    TraceNotFoundError,
    TraceStore,
    TraceStoreError,
)
from test_observability_models import sample_trace


def test_empty_read_does_not_create_workspace_data(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    assert store.list_traces() == ()
    assert not (tmp_path / ".cdy-agent").exists()


def test_append_writes_one_json_line_and_lists_newest_first(
    tmp_path: Path,
) -> None:
    first = sample_trace()
    second_payload = first.to_dict()
    second_payload["trace_id"] = "0cebd5c2-7d4c-4655-a997-f31e05eb74a5"
    second_payload["started_at"] = "2026-07-20T09:30:00.000000Z"
    second = type(first).from_dict(second_payload)
    store = TraceStore(tmp_path)
    store.append(first)
    store.append(second)

    lines = (tmp_path / ".cdy-agent" / "traces.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 2
    assert [record.trace_id for record in store.list_traces()] == [
        second.trace_id,
        first.trace_id,
    ]
    assert store.get(first.trace_id) == first


def test_list_sorts_mixed_timestamp_precision_chronologically(
    tmp_path: Path,
) -> None:
    earlier = sample_trace()
    later_payload = earlier.to_dict()
    later_payload["trace_id"] = "0cebd5c2-7d4c-4655-a997-f31e05eb74a5"
    later_payload["started_at"] = "2026-07-20T08:30:00.100000Z"
    later = type(earlier).from_dict(later_payload)
    earlier_payload = earlier.to_dict()
    earlier_payload["started_at"] = "2026-07-20T08:30:00Z"
    earlier = type(earlier).from_dict(earlier_payload)
    store = TraceStore(tmp_path)
    store.append(later)
    store.append(earlier)

    assert [record.trace_id for record in store.list_traces()] == [
        later.trace_id,
        earlier.trace_id,
    ]


@pytest.mark.parametrize(
    ("corrupt_line", "line_number"),
    [
        ("not-json", 2),
        ("", 2),
        (json.dumps({**sample_trace().to_dict(), "schema_version": 2}), 2),
    ],
)
def test_corrupt_line_reports_line_number(
    tmp_path: Path, corrupt_line: str, line_number: int
) -> None:
    path = tmp_path / ".cdy-agent" / "traces.jsonl"
    path.parent.mkdir()
    path.write_text(
        json.dumps(sample_trace().to_dict()) + "\n" + corrupt_line + "\n",
        encoding="utf-8",
    )

    with pytest.raises(TraceStoreError, match=rf"line {line_number}"):
        TraceStore(tmp_path).list_traces()


def test_invalid_utf8_reports_safe_line_number(tmp_path: Path) -> None:
    path = tmp_path / ".cdy-agent" / "traces.jsonl"
    path.parent.mkdir()
    valid_line = json.dumps(sample_trace().to_dict()).encode("utf-8")
    path.write_bytes(valid_line + b"\n\xffprivate-bytes\n")

    with pytest.raises(
        TraceStoreError, match=r"^Invalid trace data on line 2\.$"
    ):
        TraceStore(tmp_path).list_traces()


def test_get_requires_complete_existing_uuid(tmp_path: Path) -> None:
    store = TraceStore(tmp_path)
    with pytest.raises(TraceStoreError, match="complete UUID"):
        store.get("52c809c6")
    with pytest.raises(TraceStoreError, match="complete UUID"):
        store.get("52C809C6-6E55-4FF1-9220-E4F90A4F6774")
    with pytest.raises(TraceNotFoundError, match="not found"):
        store.get("52c809c6-6e55-4ff1-9220-e4f90a4f6774")


def test_read_and_write_errors_use_safe_generic_messages(tmp_path: Path) -> None:
    data_path = tmp_path / ".cdy-agent" / "traces.jsonl"
    data_path.mkdir(parents=True)

    with pytest.raises(TraceStoreError, match=r"^Could not read trace data\.$"):
        TraceStore(tmp_path).list_traces()

    data_path.rmdir()
    (tmp_path / ".cdy-agent").rmdir()
    (tmp_path / ".cdy-agent").write_text("not a directory", encoding="utf-8")
    with pytest.raises(TraceStoreError, match=r"^Could not write trace data\.$"):
        TraceStore(tmp_path).append(sample_trace())
