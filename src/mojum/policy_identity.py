"""Single source of truth for persisted Aquarium policy identities.

Issue #116 blocks release because two unreleased draft runtimes both hung
the public word ``local_cell_v2`` on *different* persisted schemas: one
carried it under ``motor_policy`` (``mojum.developmental.explorer.local-cell/2``)
and the other under ``developmental_policy`` (``mojum.developmental.local-cell/2``).
Reading a PRIVATE journal is only safe when the writer contract is known
unambiguously, so every supported identity is declared exactly once here.

The read-only inspector (:mod:`mojum.aquarium_wire`) resolves a stored
manifest selector set against this table. Declaring two identities with the
same inspection id, public configuration, selector set, or persisted
contract is a programming error and fails at import time rather than
silently reinterpreting somebody's private history.

Nothing here selects a policy for new organisms: this table is descriptive.
Promoting either v2 draft, or choosing one canonical future v2 identity,
remains the explicit owner decision tracked in #116.
"""

from __future__ import annotations

from dataclasses import dataclass

from .aquarium import EPOCH_EVENT_SCHEMA
from .archived_policy_v2a import SCHEMA as V2A_SCHEMA
from .cell_policy_v2 import (
    LEARNING_SCHEMA as V2B_LEARNING_SCHEMA,
    SCHEMA as V2B_SCHEMA,
)
from .cell_policy_v3 import (
    LEARNING_SCHEMA as V3_LEARNING_SCHEMA,
    SCHEMA as V3_SCHEMA,
)
from .cell_policy_v3_escape import (
    LEARNING_SCHEMA as V3E_LEARNING_SCHEMA,
    MAX_CONSECUTIVE_RESTS as V3E_MAX_CONSECUTIVE_RESTS,
    SCHEMA as V3E_SCHEMA,
)
from .cell_policy_v4 import (
    LEARNING_SCHEMA as V4_LEARNING_SCHEMA,
    SCHEMA as V4_SCHEMA,
)
from .developmental import SCHEMA as MOTOR_PLAY_SCHEMA
from .epoch_sense_v2 import (
    EPOCH_TICKS,
    MAX_ABSOLUTE_TICKS,
    SCHEMA as EPOCH_SENSE_SCHEMA,
)


SCHEMA = "mojum.aquarium.policy-identities/1"

LEGACY_COARSE_SCHEMA = "mojum.developmental.explorer/1"


class PolicyIdentityError(ValueError):
    """A duplicate, incomplete or unknown persisted policy identity."""


@dataclass(frozen=True)
class PolicyIdentity:
    """One persisted contract and the manifest selectors that pin it.

    ``selectors`` is an ordered tuple of ``(key, value)`` pairs so the
    declaration can be hashed and compared exactly as stored in SQLite.
    Keys are non-empty strings; values are the JSON scalars a manifest may
    legitimately pin (string or plain integer), never a container.
    """

    inspection_id: str
    selectors: tuple
    state_schema: str | None
    learning_schema: str | None
    intent_source: str | None
    configuration: str

    @property
    def selector_dict(self):
        return dict(self.selectors)

    @property
    def frame_contract(self):
        """``None`` for scripted journals, else the pinned frame contract."""
        if self.state_schema is None:
            return None
        return self.state_schema, self.learning_schema, self.intent_source


def _check_fields(item):
    if type(item) is not PolicyIdentity:
        raise PolicyIdentityError("policy identity must be a PolicyIdentity")
    for name in ("inspection_id", "configuration"):
        value = getattr(item, name)
        if type(value) is not str or not value:
            raise PolicyIdentityError(f"policy identity needs a {name}")
    if type(item.selectors) is not tuple or any(
        type(pair) is not tuple or len(pair) != 2
        or type(pair[0]) is not str or not pair[0]
        or (type(pair[1]) is str and not pair[1])
        or type(pair[1]) not in (str, int)
        for pair in item.selectors
    ):
        raise PolicyIdentityError("policy selectors must be string pairs")
    if len({key for key, _ in item.selectors}) != len(item.selectors):
        raise PolicyIdentityError("duplicate selector key in one identity")
    if item.state_schema is None:
        if item.learning_schema is not None or item.intent_source is not None:
            raise PolicyIdentityError(
                "incomplete persisted contract for scripted journal")
        return
    for name in ("state_schema", "learning_schema", "intent_source"):
        value = getattr(item, name)
        if value is not None and (type(value) is not str or not value):
            raise PolicyIdentityError(f"invalid {name}")


def validate_identities(identities):
    """Fail closed on duplicate or incomplete policy identity declarations."""
    seen_ids, seen_config, seen_selectors, seen_contracts = (
        set(), set(), set(), set(),
    )
    for item in identities:
        _check_fields(item)
        selectors = tuple(sorted(item.selector_dict.items()))
        if item.inspection_id in seen_ids:
            raise PolicyIdentityError("duplicate policy inspection id")
        if item.configuration in seen_config:
            raise PolicyIdentityError("duplicate policy configuration")
        if selectors in seen_selectors:
            raise PolicyIdentityError("duplicate persisted policy selectors")
        if item.frame_contract is not None and item.frame_contract in seen_contracts:
            raise PolicyIdentityError("duplicate persisted contract")
        seen_ids.add(item.inspection_id)
        seen_config.add(item.configuration)
        seen_selectors.add(selectors)
        if item.frame_contract is not None:
            seen_contracts.add(item.frame_contract)
    return tuple(identities)


