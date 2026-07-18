import json
from pathlib import Path

from cdy_agent.tools.personal_store import PersonalStore


NOTE = {
    "id": "00000000-0000-4000-8000-000000000001",
    "title": "Plan",
    "content": "Ship phase five",
    "created_at": "2026-07-18T02:00:00Z",
}
TODO = {
    "id": "00000000-0000-4000-8000-000000000002",
    "text": "Write tests",
    "completed": False,
    "created_at": "2026-07-18T02:01:00Z",
    "completed_at": None,
}


def test_empty_store_reads_without_creating_data_directory(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path)

    assert store.load_notes().data == []
    assert store.load_todos().data == []
    assert not (tmp_path / ".cdy-agent").exists()


def test_store_persists_versioned_note_and_todo_documents(tmp_path: Path) -> None:
    store = PersonalStore(tmp_path)

    assert store.save_notes([NOTE]).ok
    assert store.save_todos([TODO]).ok
    assert PersonalStore(tmp_path).load_notes().data == [NOTE]
    assert PersonalStore(tmp_path).load_todos().data == [TODO]
    assert json.loads((tmp_path / ".cdy-agent/notes.json").read_text()) == {
        "version": 1,
        "items": [NOTE],
    }
