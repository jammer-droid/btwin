from pathlib import Path

import pytest
from pydantic import ValidationError

from btwin_core.protocol_store import Protocol, ProtocolGuardSet, ProtocolPhase
from btwin_core.protocol_store import ProtocolStore


def test_protocol_store_parses_interaction_mode(tmp_path: Path):
    path = tmp_path / "protocols"
    path.mkdir()
    (path / "debate.yaml").write_text(
        """
name: debate
phases:
  - name: discussion
    actions: [discuss]
interaction:
  mode: orchestrated_chat
  allow_user_chat: true
  default_actor: user
""",
        encoding="utf-8",
    )

    store = ProtocolStore(path)
    proto = store.get_protocol("debate")

    assert proto is not None
    assert proto.interaction.mode == "orchestrated_chat"
    assert proto.interaction.allow_user_chat is True
    assert proto.interaction.default_actor == "user"


def test_protocol_store_preserves_unquoted_on_transition_keys_in_block_mappings(tmp_path: Path):
    path = tmp_path / "protocols"
    path.mkdir()
    (path / "custom-review.yaml").write_text(
        """
name: custom-review
phases:
  - name: review
    actions: [contribute]
  - name: decision
    actions: [decide]
transitions:
  - from: review
    to: review
    on: retry
  - from: review
    to: decision
    on: accept
outcomes: [retry, accept]
""",
        encoding="utf-8",
    )

    store = ProtocolStore(path)
    proto = store.get_protocol("custom-review")

    assert proto is not None
    assert [transition.on for transition in proto.transitions] == ["retry", "accept"]


def test_protocol_store_preserves_unquoted_on_transition_keys_in_flow_mappings(tmp_path: Path):
    path = tmp_path / "protocols"
    path.mkdir()
    (path / "custom-review.yaml").write_text(
        """
name: custom-review
phases:
  - name: review
    actions: [contribute]
  - name: decision
    actions: [decide]
transitions:
  - {from: review, to: review, on: retry}
  - {from: review, to: decision, on: accept}
outcomes: [retry, accept]
""",
        encoding="utf-8",
    )

    store = ProtocolStore(path)
    proto = store.get_protocol("custom-review")

    assert proto is not None
    assert [transition.on for transition in proto.transitions] == ["retry", "accept"]


def test_protocol_store_does_not_mutate_block_scalar_lines_starting_with_on(tmp_path: Path):
    path = tmp_path / "protocols"
    path.mkdir()
    (path / "custom-review.yaml").write_text(
        """
name: custom-review
phases:
  - name: review
    actions: [contribute]
    guidance: |
      Keep the checklist exactly as written.
      on: this line is literal guidance, not a transition key
  - name: decision
    actions: [decide]
transitions:
  - from: review
    to: decision
    on: accept
outcomes: [accept]
""",
        encoding="utf-8",
    )

    store = ProtocolStore(path)
    proto = store.get_protocol("custom-review")

    assert proto is not None
    assert proto.phases[0].guidance == (
        "Keep the checklist exactly as written.\n"
        "on: this line is literal guidance, not a transition key\n"
    )
    assert proto.transitions[0].on == "accept"


def test_protocol_rejects_unknown_guard_set_reference():
    with pytest.raises(ValidationError, match="guard_set"):
        Protocol.model_validate(
            {
                "name": "debate",
                "guard_sets": [
                    {"name": "discussion-guards", "guards": ["contribution_required"]},
                ],
                "phases": [
                    {
                        "name": "discussion",
                        "actions": [ "discuss" ],
                        "guard_set": "missing-guards",
                    }
                ],
            }
        )


def test_protocol_rejects_unknown_guard_name():
    with pytest.raises(ValidationError, match="unsupported guard"):
        ProtocolGuardSet(
            name="discussion-guards",
            guards=["contribution_required", "not_a_real_guard"],
        )


def test_protocol_rejects_duplicate_top_level_guard_set_names():
    with pytest.raises(ValidationError, match="duplicate guard_set name"):
        Protocol(
            name="debate",
            guard_sets=[
                ProtocolGuardSet(name="discussion-guards", guards=["contribution_required"]),
                ProtocolGuardSet(name="discussion-guards", guards=["phase_actor_eligibility"]),
            ],
            phases=[
                ProtocolPhase(
                    name="discussion",
                    actions=["discuss"],
                    guard_set="discussion-guards",
                )
            ],
        )
