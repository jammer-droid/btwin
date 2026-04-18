import pytest

from btwin_core.protocol_store import (
    ProtocolStore,
    ProtocolValidationLayerError,
    compile_protocol_definition,
)


def _authoring_protocol_definition() -> dict[str, object]:
    return {
        "name": "review-loop",
        "description": "Authoring-first review loop",
        "guard_sets": [
            {
                "name": "review-default",
                "guards": ["contribution_required", "transition_precondition"],
            }
        ],
        "phases": [
            {
                "name": "review",
                "actions": ["contribute"],
                "guard_set": "review-default",
                "gate": "review-gate",
                "outcome_policy": "review-outcomes",
            },
            {
                "name": "decision",
                "actions": ["decide"],
                "decided_by": "user",
            },
        ],
        "gates": [
            {
                "name": "review-gate",
                "routes": [
                    {
                        "outcome": "retry",
                        "target_phase": "review",
                        "alias": "Retry Loop",
                        "key": "gate-retry",
                    },
                    {
                        "outcome": "accept",
                        "target_phase": "decision",
                        "alias": "Accept Gate",
                        "key": "gate-accept",
                    },
                ],
            }
        ],
        "outcome_policies": [
            {
                "name": "review-outcomes",
                "emitters": ["reviewer", "user"],
                "actions": ["decide"],
                "outcomes": ["retry", "accept"],
            }
        ],
    }


def test_compile_protocol_definition_normalizes_authoring_dsl_into_canonical_runtime_shape():
    protocol = compile_protocol_definition(_authoring_protocol_definition())

    assert [transition.model_dump(by_alias=True) for transition in protocol.transitions] == [
        {
            "from": "review",
            "to": "review",
            "on": "retry",
            "alias": "Retry Loop",
            "key": "gate-retry",
        },
        {
            "from": "review",
            "to": "decision",
            "on": "accept",
            "alias": "Accept Gate",
            "key": "gate-accept",
        },
    ]
    assert protocol.outcomes == ["retry", "accept"]
    assert protocol.phases[0].guard_set == "review-default"
    assert protocol.phases[0].declared_guards == [
        "contribution_required",
        "transition_precondition",
    ]
    assert protocol.phases[0].outcome_policy == "review-outcomes"
    assert protocol.phases[0].outcome_emitters == ["reviewer", "user"]
    assert protocol.phases[0].outcome_actions == ["decide"]
    assert protocol.phases[0].policy_outcomes == ["retry", "accept"]


def test_compile_protocol_definition_merges_authoring_gate_metadata_into_matching_transition():
    definition = _authoring_protocol_definition()
    definition["transitions"] = [
        {"from": "review", "to": "review", "on": "retry"},
        {"from": "review", "to": "decision", "on": "accept"},
    ]

    protocol = compile_protocol_definition(definition)

    assert [transition.model_dump(by_alias=True) for transition in protocol.transitions] == [
        {
            "from": "review",
            "to": "review",
            "on": "retry",
            "alias": "Retry Loop",
            "key": "gate-retry",
        },
        {
            "from": "review",
            "to": "decision",
            "on": "accept",
            "alias": "Accept Gate",
            "key": "gate-accept",
        },
    ]


def test_compile_protocol_definition_reports_schema_layer_errors():
    with pytest.raises(ProtocolValidationLayerError, match="schema validation failed"):
        compile_protocol_definition(
            {
                "name": "review-loop",
                "phases": [{"name": "review", "actions": ["ship-it"]}],
            }
        )


def test_compile_protocol_definition_rejects_unknown_authoring_field_at_schema_layer():
    with pytest.raises(
        ProtocolValidationLayerError,
        match="schema validation failed: phases\\.0\\.guard_sett: Extra inputs are not permitted",
    ):
        compile_protocol_definition(
            {
                "name": "review-loop",
                "phases": [
                    {
                        "name": "review",
                        "actions": ["contribute"],
                        "guard_sett": "review-default",
                    }
                ],
            }
        )


def test_compile_protocol_definition_reports_semantic_layer_errors():
    definition = _authoring_protocol_definition()
    definition["transitions"] = [{"from": "review", "to": "review", "on": "accept"}]

    with pytest.raises(
        ProtocolValidationLayerError,
        match="semantic validation failed: gate 'review-gate' route for phase 'review' and outcome 'accept' contradicts canonical transition target 'review'",
    ):
        compile_protocol_definition(definition)


def test_protocol_store_loads_authoring_only_yaml_as_compiled_protocol(tmp_path):
    store = ProtocolStore(tmp_path / "protocols")
    store.save_protocol(compile_protocol_definition(_authoring_protocol_definition()))

    protocol = store.get_protocol("review-loop")

    assert protocol is not None
    assert [transition.on for transition in protocol.transitions] == ["retry", "accept"]
    assert protocol.phases[0].declared_guards == [
        "contribution_required",
        "transition_precondition",
    ]
