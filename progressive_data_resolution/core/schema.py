"""Schema parsing and validation.

A schema is untrusted configuration, so it is validated in full before any
workflow runs against it: unknown keywords are rejected rather than ignored, rule
references must point at real fields, activities must come from a fixed
allowlist, and the reference graph must be acyclic.
"""

import re
from collections.abc import Iterable
from typing import Any, Mapping, Optional

from .model import Collection, FieldDef, NextSchema, OutcomeRule, Schema
from .rules import lookup_path, referenced_fields, validate_reference, validate_rule

# `$schema` lets editors validate against schemas/progressive-data-resolution.schema.json.
_TOP_LEVEL_KEYS = {"$schema", "id", "version", "title", "context", "fields", "outcome", "next"}
_NEXT_KEYS = {"when", "schema", "version", "as"}
# In `next[].when`, `outcome` names this form's derived outcome, so no field may use it.
_RESERVED = {"outcome"}
_FIELD_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIELD_KEYS = {
    "type", "label", "required", "requiredWhen", "dependsOn", "readyWhen", "collection",
    "enum", "min", "max", "minLength", "maxLength", "pattern",
}  # fmt: skip
_TYPES = {"string", "number", "boolean", "object"}
_STRING_ONLY = ("enum", "minLength", "maxLength", "pattern")
_NUMBER_ONLY = ("min", "max")


class SchemaError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_schema(raw: dict[str, Any], allowed_activities: Iterable[str]) -> Schema:
    """Validate a raw JSON schema and convert it to typed form, or raise SchemaError."""
    errors = _validate(raw, set(allowed_activities))
    if errors:
        raise SchemaError(errors)
    fields = {
        fid: FieldDef(
            type=f["type"],
            label=f.get("label", fid),
            required=f.get("required", False),
            required_when=f.get("requiredWhen"),
            depends_on=list(f.get("dependsOn", [])),
            ready_when=f.get("readyWhen"),
            collection=Collection(**f["collection"]),
            enum=f.get("enum"),
            min=_static_bound(f.get("min")),
            max=_static_bound(f.get("max")),
            min_from=_bound_path(f.get("min")),
            max_from=_bound_path(f.get("max")),
            min_length=f.get("minLength"),
            max_length=f.get("maxLength"),
            pattern=f.get("pattern"),
        )
        for fid, f in raw["fields"].items()
    }
    return Schema(
        id=raw["id"],
        version=str(raw["version"]),
        title=raw.get("title", raw["id"]),
        fields=fields,
        outcome=[OutcomeRule(result=o["result"], when=o.get("when")) for o in raw["outcome"]],
        field_order=list(raw["fields"]),
        context=list(raw.get("context", [])),
        next=[
            NextSchema(
                schema_id=n["schema"],
                version=n["version"],
                as_namespace=n["as"],
                when=n.get("when"),
            )
            for n in raw.get("next", [])
        ],
    )


def _static_bound(value: Any) -> Optional[float]:
    return None if isinstance(value, dict) else value


def _bound_path(value: Any) -> Optional[str]:
    return value["from"] if isinstance(value, dict) else None


def effective_bounds(
    fdef: FieldDef, context: Mapping[str, Any]
) -> tuple[Optional[float], Optional[float]]:
    """Numeric bounds for a field, with any `{"from": path}` bound read from context."""

    def resolve(static: Optional[float], path: Optional[str]) -> Optional[float]:
        if path is None:
            return static
        value = lookup_path(context, path)
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        return value if is_number else None

    return resolve(fdef.min, fdef.min_from), resolve(fdef.max, fdef.max_from)


def check_context(schema: Schema, context: Mapping[str, Any]) -> list[str]:
    """Errors if the carried context cannot serve this schema's namespaces and bounds."""
    errors = [f"context '{ns}' was not provided" for ns in schema.context if ns not in context]
    for fid, fdef in schema.fields.items():
        lo, hi = effective_bounds(fdef, context)
        for path, bound in ((fdef.min_from, lo), (fdef.max_from, hi)):
            if path is not None and bound is None:
                errors.append(f"fields.{fid}: '{path}' is not a number in the context")
    return errors


