"""Tests for small pure helpers: text-protocol tools, markdown splitting, mood, history."""
from capri.character.mood import MoodEngine
from capri.llm.agent import trim_history
from capri.llm.base import Message, ToolCall
from capri.llm.providers.anthropic import AnthropicBackend
from capri.llm.text_tools import StreamFilter, parse_tool_calls
from capri.ui.markdown import split_segments


def test_stream_filter_hides_tool_blocks() -> None:
    flt = StreamFilter()
    shown = "".join(flt.feed(chunk) for chunk in
                    ["Let me look. <to", "ol_call>{\"name\": \"web_search\", ",
                     "\"arguments\": {\"query\": \"x\"}}</tool_call>"])
    shown += flt.finish()
    assert shown == "Let me look. "
    clean, calls = parse_tool_calls(flt.full_text)
    assert clean == "Let me look." and calls[0].name == "web_search"
    assert calls[0].arguments == {"query": "x"}


def test_stream_filter_passes_plain_text() -> None:
    flt = StreamFilter()
    assert flt.feed("a < b and <tool") + flt.feed("box>") + flt.finish() == "a < b and <toolbox>"


def test_split_segments_handles_open_fence() -> None:
    parts = split_segments("Intro\n```python\nprint(1)\n```\nOutro\n```ps1\nGet-Item")
    assert [p[0] for p in parts] == ["text", "code", "text", "code"]
    assert parts[1] == ("code", "print(1)", "python") and parts[3][2] == "ps1"


def test_trim_history_cuts_at_user_boundary() -> None:
    history = []
    for i in range(30):
        history += [Message("user", f"q{i}"),
                    Message("assistant", "", tool_calls=[ToolCall(f"c{i}", "t", {})]),
                    Message("tool", "r", tool_call_id=f"c{i}", name="t"),
                    Message("assistant", f"a{i}")]
    trimmed = trim_history(history, limit=10)
    assert trimmed[0].role == "user" and len(trimmed) <= 10


def test_anthropic_conversion_merges_tool_results() -> None:
    system, messages = AnthropicBackend._convert([
        Message("system", "sys"), Message("user", "hi"),
        Message("assistant", "", tool_calls=[ToolCall("a", "t", {}), ToolCall("b", "t", {})]),
        Message("tool", "r1", tool_call_id="a", name="t"),
        Message("tool", "r2", tool_call_id="b", name="t"),
        Message("assistant", "done")])
    assert system == "sys"
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [b["type"] for b in messages[2]["content"]] == ["tool_result", "tool_result"]


def test_mood_rules() -> None:
    mood = MoodEngine({"energy": 0.9, "engagement": 0.3})
    assert mood.mood == "content"
    for _ in range(5):
        mood.note_interaction("message")
    assert mood.mood == "excited"
    mood.tick(6 * 3600, hour=14, session_minutes=30)
    assert mood.mood == "bored"
    mood.tick(3 * 3600, hour=3, session_minutes=30)
    assert mood.mood == "tired"