POLICIES = validate_identities((
    PolicyIdentity(
        inspection_id="scripted_v1",
        selectors=(),
        state_schema=None, learning_schema=None, intent_source=None,
        configuration="scripted",
    ),
    PolicyIdentity(
        inspection_id="motor_play_v1",
        selectors=(
            ("motor_mode", "developmental_fixture"),
            ("motor_policy_schema", MOTOR_PLAY_SCHEMA),
        ),
        state_schema=MOTOR_PLAY_SCHEMA, learning_schema=None,
        intent_source=None,
        configuration="motor_mode=developmental_fixture",
    ),
    PolicyIdentity(
        inspection_id="legacy_coarse_v1",
        selectors=(
            ("exploration_mode", "goal_free"),
            ("developmental_schema", LEGACY_COARSE_SCHEMA),
        ),
        state_schema=LEGACY_COARSE_SCHEMA,
        learning_schema=LEGACY_COARSE_SCHEMA,
        intent_source="bounded_goal_free_motor_play",
        configuration=(
            "exploration_mode=goal_free + developmental_schema="
            + LEGACY_COARSE_SCHEMA + " (legacy)"
        ),
    ),
    # The two unreleased drafts below share the word ``local_cell_v2`` on
    # purpose: renaming their persisted schemas would rewrite PRIVATE
    # history. They differ in manifest key, schema and intent source and
    # must never be treated as one ABI.
    PolicyIdentity(
        inspection_id="local_cell_v2_a_unreleased",
        selectors=(
            ("exploration_mode", "goal_free"),
            ("developmental_schema", V2A_SCHEMA),
            ("motor_policy", "local_cell_v2"),
            ("motor_policy_schema", V2A_SCHEMA),
        ),
        state_schema=V2A_SCHEMA, learning_schema=V2A_SCHEMA,
        intent_source="bounded_local_cell_motor_play_v2",
        configuration="motor_policy=local_cell_v2 (unreleased draft A)",
    ),
    PolicyIdentity(
        inspection_id="local_cell_v2_b_unreleased",
        selectors=(
            ("exploration_mode", "goal_free"),
            ("developmental_schema", V2B_SCHEMA),
            ("developmental_policy", "local_cell_v2"),
        ),
        state_schema=V2B_SCHEMA, learning_schema=V2B_LEARNING_SCHEMA,
        intent_source="bounded_goal_free_local_cell_motor_play_v2",
        configuration="developmental_policy=local_cell_v2 (unreleased draft B)",
    ),
    PolicyIdentity(
        inspection_id="local_cell_v3",
        selectors=(
            ("exploration_mode", "goal_free"),
            ("developmental_schema", V3_SCHEMA),
            ("developmental_policy", "local_cell_v3"),
        ),
        state_schema=V3_SCHEMA, learning_schema=V3_LEARNING_SCHEMA,
        intent_source="bounded_goal_free_local_cell_motor_play_v3",
        configuration="developmental_policy=local_cell_v3",
    ),
    PolicyIdentity(
        inspection_id="local_cell_v3_escape",
        selectors=(
            ("exploration_mode", "goal_free"),
            ("developmental_schema", V3E_SCHEMA),
            ("developmental_policy", "local_cell_v3_escape"),
            ("max_consecutive_rests", V3E_MAX_CONSECUTIVE_RESTS),
        ),
        state_schema=V3E_SCHEMA, learning_schema=V3E_LEARNING_SCHEMA,
        intent_source="bounded_goal_free_local_cell_escape_probe_v3e",
        configuration="developmental_policy=local_cell_v3_escape",
    ),
    PolicyIdentity(
        inspection_id="local_cell_v4",
        selectors=(
            ("exploration_mode", "goal_free"),
            ("developmental_schema", V4_SCHEMA),
            ("developmental_policy", "local_cell_v4"),
            ("epoch_transition_schema", EPOCH_EVENT_SCHEMA),
            ("epoch_ticks", EPOCH_TICKS),
            ("proprioception_schema", EPOCH_SENSE_SCHEMA),
            ("max_absolute_ticks", MAX_ABSOLUTE_TICKS),
        ),
        state_schema=V4_SCHEMA, learning_schema=V4_LEARNING_SCHEMA,
        intent_source="bounded_goal_free_local_cell_motor_play_v4",
        configuration="developmental_policy=local_cell_v4",
    ),
))

MOTOR_SELECTOR_KEYS = frozenset(
    key for item in POLICIES for key in item.selector_dict
)


def by_inspection_id(inspection_id):
    for item in POLICIES:
        if item.inspection_id == inspection_id:
            return item
    raise PolicyIdentityError("ambiguous or unknown stored Aquarium policy")


def by_selector(selectors):
    """Return the single identity pinned by an extracted manifest selector set."""
    if type(selectors) is not dict:
        raise PolicyIdentityError("ambiguous or unknown stored Aquarium policy")
    # Python dictionary equality considers an integer equal to its float
    # representation. Persisted JSON selectors must also match their types.
    matches = [
        item for item in POLICIES
        if item.selector_dict == selectors
        and all(type(selectors[key]) is type(value) for key, value in item.selectors)
    ]
    if len(matches) != 1:
        raise PolicyIdentityError("ambiguous or unknown stored Aquarium policy")
    return matches[0]


def identities_mentioning_value(value):
    """Every identity whose selectors carry ``value`` under any key."""
    return tuple(
        item for item in POLICIES if value in item.selector_dict.values()
    )


def as_public_record(item):
    """JSON-ready description used by ``--list-policies``."""
    return {
        "inspection_id": item.inspection_id,
        "configuration": item.configuration,
        "selectors": item.selector_dict,
        "state_schema": item.state_schema,
        "learning_schema": item.learning_schema,
        "intent_source": item.intent_source,
    }
