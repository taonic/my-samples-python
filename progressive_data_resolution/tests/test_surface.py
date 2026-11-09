from typing import Any

from progressive_data_resolution.core.model import ResolutionState, RetainedValue
from progressive_data_resolution.core.resolver import resolve_requirements
from progressive_data_resolution.core.surface import render_surface

from .test_resolver_and_planner import EMPLOYED, SELF_EMPLOYED, V1, complete_activities


def state_for(data: dict[str, Any], results=None, **overrides: Any) -> ResolutionState:
    res = resolve_requirements(V1, data, results or {}, {})
    fields = dict(
        schema_id="loan_application",
        schema_version="1",
        status="waiting_for_input",
        revision=3,
        schema=V1,
        progress=res.progress,
        outcome=res.outcome,
        data={k: v for k, v in data.items() if k not in res.retire},
        values=res.values,
        requirements=res.requirements,
    )
    return ResolutionState(**{**fields, **overrides})


def fields_of(surface) -> list[str]:
    return [c.field for c in surface.components]


def test_initial_surface_renders_supported_component_types():
    surface = render_surface(state_for({}))
    assert fields_of(surface) == [
        "applicant_name",
        "consent_to_checks",
        "employment_type",
        "annual_income",
    ]
    by_field = {c.field: c for c in surface.components}
    assert by_field["applicant_name"].type == "text_input"
    assert by_field["consent_to_checks"].type == "boolean_input"
    assert by_field["employment_type"].type == "dropdown"
    assert by_field["employment_type"].options == ["employed", "self_employed"]
    assert by_field["annual_income"].type == "number_input"
    assert by_field["annual_income"].constraints == {"min": 0}
    assert surface.can_submit and surface.revision == 3


def test_conditional_fields_follow_employment_type():
    self_employed = fields_of(render_surface(state_for({"employment_type": "self_employed"})))
    assert "business_registration" in self_employed and "employer_name" not in self_employed
    employed = fields_of(render_surface(state_for({"employment_type": "employed"})))
    assert "employer_name" in employed and "business_registration" not in employed


def test_satisfied_fields_are_prefilled_for_correction():
    surface = render_surface(state_for(SELF_EMPLOYED))
    by_field = {c.field: c for c in surface.components}
    assert by_field["employment_type"].value == "self_employed"
    assert by_field["business_operating_years"].value == 4


def test_retained_values_are_not_rendered():
    data = {**SELF_EMPLOYED, "employment_type": "employed"}
    state = state_for(data, retained={"business_registration": [RetainedValue("BR-123456", 2)]})
    surface = render_surface(state)
    assert "business_registration" not in fields_of(surface)
    assert all(c.value != "BR-123456" for c in surface.components)


def test_invalid_value_surfaces_error():
    surface = render_surface(
        state_for({"business_registration": "nope", "employment_type": "self_employed"})
    )
    reg = next(c for c in surface.components if c.field == "business_registration")
    assert reg.error == "must match the format BR-[0-9]{6}"


def test_automated_checks_and_completion():
    results = complete_activities(EMPLOYED)
    running = render_surface(state_for(EMPLOYED))
    assert [a.field for a in running.automated] == [
        "identity_verification",
        "affordability_assessment",
        "credit_check",
    ]
    confirmed = {**EMPLOYED, "final_confirmation": True}
    done = render_surface(
        state_for(confirmed, results, status="completed", status_reason="outcome: approved")
    )
    assert done.outcome == "approved"
    assert not done.can_submit
    assert done.automated[0].result == {"status": "passed", "band": "good"}
