from typing import Any

from progressive_data_resolution.core.model import ActivityResult, PlannedAction
from progressive_data_resolution.core.planner import plan_actions
from progressive_data_resolution.core.resolver import resolve_requirements
from progressive_data_resolution.core.schema import parse_schema
from progressive_data_resolution.core.submission import check_submission

from .test_rules_and_schema import ACTIVITIES, raw_schema

V1 = parse_schema(raw_schema(), ACTIVITIES)

BASE = {"applicant_name": "Ada Lovelace", "consent_to_checks": True}
SELF_EMPLOYED = {
    **BASE,
    "employment_type": "self_employed",
    "annual_income": 90000,
    "business_registration": "BR-123456",
    "business_operating_years": 4,
}
EMPLOYED = {**BASE, "employment_type": "employed", "annual_income": 60000, "employer_name": "Acme"}


def statuses(data, results=None, actions=None, schema=V1) -> dict[str, str]:
    res = resolve_requirements(schema, data, results or {}, actions or {})
    return {r.id: r.status for r in res.requirements}


def complete_activities(data: dict, schema=V1, value: Any = None) -> dict:
    """Simulate every runnable activity returning `value` (or a passing result)."""
    results: dict[str, dict[str, ActivityResult]] = {}
    for _ in range(len(schema.fields)):
        res = resolve_requirements(schema, data, results, {})
        ready = [r for r in res.requirements if r.kind == "activity" and r.status == "missing"]
        if not ready:
            break
        for r in ready:
            results.setdefault(r.id, {})[r.fingerprint] = ActivityResult(
                fingerprint=r.fingerprint,
                action_id=f"x:{r.fingerprint}",
                revision=1,
                value=value
                or {
                    "status": "passed" if r.id == "identity_verification" else "affordable",
                    "band": "good",
                },
            )
    return results


class TestResolver:
    def test_initial_requirements(self):
        s = statuses({})
        assert s["applicant_name"] == s["employment_type"] == s["annual_income"] == "missing"
        # Applicability is unknown until employment_type is known: blocked, not missing.
        assert s["employer_name"] == s["business_registration"] == "blocked"
        assert s["identity_verification"] == "blocked"

    def test_conditional_activation(self):
        s = statuses({"employment_type": "self_employed"})
        assert s["business_registration"] == s["business_operating_years"] == "missing"
        assert s["employer_name"] == "not_applicable"

    def test_conditional_deactivation_on_correction(self):
        data = {**SELF_EMPLOYED, "employment_type": "employed"}
        res = resolve_requirements(V1, data, {}, {})
        s = {r.id: r.status for r in res.requirements}
        assert s["business_registration"] == s["business_operating_years"] == "not_applicable"
        assert s["employer_name"] == "missing"
        # Retained values are flagged for retirement and never surface as values.
        assert set(res.retire) == {"business_registration", "business_operating_years"}
        assert "business_registration" not in res.values

    def test_dependency_resolution(self):
        s = statuses({**BASE, "employment_type": "self_employed", "annual_income": 1})
        assert s["identity_verification"] == "missing"  # ready to run
        # Affordability depends on operating years, which apply to the self-employed.
        assert s["affordability_assessment"] == "blocked"
        s = statuses({**BASE, "employment_type": "employed", "annual_income": 1})
        # ...but not to the employed: a non-applicable dependency counts as met.
        assert s["affordability_assessment"] == "missing"

    def test_readiness_false_is_impossible(self):
        res = resolve_requirements(V1, {**EMPLOYED, "consent_to_checks": False}, {}, {})
        identity = res.by_id()["identity_verification"]
        assert identity.status == "blocked" and identity.impossible
        assert res.progress == "impossible"

    def test_invalid_value_is_missing_with_reason(self):
        res = resolve_requirements(V1, {"annual_income": -5}, {}, {})
        income = res.by_id()["annual_income"]
        assert income.status == "missing"
        assert income.reason == "invalid value: must be at least 0"

    def test_completion_and_outcome(self):
        for data in (SELF_EMPLOYED, EMPLOYED):
            results = complete_activities(data)
            # Checks alone are not enough: the confirmation they unlock is required.
            assert not resolve_requirements(V1, data, results, {}).complete
            confirmed = {**data, "final_confirmation": True}
            res = resolve_requirements(V1, confirmed, results, {})
            assert res.complete and res.outcome == "approved"
        confirmed = {**EMPLOYED, "final_confirmation": True}
        results = complete_activities(confirmed, value={"status": "unaffordable"})
        assert resolve_requirements(V1, confirmed, results, {}).outcome == "declined"

    def test_stale_result_does_not_satisfy(self):
        results = complete_activities(EMPLOYED)
        corrected = {**EMPLOYED, "annual_income": 20000}
        res = resolve_requirements(V1, corrected, results, {})
        afford = res.by_id()["affordability_assessment"]
        assert afford.status == "missing"
        assert "inputs changed" in afford.reason
        assert not res.complete
        # Identity inputs didn't change, so its result still counts.
        assert res.by_id()["identity_verification"].status == "satisfied"

    def test_deterministic(self):
        a = resolve_requirements(V1, SELF_EMPLOYED, {}, {})
        b = resolve_requirements(V1, dict(reversed(list(SELF_EMPLOYED.items()))), {}, {})
        assert a == b

    def test_requirements_chain_on_activity_results(self):
        s = statuses(EMPLOYED)
        assert s["credit_check"] == "blocked"  # applicability depends on identity result
        assert s["final_confirmation"] == "not_applicable"
        results = complete_activities(EMPLOYED)
        s = statuses(EMPLOYED, results)
        assert s["credit_check"] == "satisfied"
        assert s["final_confirmation"] == "missing"


