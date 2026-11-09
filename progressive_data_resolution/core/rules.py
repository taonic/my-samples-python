"""The rule language: `equals`, `notEquals`, `exists`, `and`, `or`. Nothing else.

Rules are plain JSON objects, never code:

    {"field": "employment_type", "equals": "self_employed"}
    {"field": "identity_verification.status", "notEquals": "passed"}
    {"field": "consent_to_checks", "exists": true}
    {"and": [<rule>, ...]}
    {"or": [<rule>, ...]}

Evaluation is three-valued. A field whose value is not yet settled is UNKNOWN,
and a comparison against it is `None` rather than `False`; that is what lets the
resolver say "can't tell yet" (blocked) instead of "doesn't apply". `and`/`or`
follow Kleene logic, so `{"and": [false, unknown]}` is still `False`.

A field that is settled as not applicable is ABSENT: `equals` is False,
`notEquals` is True. `exists` only asks whether a value is present right now, so
it is never unknown.

A schema that declares a context namespace (data carried over from an earlier
form) may also read `<namespace>.<field>` or `<namespace>.<field>.<key>`. Context
is read-only and fully known, so those references are never unknown either.
"""

from typing import Any, Callable, Collection, Mapping, Optional, Union


class _Sentinel:
    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:
        return self._name


UNKNOWN = _Sentinel("UNKNOWN")
ABSENT = _Sentinel("ABSENT")

Lookup = Callable[[str], Union[Any, _Sentinel]]

_LEAF_OPS = ("equals", "notEquals", "exists")


def lookup_path(values: Mapping[str, Any], path: str) -> Any:
    """Walk a dotted path through nested dicts. Returns ABSENT if any step is missing."""
    current: Any = values
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return ABSENT
        current = current[key]
    return current


def evaluate(rule: dict[str, Any], lookup: Lookup) -> Optional[bool]:
    """Evaluate a rule. Returns True, False, or None for unknown."""
    if "and" in rule:
        results = [evaluate(r, lookup) for r in rule["and"]]
        if False in results:
            return False
        return None if None in results else True
    if "or" in rule:
        results = [evaluate(r, lookup) for r in rule["or"]]
        if True in results:
            return True
        return None if None in results else False

    value = lookup(rule["field"])
    if "exists" in rule:
        present = value is not UNKNOWN and value is not ABSENT
        return present == rule["exists"]
    if value is UNKNOWN:
        return None
    if "equals" in rule:
        return value is not ABSENT and value == rule["equals"]
    return value is ABSENT or value != rule["notEquals"]


def referenced_fields(rule: Optional[dict[str, Any]]) -> list[str]:
    """Top-level field ids a rule reads, in order, without duplicates."""
    if not rule:
        return []
    if "and" in rule or "or" in rule:
        seen: list[str] = []
        for r in rule.get("and", rule.get("or", [])):
            for f in referenced_fields(r):
                if f not in seen:
                    seen.append(f)
        return seen
    return [rule["field"].split(".", 1)[0]]


def validate_rule(
    rule: Any, field_ids: Collection[str], path: str, namespaces: Collection[str] = ()
) -> list[str]:
    """Structural validation used by the schema loader."""
    if not isinstance(rule, dict):
        return [f"{path}: rule must be an object"]
    for combinator in ("and", "or"):
        if combinator in rule:
            if set(rule) != {combinator}:
                return [f"{path}: '{combinator}' cannot be combined with other keys"]
            children = rule[combinator]
            if not isinstance(children, list) or not children:
                return [f"{path}.{combinator}: must be a non-empty list"]
            errors: list[str] = []
            for i, child in enumerate(children):
                errors += validate_rule(child, field_ids, f"{path}.{combinator}[{i}]", namespaces)
            return errors

    ops = [op for op in _LEAF_OPS if op in rule]
    unknown_keys = set(rule) - {"field", *_LEAF_OPS}
    if unknown_keys:
        return [f"{path}: unsupported rule keys {sorted(unknown_keys)}"]
    if len(ops) != 1 or "field" not in rule:
        return [f"{path}: a rule needs 'field' and exactly one of {list(_LEAF_OPS)}"]
    ref = rule["field"]
    if not isinstance(ref, str):
        return [f"{path}: field must be a string"]
    errors = validate_reference(ref, field_ids, namespaces)
    if errors:
        return [f"{path}: {e}" for e in errors]
    if ops[0] == "exists" and not isinstance(rule["exists"], bool):
        return [f"{path}: 'exists' must be true or false"]
    return []


def validate_reference(
    ref: str, field_ids: Collection[str], namespaces: Collection[str] = ()
) -> list[str]:
    """`field`, `field.key`, or (for declared context) `namespace.field[.key]`."""
    parts = ref.split(".")
    if parts[0] in namespaces:
        if not 2 <= len(parts) <= 3:
            return [f"'{ref}' must be '{parts[0]}.field' or '{parts[0]}.field.key'"]
        return []
    if len(parts) > 2:
        return [f"'{ref}' must be 'name' or 'name.key'"]
    if parts[0] not in field_ids:
        return [f"unknown field '{ref}'"]
    return []


def describe(rule: Optional[dict[str, Any]]) -> str:
    """Human-readable rendering for decision records and debug output."""
    if not rule:
        return "always"
    if "and" in rule:
        return " and ".join(f"({describe(r)})" for r in rule["and"])
    if "or" in rule:
        return " or ".join(f"({describe(r)})" for r in rule["or"])
    if "exists" in rule:
        return f"{rule['field']} {'is present' if rule['exists'] else 'is absent'}"
    if "equals" in rule:
        return f"{rule['field']} == {rule['equals']!r}"
    return f"{rule['field']} != {rule['notEquals']!r}"
