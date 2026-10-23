"""Conservative local decision diagnostics, not an inferred Engine success flag."""

from __future__ import annotations

from typing import Any

SCHEMA = "deepdeck-decision-audit/v1"


def summarize_decisions(
    frames: list[dict[str, Any]], *, unmatched_feedback: int = 0
) -> dict[str, Any]:
    decisions = [frame for frame in frames if isinstance(frame.get("agentDecision"), dict)]
    confirmed = sum(
        frame["agentDecision"].get("resolution") in {"confirmed", "fallback"} for frame in decisions
    )
    fallbacks = sum(bool(frame.get("fallbackReason")) for frame in decisions)
    errors = sum(frame["agentDecision"].get("status") == "error" for frame in decisions)
    cancelled = sum(frame["agentDecision"].get("status") == "cancelled" for frame in decisions)
    durations = [
        float(frame["agentDecision"]["durationMs"])
        for frame in decisions
        if isinstance(frame["agentDecision"].get("durationMs"), (int, float))
    ]
    policy_durations = [
        float(frame["agentDecision"]["policyDurationMs"])
        for frame in decisions
        if isinstance(frame["agentDecision"].get("policyDurationMs"), (int, float))
    ]
    complete = bool(decisions) and confirmed == len(decisions) and not unmatched_feedback
    return {
        "schemaVersion": SCHEMA,
        "status": "degraded"
        if fallbacks or errors or cancelled
        else "confirmed"
        if complete
        else "unconfirmed",
        "recordedDecisions": len(decisions),
        "engineConfirmations": confirmed,
        "confirmedFallbacks": fallbacks,
        "handlerErrors": errors,
        "cancelledHandlers": cancelled,
        "unconfirmedDecisions": len(decisions) - confirmed,
        "unmatchedFeedback": unmatched_feedback,
        "meanDurationMs": round(sum(durations) / len(durations), 3) if durations else None,
        "maxDurationMs": round(max(durations), 3) if durations else None,
        "maxPolicyDurationMs": round(max(policy_durations), 3) if policy_durations else None,
        "timingScope": "agent-handler-and-recorder-only",
        "reasonSamples": list(
            dict.fromkeys(
                str(frame.get("fallbackReason") or frame["agentDecision"].get("error"))
                for frame in decisions
                if frame.get("fallbackReason") or frame["agentDecision"].get("error")
            )
        )[:3],
    }


def audit_status(value: Any) -> str:
    if not isinstance(value, dict) or value.get("schemaVersion") != SCHEMA:
        return "unknown"
    status = value.get("status")
    return (
        status
        if isinstance(status, str) and status in {"confirmed", "degraded", "unconfirmed"}
        else "unknown"
    )
