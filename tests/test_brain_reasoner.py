from src.brain.reasoner import LLMReasoner, build_prompt, parse_proposals
from src.brain.types import DeliberationContext, Notable, ProposalKind


def _ctx(notables=None):
    return DeliberationContext(
        notables=notables or [Notable("resources", "disk.C:.free_gb", "low", "warn")],
        memory_context="User prefers dark mode.",
        recent_turns=[{"role": "user", "text": "hi"}],
        tool_catalogue=[{"name": "system_info", "description": "reads system state"}],
        suppressions=["disk space"],
        autonomy="full",
    )


def test_parse_clean_array():
    raw = '[{"kind": "speak", "message": "Disk C is low.", "urgency": "normal"}]'
    proposals = parse_proposals(raw)
    assert len(proposals) == 1
    assert proposals[0].kind is ProposalKind.SPEAK
    assert proposals[0].message == "Disk C is low."


def test_parse_fenced_json():
    raw = '```json\n[{"kind": "act", "message": "check disks", "tool": "system_info", "args": {"query": "disks"}}]\n```'
    proposals = parse_proposals(raw)
    assert len(proposals) == 1
    assert proposals[0].tool == "system_info"
    assert proposals[0].args == {"query": "disks"}


def test_parse_prose_wrapped_array():
    raw = 'Sure! Here is my decision:\n[{"kind":"speak","message":"Reboot pending."}]\nHope that helps.'
    proposals = parse_proposals(raw)
    assert len(proposals) == 1
    assert proposals[0].message == "Reboot pending."


def test_parse_garbage_returns_empty():
    assert parse_proposals("not json at all") == []
    assert parse_proposals('{"kind": "speak"}') == []  # object, not array


def test_parse_drops_invalid_entries():
    raw = '[{"kind": "speak", "message": "ok"}, {"kind": "nonsense"}, {"kind": "act"}]'
    proposals = parse_proposals(raw)
    assert len(proposals) == 1


def test_build_prompt_includes_context():
    prompt = build_prompt(_ctx())
    assert "disk space" in prompt          # suppression surfaced
    assert "dark mode" in prompt           # memory surfaced
    assert "system_info" in prompt         # tool catalogue surfaced
    assert "Autonomy mode: full" in prompt


class _FakeChat:
    name = "fake"
    model = "fake-model"

    def __init__(self, text, boom=False):
        self._text = text
        self._boom = boom
        self.calls = []

    def generate(self, prompt, *, system=None, temperature=0.4, max_tokens=None):
        self.calls.append(prompt)
        if self._boom:
            raise RuntimeError("model down")
        return self._text


def test_llm_reasoner_happy_path():
    chat = _FakeChat('[{"kind": "speak", "message": "CPU is pegged."}]')
    reasoner = LLMReasoner(chat)

    proposals = reasoner.decide(_ctx())

    assert len(proposals) == 1
    assert proposals[0].message == "CPU is pegged."
    assert chat.calls and "What just changed" in chat.calls[0]


def test_llm_reasoner_survives_exception():
    assert LLMReasoner(_FakeChat("", boom=True)).decide(_ctx()) == []
