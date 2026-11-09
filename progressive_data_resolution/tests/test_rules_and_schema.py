import copy
import json
from pathlib import Path

import pytest

from progressive_data_resolution.core.rules import ABSENT, UNKNOWN, evaluate
from progressive_data_resolution.core.schema import SchemaError, parse_schema, validate_value

SCHEMAS = Path(__file__).parent.parent / "schemas"
ACTIVITIES = {"verify_identity", "assess_affordability", "fetch_credit_score"}


def raw_schema(version: str = "1") -> dict:
    return json.loads((SCHEMAS / f"loan_application.v{version}.json").read_text())


def lookup_from(values: dict):
    return lambda path: values.get(path, UNKNOWN)


class TestRules:
    def test_equals_known_absent_unknown(self):
        rule = {"field": "x", "equals": "a"}
        assert evaluate(rule, lookup_from({"x": "a"})) is True
        assert evaluate(rule, lookup_from({"x": "b"})) is False
        assert evaluate(rule, lookup_from({"x": ABSENT})) is False
        assert evaluate(rule, lookup_from({})) is None

    def test_not_equals_treats_absent_as_different(self):
        rule = {"field": "x", "notEquals": "a"}
        assert evaluate(rule, lookup_from({"x": ABSENT})) is True
        assert evaluate(rule, lookup_from({"x": "a"})) is False
        assert evaluate(rule, lookup_from({})) is None

    def test_exists_is_never_unknown(self):
        assert evaluate({"field": "x", "exists": True}, lookup_from({})) is False
        assert evaluate({"field": "x", "exists": False}, lookup_from({})) is True
        assert evaluate({"field": "x", "exists": True}, lookup_from({"x": 0})) is True

    def test_kleene_and_or(self):
        t = {"field": "t", "equals": 1}
        f = {"field": "f", "equals": 1}
        u = {"field": "u", "equals": 1}
        lookup = lookup_from({"t": 1, "f": 0})
        assert evaluate({"and": [f, u]}, lookup) is False
        assert evaluate({"and": [t, u]}, lookup) is None
        assert evaluate({"or": [t, u]}, lookup) is True
        assert evaluate({"or": [f, u]}, lookup) is None
        assert evaluate({"or": [f, f]}, lookup) is False


class TestSchema:
    def test_shipped_schema_is_valid(self):
        schema = parse_schema(raw_schema(), ACTIVITIES)
        assert schema.version == "1"
        assert schema.fields["business_registration"].required_when == {
            "field": "employment_type",
            "equals": "self_employed",
        }

    @pytest.mark.parametrize(
        "mutate, expected",
        [
            (lambda s: s["fields"]["annual_income"].update(minimum=0), "unknown keyword 'minimum'"),
            (lambda s: s["fields"]["annual_income"].update(pattern="x"), "only valid for string"),
            (
                lambda s: s["fields"]["identity_verification"]["collection"].update(
                    activity="os.system"
                ),
                "not a registered activity",
            ),
            (
                lambda s: s["fields"]["employer_name"].update(
                    requiredWhen={"field": "nope", "equals": 1}
                ),
                "unknown field 'nope'",
            ),
            (
                lambda s: s["fields"]["employer_name"].update(
                    requiredWhen={"field": "employment_type", "matches": ".*"}
                ),
                "unsupported rule keys",
            ),
            (lambda s: s["fields"]["employer_name"].update(required=True), "exactly one of"),
            (lambda s: s["outcome"].pop(), "unconditional default"),
            (
                lambda s: s["fields"]["employer_name"].update(
                    requiredWhen={"field": "employer_name", "exists": True}
                ),
                "reference cycle: employer_name -> employer_name",
            ),
        ],
    )
    def test_invalid_schemas_are_rejected(self, mutate, expected):
        raw = copy.deepcopy(raw_schema())
        mutate(raw)
        with pytest.raises(SchemaError) as err:
            parse_schema(raw, ACTIVITIES)
        assert any(expected in e for e in err.value.errors), err.value.errors

    def test_value_validation(self):
        fields = parse_schema(raw_schema(), ACTIVITIES).fields
        assert validate_value(fields["annual_income"], 50000) is None
        assert validate_value(fields["annual_income"], -1) == "must be at least 0"
        assert validate_value(fields["annual_income"], True) == "must be a number"
        assert validate_value(fields["business_registration"], "BR-123456") is None
        assert "format" in validate_value(fields["business_registration"], "BR-12")
        assert "one of" in validate_value(fields["employment_type"], "retired")
        assert validate_value(fields["consent_to_checks"], "yes") == "must be true or false"
