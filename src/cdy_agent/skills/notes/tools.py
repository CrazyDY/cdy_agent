"""Notes skill tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import count

from cdy_agent.openai_sdk import function_tool


@dataclass
class Note:
    id: int
    title: str
    content: str
    tags: list[str] = field(default_factory=list)


_note_ids = count(1)
_notes: list[Note] = []


def reset_notes() -> None:
    """Clear the in-memory notes store. Intended for tests and local demos."""

    global _note_ids
    _note_ids = count(1)
    _notes.clear()


@function_tool
def create_note(title: str, content: str, tags: list[str] | None = None) -> dict:
    """Create a note.

    Args:
        title: Note title.
        content: Note body.
        tags: Optional tags for later lookup.
    """

    note = Note(id=next(_note_ids), title=title, content=content, tags=tags or [])
    _notes.append(note)
    return asdict(note)


@function_tool
def list_notes() -> list[dict]:
    """List all notes."""

    return [asdict(note) for note in _notes]


@function_tool
def search_notes(query: str) -> list[dict]:
    """Search notes by title, content, or tag.

    Args:
        query: Search keyword.
    """

    normalized_query = query.casefold()
    return [
        asdict(note)
        for note in _notes
        if normalized_query in note.title.casefold()
        or normalized_query in note.content.casefold()
        or any(normalized_query in tag.casefold() for tag in note.tags)
    ]
