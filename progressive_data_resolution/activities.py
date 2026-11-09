import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from .core.model import Schema
from .core.schema import SchemaError, parse_schema

SCHEMA_DIR = Path(__file__).parent / "schemas"

# Scales the simulated latency of the mock checks. The defaults (3-4s) are long
# enough to watch actions run concurrently and to submit a correction mid-flight.
DELAY_SCALE = float(os.environ.get("PDR_ACTIVITY_DELAY_SCALE", "1"))


@dataclass
class LoadSchemaInput:
    schema_id: str
    schema_version: str


@activity.defn
async def load_schema(params: LoadSchemaInput) -> Schema:
    """Load and validate an immutable schema version.

    The workflow keeps the returned definition in its state (and history), so a
    running application stays pinned to the version it started with even if the
    file changes.
    """
    if not re.fullmatch(r"[a-z_]+", params.schema_id) or not re.fullmatch(
        r"[0-9]+", params.schema_version
    ):
        raise ApplicationError(
            "Malformed schema id or version", type="SchemaNotFound", non_retryable=True
        )
    schema = _read_schema(params.schema_id, params.schema_version)
    # Fail now, not at the end of the form, if a follow-up form is unusable.
    for nxt in schema.next:
        target = _read_schema(nxt.schema_id, nxt.version)
        if nxt.as_namespace not in target.context:
            raise ApplicationError(
                f"{nxt.schema_id} v{nxt.version} does not declare context '{nxt.as_namespace}'",
                type="InvalidSchema",
                non_retryable=True,
            )
    return schema


def _read_schema(schema_id: str, version: str) -> Schema:
    path = SCHEMA_DIR / f"{schema_id}.v{version}.json"
    if not path.exists():
        raise ApplicationError(f"No schema {path.name}", type="SchemaNotFound", non_retryable=True)
    try:
        return parse_schema(json.loads(path.read_text()), AUTOMATED_ACTIVITIES)
    except SchemaError as err:
        raise ApplicationError(
            f"Invalid schema {path.name}", *err.errors, type="InvalidSchema", non_retryable=True
        )


def _demo_knobs(name: str) -> None:
    """Names containing 'flaky' fail twice then succeed; 'broken' always fails."""
    lowered = name.lower()
    if "broken" in lowered:
        raise ApplicationError("Identity provider unavailable", type="ProviderUnavailable")
    if "flaky" in lowered and activity.info().attempt < 3:
        raise ApplicationError(
            f"Identity provider timeout (attempt {activity.info().attempt})", type="ProviderTimeout"
        )


def _digest(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)


@activity.defn
async def verify_identity(inputs: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(3 * DELAY_SCALE)
    name = inputs["applicant_name"]
    _demo_knobs(name)
    if "fraud" in name.lower():
        return {"status": "failed", "reason": "watchlist match"}
    return {"status": "passed", "reference": f"ID-{_digest(name):08x}"}


@activity.defn
async def assess_affordability(inputs: dict[str, Any]) -> dict[str, Any]:
    await asyncio.sleep(4 * DELAY_SCALE)
    income = inputs["annual_income"]
    if inputs["employment_type"] == "employed":
        affordable = income >= 30000
    else:
        affordable = income >= 50000 and inputs.get("business_operating_years", 0) >= 2
    return {
        "status": "affordable" if affordable else "unaffordable",
        "max_loan": int(income * 4) if affordable else 0,
        "assessed_income": income,
    }


@activity.defn
async def fetch_credit_score(inputs: dict[str, Any]) -> dict[str, Any]:
    """Good credit (650-849) unless the name contains 'risky' (520-619, declined)."""
    await asyncio.sleep(2 * DELAY_SCALE)
    name = inputs["applicant_name"]
    if "risky" in name.lower():
        score = 520 + _digest(name) % 100
    else:
        score = 650 + _digest(name) % 200
    return {"score": score, "band": "good" if score >= 620 else "poor"}


@activity.defn
async def prepare_offer(inputs: dict[str, Any]) -> dict[str, Any]:
    """Price a loan: a fixed-rate amortising loan with a term-dependent APR."""
    await asyncio.sleep(2 * DELAY_SCALE)
    amount = inputs["loan_amount"]
    months = int(inputs["term_months"])
    apr = round(5.9 + 0.25 * (months // 12), 2)
    rate = apr / 100 / 12
    monthly = amount * rate / (1 - (1 + rate) ** -months)
    return {
        "apr": apr,
        "monthly_payment": round(monthly, 2),
        "total_repayable": round(monthly * months, 2),
    }


@activity.defn
async def assess_household_affordability(inputs: dict[str, Any]) -> dict[str, Any]:
    """Re-assess with the applicant's income (from the application) plus household income."""
    await asyncio.sleep(2 * DELAY_SCALE)
    household = (
        inputs["application.annual_income"]
        + inputs["other_income"]
        + inputs.get("co_applicant_income", 0)
    )
    affordable = household >= 40000
    return {
        "status": "affordable" if affordable else "unaffordable",
        "household_income": household,
        "max_loan": int(household * 3) if affordable else 0,
    }


@activity.defn
async def schedule_disbursement(inputs: dict[str, Any]) -> dict[str, Any]:
    """Book the payout of an accepted offer."""
    await asyncio.sleep(2 * DELAY_SCALE)
    account = inputs.get("payout_account") or inputs["offer.repayment_account"]
    amount = inputs["offer.loan_amount"]
    return {
        "reference": f"DSB-{_digest(f'{account}:{amount}'):08x}",
        "amount": amount,
        "account": account,
        "settles_in_business_days": 3,
    }


# The fixed registry of activities a schema may name. Schema content selects from
# this list; it can never introduce new code.
AUTOMATED_ACTIVITIES: dict[str, Any] = {
    "verify_identity": verify_identity,
    "assess_affordability": assess_affordability,
    "fetch_credit_score": fetch_credit_score,
    "prepare_offer": prepare_offer,
    "assess_household_affordability": assess_household_affordability,
    "schedule_disbursement": schedule_disbursement,
}