def validate_value(
    fdef: FieldDef, value: Any, context: Optional[Mapping[str, Any]] = None
) -> Optional[str]:
    """Check a human-supplied value against its field definition. Returns an error or None."""
    if value is None:
        return "a value is required"
    if fdef.type == "boolean":
        return None if isinstance(value, bool) else "must be true or false"
    if fdef.type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "must be a number"
        lo, hi = effective_bounds(fdef, context or {})
        if (fdef.min_from and lo is None) or (fdef.max_from and hi is None):
            return "its limit is not available"
        if lo is not None and value < lo:
            return f"must be at least {lo:g}"
        if hi is not None and value > hi:
            return f"must be at most {hi:g}"
        return None
    if fdef.type == "string":
        if not isinstance(value, str):
            return "must be text"
        if fdef.enum is not None and value not in fdef.enum:
            return f"must be one of {', '.join(fdef.enum)}"
        if fdef.min_length is not None and len(value) < fdef.min_length:
            return f"must be at least {fdef.min_length} characters"
        if fdef.max_length is not None and len(value) > fdef.max_length:
            return f"must be at most {fdef.max_length} characters"
        if fdef.pattern is not None and not re.fullmatch(fdef.pattern, value):
            return f"must match the format {fdef.pattern}"
        return None
    return "cannot be entered directly"


def _validate(raw: Any, allowed_activities: set[str]) -> list[str]:
    if not isinstance(raw, dict):
        return ["schema must be an object"]
    errors = [f"unknown top-level key '{k}'" for k in sorted(set(raw) - _TOP_LEVEL_KEYS)]
    for key in ("id", "version"):
        if not isinstance(raw.get(key), str) or not raw[key]:
            errors.append(f"'{key}' must be a non-empty string")
    fields = raw.get("fields")
    if not isinstance(fields, dict) or not fields:
        return errors + ["'fields' must be a non-empty object"]

    field_ids = set(fields)
    errors += [
        f"fields.{fid}: ids must be letters, digits and underscores"
        for fid in fields
        if not _FIELD_ID.fullmatch(fid)
    ]
    errors += [f"fields.{fid}: '{fid}' is reserved" for fid in fields if fid in _RESERVED]

    namespaces = raw.get("context", [])
    if not isinstance(namespaces, list) or not all(isinstance(n, str) for n in namespaces):
        return errors + ["'context' must be a list of namespace names"]
    for ns in namespaces:
        if not _FIELD_ID.fullmatch(ns) or ns in field_ids or ns in _RESERVED:
            errors.append(f"context: '{ns}' must be a unique name distinct from every field")

    for fid, f in fields.items():
        errors += _validate_field(fid, f, field_ids, allowed_activities, namespaces)
    errors += _validate_outcome(raw.get("outcome"), field_ids, namespaces)
    errors += _validate_next(raw.get("next", []), field_ids, namespaces)
    if not errors:
        errors += _find_cycles(fields)
    return errors


def _validate_field(
    fid: str, f: Any, field_ids: set[str], allowed_activities: set[str], namespaces: list[str]
) -> list[str]:
    p = f"fields.{fid}"
    if not isinstance(f, dict):
        return [f"{p}: must be an object"]
    errors = [f"{p}: unknown keyword '{k}'" for k in sorted(set(f) - _FIELD_KEYS)]

    ftype = f.get("type")
    if ftype not in _TYPES:
        errors.append(f"{p}.type: must be one of {sorted(_TYPES)}")
    if ftype != "string":
        errors += [f"{p}.{k}: only valid for string fields" for k in _STRING_ONLY if k in f]
    if ftype != "number":
        errors += [f"{p}.{k}: only valid for number fields" for k in _NUMBER_ONLY if k in f]
    for key in _NUMBER_ONLY:
        if key in f:
            errors += _validate_bound(f[key], f"{p}.{key}", namespaces)
    if "enum" in f and (not isinstance(f["enum"], list) or not f["enum"]):
        errors.append(f"{p}.enum: must be a non-empty list")
    if "pattern" in f:
        try:
            re.compile(f["pattern"])
        except (re.error, TypeError) as err:
            errors.append(f"{p}.pattern: invalid regular expression ({err})")

    if "required" in f and f["required"] is not True:
        errors.append(f"{p}.required: must be true; omit it for conditional fields")
    elif ("required" in f) == ("requiredWhen" in f):
        errors.append(f"{p}: declare exactly one of 'required: true' or 'requiredWhen'")
    for key in ("requiredWhen", "readyWhen"):
        if key in f:
            errors += validate_rule(f[key], field_ids, f"{p}.{key}", namespaces)

    collection = f.get("collection")
    if not isinstance(collection, dict) or collection.get("strategy") not in ("human", "activity"):
        return errors + [f"{p}.collection: strategy must be 'human' or 'activity'"]
    if collection["strategy"] == "human":
        if set(collection) != {"strategy"}:
            errors.append(f"{p}.collection: human collection takes no other keys")
        if ftype == "object":
            errors.append(f"{p}: human fields cannot be of type object")
        if "dependsOn" in f or "readyWhen" in f:
            errors.append(f"{p}: dependsOn/readyWhen only apply to activity fields")
    else:
        if set(collection) != {"strategy", "activity"}:
            errors.append(f"{p}.collection: activity collection needs exactly 'activity'")
        elif collection["activity"] not in allowed_activities:
            errors.append(
                f"{p}.collection.activity: '{collection['activity']}' is not a registered activity"
            )
        if ftype != "object":
            errors.append(f"{p}: activity fields must be of type object")
        deps = f.get("dependsOn", [])
        if not isinstance(deps, list):
            errors.append(f"{p}.dependsOn: must be a list")
        else:
            for d in deps:
                if not isinstance(d, str):
                    errors.append(f"{p}.dependsOn: entries must be strings")
                elif d.split(".", 1)[0] in namespaces:
                    errors += [f"{p}.dependsOn: {e}" for e in validate_reference(d, (), namespaces)]
                elif d not in field_ids:
                    errors.append(f"{p}.dependsOn: unknown field '{d}'")
    return errors


