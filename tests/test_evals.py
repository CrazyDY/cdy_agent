from collections.abc import Sequence
from pathlib import Path

import pytest

from cdy_agent.conversation import Message
from cdy_agent.evals import EvalFileError, run_eval_file


class FakeAgent:
    def __init__(self, replies: Sequence[str]) -> None:
        self.replies = iter(replies)
        self.calls: list[tuple[Message, ...]] = []

    def run(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        return next(self.replies)


def test_eval_file_reports_passed_and_failed_cases(tmp_path: Path) -> None:
    eval_file = tmp_path / "cases.yaml"
    eval_file.write_text(
        "\n".join(
            [
                "cases:",
                "  - name: greeting",
                "    prompt: Say hello",
                "    expect:",
                "      exact: Hello",
                "  - name: todo help",
                "    prompt: How do todos work?",
                "    expect:",
                "      contains:",
                "        - create",
                "        - complete",
                "",
            ]
        ),
        encoding="utf-8",
    )
    agent = FakeAgent(("Hello", "You can create todo items."))

    report = run_eval_file(eval_file, agent)

    assert report.total == 2
    assert report.passed == 1
    assert report.failed == 1
    assert [result.name for result in report.results] == ["greeting", "todo help"]
    assert report.results[0].passed is True
    assert report.results[1].passed is False
    assert "complete" in report.results[1].message
    assert agent.calls == [
        (Message("user", "Say hello"),),
        (Message("user", "How do todos work?"),),
    ]


def test_eval_file_accepts_json_cases(tmp_path: Path) -> None:
    eval_file = tmp_path / "cases.json"
    eval_file.write_text(
        '{"cases":[{"name":"json","prompt":"Say hi","expect":{"contains":"hi"}}]}',
        encoding="utf-8",
    )

    report = run_eval_file(eval_file, FakeAgent(("well, hi",)))

    assert report.passed == 1
    assert report.failed == 0


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("{}", "cases"),
        ("cases:\n  - prompt: Missing name\n    expect:\n      exact: x\n", "name"),
        ("cases:\n  - name: bad\n    expect:\n      exact: x\n", "prompt"),
        ("cases:\n  - name: bad\n    prompt: hi\n", "expect"),
    ],
)
def test_eval_file_rejects_invalid_case_shapes(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    eval_file = tmp_path / "cases.yaml"
    eval_file.write_text(body, encoding="utf-8")

    with pytest.raises(EvalFileError, match=message):
        run_eval_file(eval_file, FakeAgent(("unused",)))
