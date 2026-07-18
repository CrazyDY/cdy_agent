import pytest

from cdy_agent.conversation import Conversation, Message


def test_conversation_appends_normalized_messages_in_order() -> None:
    conversation = Conversation()

    user_message = conversation.append("user", "  Hello  ")
    assistant_message = conversation.append("assistant", " Hi there. ")

    assert user_message == Message(role="user", content="Hello")
    assert assistant_message == Message(
        role="assistant",
        content="Hi there.",
    )
    assert conversation.history == (user_message, assistant_message)


def test_history_is_an_immutable_snapshot() -> None:
    conversation = Conversation()
    conversation.append("user", "Hello")

    history = conversation.history
    conversation.append("assistant", "Hi")

    assert history == (Message(role="user", content="Hello"),)
    assert conversation.history != history


@pytest.mark.parametrize("content", ["", "   "])
def test_conversation_rejects_blank_content(content: str) -> None:
    conversation = Conversation()

    with pytest.raises(ValueError, match="Message must not be empty"):
        conversation.append("user", content)

    assert conversation.history == ()


def test_conversation_rejects_unsupported_role() -> None:
    conversation = Conversation()

    with pytest.raises(ValueError, match="Unsupported message role"):
        conversation.append("system", "Instructions")  # type: ignore[arg-type]

    assert conversation.history == ()
