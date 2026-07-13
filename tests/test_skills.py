import asyncio

from cdy_agent.skills.notes import tools as notes_tools
from cdy_agent.skills.todo import tools as todo_tools


def call_tool(tool, **kwargs):
    return asyncio.run(tool.on_invoke_tool(None, kwargs))


def test_todo_skill_lifecycle():
    todo_tools.reset_todos()

    created = call_tool(todo_tools.add_todo, title="写项目计划", due_date="2026-07-15", priority="high")
    assert created["id"] == 1
    assert created["done"] is False

    visible = call_tool(todo_tools.list_todos)
    assert [item["title"] for item in visible] == ["写项目计划"]

    completed = call_tool(todo_tools.complete_todo, todo_id=1)
    assert completed["done"] is True

    assert call_tool(todo_tools.list_todos) == []


def test_notes_skill_create_and_search():
    notes_tools.reset_notes()

    created = call_tool(
        notes_tools.create_note,
        title="Agent MVP",
        content="先实现 todo 和 notes skills",
        tags=["agent", "mvp"],
    )
    assert created["id"] == 1

    matches = call_tool(notes_tools.search_notes, query="mvp")
    assert len(matches) == 1
    assert matches[0]["title"] == "Agent MVP"
