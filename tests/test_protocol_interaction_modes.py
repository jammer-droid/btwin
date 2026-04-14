from pathlib import Path

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
