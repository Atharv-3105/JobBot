"""
Shared structured-metrics collector for the test audit.
Every test (mock-based or real-LLM) appends one record via `record()`.
At the end, `dump_and_summarize()` writes the raw records to
tests/metrics_output/run_<timestamp>.json and returns computed aggregates.
"""
import json
import os
import time
import statistics
from datetime import datetime, timezone
from typing import Any, Dict, List

_records: List[Dict[str, Any]] = []

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "metrics_output")


def record(**fields) -> None:
    """
        Append one structured record. Common fields used across this audit:
        test_name, mode ("mock"|"real"), profile_name, jd_label, provider,
        latency_ms, outcome ("pdf_generated"|"dropped"|"error"),
        drop_reason (skill_blocked/quantifier_blocked/retention_too_low/
        xml_malformed/provider_exhausted/compile_failed/latex_injection_blocked/None),
        retention_rate, blocked_sections (list), timestamp
    """
    fields.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    _records.append(fields)


def reset() -> None:
    _records.clear()


def get_records() -> List[Dict[str, Any]]:
    return list(_records)


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    k = (len(values) - 1) * pct
    f = int(k)
    c = min(f + 1, len(values) - 1)
    if f == c:
        return values[f]
    return values[f] + (values[c] - values[f]) * (k - f)


def compute_aggregates() -> Dict[str, Any]:
    total = len(_records)
    outcomes = {}
    drop_reasons = {}
    latencies_all = []
    latencies_by_provider: Dict[str, List[float]] = {}
    profile_jd_grid: Dict[str, Dict[str, str]] = {}

    for r in _records:
        outcome = r.get("outcome", "unknown")
        outcomes[outcome] = outcomes.get(outcome, 0) + 1

        reason = r.get("drop_reason")
        if reason:
            drop_reasons[reason] = drop_reasons.get(reason, 0) + 1

        lat = r.get("latency_ms")
        if lat is not None:
            latencies_all.append(lat)
            prov = r.get("provider", "unknown")
            latencies_by_provider.setdefault(prov, []).append(lat)

        profile = r.get("profile_name")
        jd = r.get("jd_label")
        if profile and jd:
            profile_jd_grid.setdefault(profile, {})[jd] = outcome

    def _lat_stats(vals: List[float]) -> Dict[str, float]:
        if not vals:
            return {"count": 0, "mean_ms": 0, "p50_ms": 0, "p95_ms": 0}
        return {
            "count": len(vals),
            "mean_ms": round(statistics.mean(vals), 1),
            "p50_ms": round(_percentile(vals, 0.5), 1),
            "p95_ms": round(_percentile(vals, 0.95), 1),
        }

    return {
        "total_records": total,
        "outcomes": outcomes,
        "drop_reasons": drop_reasons,
        "latency_overall": _lat_stats(latencies_all),
        "latency_by_provider": {p: _lat_stats(v) for p, v in latencies_by_provider.items()},
        "profile_jd_outcome_grid": profile_jd_grid,
    }


def dump_and_summarize(run_label: str = "run") -> Dict[str, Any]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTPUT_DIR, f"{run_label}_{ts}.json")

    aggregates = compute_aggregates()
    payload = {"records": _records, "aggregates": aggregates}

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)

    aggregates["_output_path"] = out_path
    return aggregates