class TestPlanner:
    def plan(self, data, actions=None, results=None):
        res = resolve_requirements(V1, data, results or {}, actions or {})
        return plan_actions(V1, res, actions or {})

    def test_initial_actions_are_human_only(self):
        plan = self.plan({})
        assert sorted(plan.new_action_ids) == [
            "collect:annual_income",
            "collect:applicant_name",
            "collect:consent_to_checks",
            "collect:employment_type",
        ]

    def test_independent_activities_planned_together(self):
        plan = self.plan(EMPLOYED)
        kinds = {
            a.parameters.get("activityType")
            for a in plan.actions.values()
            if a.type == "execute_activity"
        }
        assert kinds == {"verify_identity", "assess_affordability"}

    def test_replanning_is_idempotent_and_ids_are_stable(self):
        first = self.plan(EMPLOYED)
        running = {aid: _with_status(a, "running") for aid, a in first.actions.items()}
        second = self.plan(EMPLOYED, actions=running)
        assert second.new_action_ids == []
        again = self.plan(EMPLOYED)
        assert set(again.actions) == set(first.actions)

    def test_blocked_dependencies_plan_nothing(self):
        plan = self.plan({**BASE, "employment_type": "self_employed", "annual_income": 1})
        assert not any(a.startswith("assess_affordability") for a in plan.actions)

    def test_correction_creates_new_action_and_closes_human_ones(self):
        first = self.plan(SELF_EMPLOYED)
        running = {aid: _with_status(a, "running") for aid, a in first.actions.items()}
        corrected = {**SELF_EMPLOYED, "employment_type": "employed"}
        second = self.plan(corrected, actions=running)
        new_afford = [a for a in second.new_action_ids if a.startswith("assess_affordability")]
        assert len(new_afford) == 1 and new_afford[0] not in running
        assert "collect:employer_name" in second.new_action_ids

        pending = self.plan({})
        closed = self.plan({"employment_type": "employed"}, actions=pending.actions)
        assert closed.actions["collect:employment_type"].status == "completed"

    def test_failed_action_is_not_rescheduled(self):
        first = self.plan(EMPLOYED)
        failed = {aid: _with_status(a, "failed") for aid, a in first.actions.items()}
        res = resolve_requirements(V1, EMPLOYED, {}, failed)
        assert res.by_id()["identity_verification"].status == "failed"
        assert res.progress == "action_failed"
        assert plan_actions(V1, res, failed).new_action_ids == []


class TestSubmissionCheck:
    def check(self, values, data=None, field_revisions=None, base_revision=0):
        return check_submission(
            V1, data or {}, {}, {}, field_revisions or {}, base_revision, values
        )

    def test_invalid_values(self):
        check = self.check({"annual_income": "lots", "nope": 1, "identity_verification": {}})
        assert set(check.invalid) == {"annual_income", "nope", "identity_verification"}

    def test_conflict_only_on_fields_changed_since_base(self):
        revs = {"employment_type": 5}
        assert self.check({"annual_income": 1}, field_revisions=revs, base_revision=3).ok
        check = self.check({"employment_type": "employed"}, field_revisions=revs, base_revision=3)
        assert "employment_type" in check.conflicts

    def test_applicability_judged_after_submission(self):
        assert self.check(
            {"employment_type": "self_employed", "business_registration": "BR-000001"}
        ).ok
        check = self.check({"employer_name": "Acme"}, data={"employment_type": "self_employed"})
        assert "not currently applicable" in check.conflicts["employer_name"]


def _with_status(action: PlannedAction, status) -> PlannedAction:
    action.status = status
    return action