def _validate_outcome(outcome: Any, field_ids: set[str], namespaces: list[str]) -> list[str]:
    if not isinstance(outcome, list) or not outcome:
        return ["'outcome' must be a non-empty list"]
    errors = []
    for i, o in enumerate(outcome):
        p = f"outcome[{i}]"
        if not isinstance(o, dict) or not isinstance(o.get("result"), str):
            errors.append(f"{p}: needs a string 'result'")
            continue
        errors += [f"{p}: unknown key '{k}'" for k in sorted(set(o) - {"when", "result"})]
        if "when" in o:
            errors += validate_rule(o["when"], field_ids, f"{p}.when", namespaces)
    if isinstance(outcome[-1], dict) and "when" in outcome[-1]:
        errors.append("outcome: the last entry must be an unconditional default")
    return errors


def _validate_bound(value: Any, p: str, namespaces: list[str]) -> list[str]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return []
    if not isinstance(value, dict) or set(value) != {"from"} or not isinstance(value["from"], str):
        return [f'{p}: must be a number or {{"from": "<context path>"}}']
    if value["from"].split(".")[0] not in namespaces:
        return [f"{p}: '{value['from']}' must start with a declared context namespace"]
    return [f"{p}: {e}" for e in validate_reference(value["from"], (), namespaces)]


def _validate_next(entries: Any, field_ids: set[str], namespaces: list[str]) -> list[str]:
    if not isinstance(entries, list):
        return ["'next' must be a list"]
    errors = []
    for i, n in enumerate(entries):
        p = f"next[{i}]"
        if not isinstance(n, dict):
            errors.append(f"{p}: must be an object")
            continue
        errors += [f"{p}: unknown key '{k}'" for k in sorted(set(n) - _NEXT_KEYS)]
        if not re.fullmatch(r"[a-z_]+", str(n.get("schema", ""))):
            errors.append(f"{p}.schema: must be a schema id")
        if not re.fullmatch(r"[0-9]+", str(n.get("version", ""))):
            errors.append(f"{p}.version: must be a version string")
        if not _FIELD_ID.fullmatch(str(n.get("as", ""))):
            errors.append(f"{p}.as: must be a namespace name")
        if "when" in n:
            errors += validate_rule(n["when"], field_ids | _RESERVED, f"{p}.when", namespaces)
    return errors


def _find_cycles(fields: dict[str, Any]) -> list[str]:
    def refs(f: dict[str, Any]) -> set[str]:
        found = set(f.get("dependsOn", []))
        found |= set(referenced_fields(f.get("requiredWhen")))
        found |= set(referenced_fields(f.get("readyWhen")))
        return found & set(fields)  # context namespaces are not part of the graph

    edges = {fid: refs(f) for fid, f in fields.items()}
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(fid: str, trail: list[str]) -> Optional[str]:
        if fid in done:
            return None
        if fid in visiting:
            return " -> ".join(trail[trail.index(fid) :] + [fid])
        visiting.add(fid)
        for nxt in sorted(edges[fid]):
            cycle = visit(nxt, trail + [fid])
            if cycle:
                return cycle
        visiting.discard(fid)
        done.add(fid)
        return None

    for fid in fields:
        cycle = visit(fid, [])
        if cycle:
            return [f"reference cycle: {cycle}"]
    return []
