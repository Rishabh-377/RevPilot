"""
RevPilot API Routes
===================

REST API endpoints powering the RevPilot Financial Controller and Dashboard.
All metrics and decision chains are read directly from live services and benchmark logs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.agents.diagnosis import DiagnosisAgent
from backend.models.schemas import (
    AuditEvent,
    DiagnosisResult,
    PaymentFailureEvent,
)
from backend.services.chaos import ChaosSuite
from backend.services.pipeline import EventPipelineResult, RevPilotPipeline
from backend.simulator.event_generator import EventGenerator

router = APIRouter(prefix="/api/v1", tags=["revpilot"])

# Global shared pipeline instance for API requests
_pipeline = RevPilotPipeline(seed=20260821)
_diagnosis_agent = DiagnosisAgent()
_chaos_suite = ChaosSuite(enabled=True)


# ---------------------------------------------------------------------------
# Core Pipeline Endpoints
# ---------------------------------------------------------------------------


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0", "engine": "RevPilot-v2026"}


@router.post("/events", response_model=DiagnosisResult)
async def ingest_event(event: PaymentFailureEvent) -> DiagnosisResult:
    """Ingest a payment failure event and execute root-cause diagnosis."""
    raw_error = event.raw_gateway_error or event.failure_code or ""
    return _diagnosis_agent.diagnose_raw(
        raw_error=raw_error,
        event_id=event.event_id,
        metadata=event.metadata,
    )


@router.post("/recover")
async def trigger_recovery(event: PaymentFailureEvent) -> EventPipelineResult:
    """Trigger the full 10-stage recovery pipeline for a payment failure event."""
    return _pipeline.process_event(event)


@router.get("/audit/{event_id}", response_model=list[AuditEvent])
async def get_audit_trail(event_id: str) -> list[AuditEvent]:
    """Retrieve the full immutable audit trail for a specific payment event."""
    trail = _pipeline.audit_service.get_trail(event_id)
    if not trail:
        # Check if present in persisted audit file
        audit_file = Path("output/audit_log.jsonl")
        if audit_file.exists():
            matched = []
            with open(audit_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        if data.get("event_id") == event_id:
                            matched.append(AuditEvent(**data))
            if matched:
                return matched
        raise HTTPException(status_code=404, detail=f"No audit trail found for event {event_id}")
    return trail


@router.get("/benchmark")
async def get_benchmark_results() -> dict[str, Any]:
    """Retrieve the latest benchmark metrics and comparison data."""
    comp_file = Path("output/comparison.json")
    if comp_file.exists():
        with open(comp_file, encoding="utf-8") as f:
            return json.load(f)

    # Fallback: run quick benchmark
    gen = EventGenerator(seed=20260821, n=500)
    events = gen.generate(n=500, seed=20260821)
    summary = _pipeline.process_batch(events, batch_id="api_benchmark_500")
    return summary.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Dashboard Dedicated Endpoints
# ---------------------------------------------------------------------------


# Metric definitions for full end-to-end traceability
METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "total_events": {
        "name": "Total Events",
        "definition": "Count of incoming failed payment events in the evaluation batch",
        "source": "EventGenerator / PaymentFailureEvent stream",
    },
    "recovery_rate": {
        "name": "Recovery Rate",
        "definition": "Fraction of processed failed payments successfully recovered",
        "formula": "successful_recoveries / processed_events",
        "source": "OutcomeEngine / PipelineBatchSummary",
    },
    "gross_recovered_revenue_inr": {
        "name": "Gross Recovered Revenue",
        "definition": "Total transaction principal in INR successfully recovered",
        "formula": "sum(amount_recovered)",
        "source": "SimOutcome.recovered_value",
    },
    "action_cost_inr": {
        "name": "Action Execution Cost",
        "definition": "Direct gateway and network execution costs incurred for recovery actions",
        "formula": "sum(action_cost)",
        "source": "SimOutcome.action_cost",
    },
    "friction_cost_units": {
        "name": "Friction Cost",
        "definition": "Quantified customer UX friction cost from invasive recovery interventions",
        "formula": "sum(friction_cost)",
        "source": "SimOutcome.friction_cost",
    },
    "net_recovered_revenue_inr": {
        "name": "Net Recovered Revenue",
        "definition": "Net economic value generated after subtracting execution and friction costs",
        "formula": "gross_recovered_revenue - (action_cost + friction_cost)",
        "source": "SimOutcome.net_recovered",
    },
    "blocked_by_guardrails": {
        "name": "Blocked Unsafe Attempts",
        "definition": "Recovery attempts halted unconditionally by deterministic financial guardrails",
        "source": "GuardrailEngine / GuardrailDecision",
    },
    "human_review": {
        "name": "Human Review Escalations",
        "definition": "Cases safely routed to human operators due to high value or ambiguous risk",
        "source": "GuardrailVerdict.escalate",
    },
    "unsafe_executions": {
        "name": "Unsafe Executions",
        "definition": "Automated retries executed on known fraudulent or duplicate transactions",
        "source": "GuardrailEngine / Simulator Verification",
    },
    "unresolved_exceptions": {
        "name": "Unresolved Exceptions",
        "definition": "Unhandled system exceptions or pipeline crashes during batch execution",
        "source": "ExceptionRecord registry",
    },
}


@router.get("/dashboard/overview")
async def get_dashboard_overview() -> dict[str, Any]:
    """Provide aggregated metrics and metric definitions for the CONTROL ROOM view."""
    rev_path = Path("output/revpilot_metrics.json")
    base_path = Path("output/baseline_metrics.json")
    comp_path = Path("output/comparison.json")

    rev_metrics: dict[str, Any] = {}
    base_metrics: dict[str, Any] = {}
    comp_metrics: dict[str, Any] = {}

    if rev_path.exists() and base_path.exists() and comp_path.exists():
        with open(rev_path, encoding="utf-8") as f:
            rev_metrics = json.load(f)
        with open(base_path, encoding="utf-8") as f:
            base_metrics = json.load(f)
        with open(comp_path, encoding="utf-8") as f:
            comp_metrics = json.load(f)
    else:
        # Compute on the fly via actual simulation if files not pre-generated
        from scripts.run_benchmark import (
            generate_comparison_metrics,
            run_revpilot,
            run_static_baseline,
        )

        gen = EventGenerator(seed=20260821, n=500)
        events = gen.generate(n=500, seed=20260821)
        base_metrics = run_static_baseline(events, seed=20260821)
        rev_metrics, _, _, _ = run_revpilot(events, seed=20260821)
        comp_metrics = generate_comparison_metrics(base_metrics, rev_metrics)

    return {
        "status": "online",
        "environment_type": "SYNTHETIC SIMULATION",
        "metric_definitions": METRIC_DEFINITIONS,
        "revpilot": rev_metrics,
        "baseline": base_metrics,
        "comparison": comp_metrics,
    }


@router.get("/dashboard/transactions")
async def get_dashboard_transactions(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None),
) -> dict[str, Any]:
    """Provide transaction records with detailed decision chains for DECISION EXPLORER."""
    # Generate reproducible batch for explorer
    gen = EventGenerator(seed=20260821, n=150)
    events = gen.generate(n=150, seed=20260821)

    explorer_pipeline = RevPilotPipeline(seed=20260821)
    results = [explorer_pipeline.process_event(ev) for ev in events]

    if status_filter:
        results = [
            r
            for r in results
            if r.status == status_filter
            or (r.guardrail_verdict and r.guardrail_verdict.value == status_filter)
        ]

    paged = results[offset : offset + limit]
    items = []
    for r in paged:
        d = r.model_dump(mode="json")
        d["amount"] = r.event.amount if r.event else r.amount_recovered
        d["payment_method"] = (
            r.event.payment_method.value
            if r.event and hasattr(r.event.payment_method, "value")
            else (str(r.event.payment_method) if r.event else "UPI")
        )
        d["failure_code"] = (
            r.event.failure_code
            if r.event
            else (r.failure_class.value if r.failure_class else "UNKNOWN")
        )
        items.append(d)

    return {
        "total": len(results),
        "limit": limit,
        "offset": offset,
        "items": items,
    }


@router.get("/dashboard/transaction/{event_id}")
async def get_transaction_detail(event_id: str) -> dict[str, Any]:
    """Retrieve full decision trace and audit chain for a single transaction."""
    gen = EventGenerator(seed=20260821, n=200)
    events = gen.generate(n=200, seed=20260821)

    target_event = next((e for e in events if e.event_id == event_id), None)
    if not target_event:
        raise HTTPException(status_code=404, detail=f"Transaction {event_id} not found")

    p = RevPilotPipeline(seed=20260821)
    result = p.process_event(target_event)
    return result.model_dump(mode="json")


@router.get("/dashboard/learning")
async def get_dashboard_learning() -> dict[str, Any]:
    """Provide posterior estimates, arm statistics, and non-stationary shift records for LEARNING view.

    If non-stationary experiment was not run, non_stationary_shift is None.
    Never fabricates placeholder statistics.
    """
    arms_data = {}
    for ctx, act_dict in _pipeline.bandit.state.arms.items():
        arms_data[ctx] = {
            act: {
                "alpha": arm.alpha,
                "beta": arm.beta,
                "posterior_mean": round(arm.posterior_mean, 4),
                "attempt_count": arm.attempt_count,
                "success_count": arm.success_count,
                "failure_count": arm.failure_count,
            }
            for act, arm in act_dict.items()
        }

    ns_file = Path("output/non_stationary_shift.json")
    ns_report = None
    if ns_file.exists():
        try:
            with open(ns_file, encoding="utf-8") as f:
                ns_report = json.load(f)
        except Exception:
            ns_report = None

    return {
        "candidate_actions": _pipeline.bandit.candidate_actions,
        "arms": arms_data,
        "non_stationary_shift": ns_report,
    }


@router.get("/dashboard/exceptions")
async def get_dashboard_exceptions() -> list[dict[str, Any]]:
    """Provide real unresolved / handled exception cases for EXCEPTIONS view.

    Reads actual exceptions from output/exceptions.json.
    Returns an empty list if zero real exceptions were produced.
    Never fabricates demonstration exceptions.
    """
    exc_file = Path("output/exceptions.json")
    if exc_file.exists():
        try:
            with open(exc_file, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass

    return []



@router.post("/dashboard/chaos/run")
async def run_dashboard_chaos() -> dict[str, Any]:
    """Execute the 10-scenario Chaos Engineering suite and return safety verification results."""
    suite = ChaosSuite(enabled=True)
    results = suite.run_all(seed=20260821)
    all_safe = all(r.safe for r in results)
    return {
        "all_safe": all_safe,
        "scenarios_passed": sum(1 for r in results if r.safe),
        "total_scenarios": len(results),
        "scenarios": [r.model_dump(mode="json") for r in results],
    }


@router.get("/dashboard/audit")
async def get_dashboard_audit(
    stage: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """Retrieve full audit log entries for AUDIT view."""
    all_audits = _pipeline.audit_service.get_all()
    if not all_audits:
        # Read from output/audit_log.jsonl
        audit_file = Path("output/audit_log.jsonl")
        if audit_file.exists():
            records = []
            with open(audit_file, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        records.append(json.loads(line))
            all_audits = [AuditEvent(**r) for r in records]

    filtered = all_audits
    if stage:
        filtered = [a for a in filtered if a.stage.lower() == stage.lower()]
    if status:
        filtered = [a for a in filtered if a.status.lower() == status.lower()]

    return [a.model_dump(mode="json") for a in filtered[:limit]]


# ---------------------------------------------------------------------------
# Judge Mode Demo Endpoints
# ---------------------------------------------------------------------------


@router.post("/judge/reset")
async def reset_judge_pipeline() -> dict[str, str]:
    """Reset the pipeline state for a clean, deterministic demo run."""
    global _pipeline
    _pipeline = RevPilotPipeline(seed=20260821)
    return {"status": "success", "message": "Judge pipeline state reset successfully."}


@router.post("/judge/run_first")
async def run_first_transaction() -> dict[str, Any]:
    """Execute the first deterministic transaction through the live pipeline."""
    # Build a deterministic payment failure event
    event = PaymentFailureEvent(
        event_id="evt_judge_001",
        payment_id="pay_judge_999",
        merchant_id="merch_judge",
        amount=4500.0,
        currency="INR",
        payment_method="credit_card",
        failure_code="bank response timed out",
        raw_gateway_error="bank response timed out after 30s",
        attempt_number=1,
    )

    # Capture bandit arm state BEFORE the transaction is processed (for Scene 8)
    context_key = "TIMEOUT_TRANSIENT+MID" # 4500 is MID value tier
    bandit_state_before = {}
    for act in _pipeline.bandit.candidate_actions:
        arm = _pipeline.bandit.state.get_arm(context_key, act)
        bandit_state_before[act] = {
            "alpha": arm.alpha,
            "beta": arm.beta,
            "posterior_mean": round(arm.posterior_mean, 4),
        }

    # Execute the event through the real pipeline
    result = _pipeline.process_event(event)

    # Capture bandit arm state AFTER the transaction is processed (for Scene 8)
    bandit_state_after = {}
    for act in _pipeline.bandit.candidate_actions:
        arm = _pipeline.bandit.state.get_arm(context_key, act)
        bandit_state_after[act] = {
            "alpha": arm.alpha,
            "beta": arm.beta,
            "posterior_mean": round(arm.posterior_mean, 4),
        }

    # Retrieve the audit trail for this event
    audit_trail = _pipeline.audit_service.get_trail("evt_judge_001")

    return {
        "event": event.model_dump(mode="json"),
        "pipeline_result": result.model_dump(mode="json"),
        "bandit_before": bandit_state_before,
        "bandit_after": bandit_state_after,
        "audit_trail": [a.model_dump(mode="json") for a in audit_trail],
    }


@router.post("/judge/run_second")
async def run_second_transaction() -> dict[str, Any]:
    """Replay the duplicate transaction through the pipeline to demonstrate the guardrail block."""
    event = PaymentFailureEvent(
        event_id="evt_judge_001_replay",
        payment_id="pay_judge_999", # Same payment_id
        merchant_id="merch_judge",
        amount=4500.0,
        currency="INR",
        payment_method="credit_card",
        failure_code="bank response timed out",
        raw_gateway_error="bank response timed out after 30s",
        attempt_number=1, # Same attempt_number
    )

    result = _pipeline.process_event(event)
    audit_trail = _pipeline.audit_service.get_trail("evt_judge_001_replay")

    return {
        "event": event.model_dump(mode="json"),
        "pipeline_result": result.model_dump(mode="json"),
        "audit_trail": [a.model_dump(mode="json") for a in audit_trail],
    }
