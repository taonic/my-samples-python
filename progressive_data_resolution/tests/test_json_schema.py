"""Keep the editor-facing JSON Schema and the authoritative Python validator in step."""

import copy
import json

import pytest
from jsonschema import Draft202012Validator

from progressive_data_resolution.activities import AUTOMATED_ACTIVITIES
from progressive_data_resolution.core.schema import SchemaError, parse_schema

from .test_rules_and_schema import ACTIVITIES, SCHEMAS, raw_schema

META = json.loads((SCHEMAS / "progressive-data-resolution.schema.json").read_text())
validator = Draft202012Validator(META)


def json_schema_errors(raw: dict) -> list[str]:
    return [e.message for e in validator.iter_errors(raw)]


def python_errors(raw: dict) -> list[str]:
    try:
        parse_schema(raw, ACTIVITIES)
    except SchemaError as err:
        return err.errors
    return []


def mutated(mutate) -> dict:
    raw = copy.deepcopy(raw_schema())
    mutate(raw)
    return raw


def test_meta_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(META)


SHIPPED = sorted(p.name for p in SCHEMAS.glob("*.v*.json"))


@pytest.mark.parametrize("name", SHIPPED)
def test_shipped_schemas_conform(name):
    raw = json.loads((SCHEMAS / name).read_text())
    assert json_schema_errors(raw) == []
    parse_schema(raw, AUTOMATED_ACTIVITIES)


def test_activity_enum_matches_registry():
    variants = META["$defs"]["collection"]["oneOf"]
    enum = next(v for v in variants if "activity" in v["properties"])["properties"]["activity"]
    assert set(enum["enum"]) == set(AUTOMATED_ACTIVITIES)


def _field(name, **changes):
    return lambda s: s["fields"][name].update(changes)


def _drop(name, key):
    return lambda s: s["fields"][name].pop(key)


# Structural mistakes: both validators must reject them.
STRUCTURAL = {
    "unknown keyword": _field("annual_income", minimum=0),
    "string keyword on number": _field("annual_income", pattern="x"),
    "number keyword on string": _field("applicant_name", min=1),
    "unregistered activity": lambda s: s["fields"]["identity_verification"]["collection"].update(
        activity="os.system"
    ),
    "unsupported rule operator": _field(
        "employer_name", requiredWhen={"field": "employment_type", "matches": ".*"}
    ),
    "two rule operators": _field(
        "employer_name", requiredWhen={"field": "employment_type", "equals": 1, "exists": True}
    ),
    "required and requiredWhen": _field("employer_name", required=True),
    "neither required nor requiredWhen": _drop("applicant_name", "required"),
    "required false": _field("applicant_name", required=False),
    "dependsOn on human field": _field("applicant_name", dependsOn=["employment_type"]),
    "human field of type object": _field("applicant_name", type="object"),
    "activity field not object": _field("identity_verification", type="string"),
    "bad field id": lambda s: s["fields"].update({"bad.id": s["fields"].pop("employer_name")}),
    "unknown top-level key": lambda s: s.update(extra=1),
    "empty outcome": lambda s: s.update(outcome=[]),
}

# Cross-field mistakes JSON Schema cannot express; only the loader catches them.
CROSS_FIELD = {
    "rule references unknown field": _field(
        "employer_name", requiredWhen={"field": "nope", "equals": 1}
    ),
    "dependsOn unknown field": _field("identity_verification", dependsOn=["nope"]),
    "reference cycle": _field(
        "employer_name", requiredWhen={"field": "employer_name", "exists": True}
    ),
    "conditional last outcome": lambda s: s["outcome"].pop(),
}


@pytest.mark.parametrize("case", STRUCTURAL)
def test_structural_mistakes_rejected_by_both(case):
    raw = mutated(STRUCTURAL[case])
    assert json_schema_errors(raw), f"JSON Schema accepted: {case}"
    assert python_errors(raw), f"loader accepted: {case}"


@pytest.mark.parametrize("case", CROSS_FIELD)
def test_cross_field_mistakes_are_loader_only(case):
    raw = mutated(CROSS_FIELD[case])
    assert json_schema_errors(raw) == []
    assert python_errors(raw)
