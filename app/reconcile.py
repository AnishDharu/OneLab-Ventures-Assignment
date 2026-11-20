"""
Reconciliation engine for payments vs settlements.
Uses Decimal for all money arithmetic — no floats.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

# ── tolerance for rounding matches ────────────────────────────────────────────
ROUNDING_TOLERANCE = Decimal("0.01")
MONTH = "2026-10"
NEXT_MONTH = "2026-11"


# ══════════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════════

def _to_decimal(val: Any) -> Decimal:
    try:
        return Decimal(str(val))
    except InvalidOperation:
        return Decimal("0")


def _gap(
    gap_type: str,
    ids: dict[str, Any],
    amount_impact: Decimal,
    explanation: str,
    confidence: float,
) -> dict:
    return {
        "type": gap_type,
        "ids": ids,
        "amount_impact": str(amount_impact),
        "explanation": explanation,
        "confidence": confidence,
    }


def _is_last_2_business_days(date_str: str) -> bool:
    """Return True if the date is in the last 2 business days of October 2026."""
    from datetime import date, timedelta
    try:
        d = date.fromisoformat(date_str[:10])
    except ValueError:
        return False
    if d.month != 10 or d.year != 2026:
        return False
    # Find last 2 business days of Oct 2026
    end = date(2026, 10, 31)
    biz_days = []
    cur = end
    while len(biz_days) < 2:
        if cur.weekday() < 5:
            biz_days.append(cur)
        cur -= timedelta(days=1)
    return d in biz_days


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — detect_duplicates
# ══════════════════════════════════════════════════════════════════════════════

def detect_duplicates(
    txn_df: pd.DataFrame,
    stl_df: pd.DataFrame,
) -> tuple[list[dict], pd.DataFrame, pd.DataFrame]:
    """
    Find exact duplicate rows in each dataset.
    Returns (gap_records, deduplicated_txn_df, deduplicated_stl_df).
    """
    gaps: list[dict] = []

    # ── transactions: same txn_id ──────────────────────────────────────────────
    dup_txn_mask = txn_df.duplicated(subset=["txn_id"], keep="first")
    dup_txns = txn_df[dup_txn_mask]
    for _, row in dup_txns.iterrows():
        amt = _to_decimal(row["amount"])
        gaps.append(_gap(
            "DUPLICATE",
            {"txn_id": row["txn_id"], "dataset": "transactions"},
            amt,
            f"Transaction {row['txn_id']} appears more than once in transactions.csv. "
            f"The duplicate row inflates the platform total by {amt}.",
            0.99,
        ))

    # ── settlements: same settlement_id ───────────────────────────────────────
    dup_stl_mask = stl_df.duplicated(subset=["settlement_id"], keep="first")
    dup_stls = stl_df[dup_stl_mask]
    for _, row in dup_stls.iterrows():
        amt = _to_decimal(row["amount"])
        gaps.append(_gap(
            "DUPLICATE",
            {"settlement_id": row["settlement_id"], "txn_ref": row["txn_ref"], "dataset": "settlements"},
            -amt,  # negative: duplicate settlement inflates bank_total, reducing platform-bank difference
            f"Settlement {row['settlement_id']} appears more than once in settlements.csv. "
            f"The duplicate inflates the bank total by {amt}.",
            0.99,
        ))

    # Deduplicate before further processing
    clean_txn = txn_df.drop_duplicates(subset=["txn_id"], keep="first").copy()
    clean_stl = stl_df.drop_duplicates(subset=["settlement_id"], keep="first").copy()

    return gaps, clean_txn, clean_stl


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — match_exact
# ══════════════════════════════════════════════════════════════════════════════

def match_exact(
    txn_df: pd.DataFrame,
    stl_df: pd.DataFrame,
) -> tuple[list[str], pd.DataFrame, pd.DataFrame]:
    """
    Match transactions to settlements where txn_id == txn_ref AND
    amount is equal (Decimal comparison).
    Returns (matched_txn_ids, unmatched_txn_df, unmatched_stl_df).
    """
    # Guard: empty DataFrames have no columns — return everything unmatched
    if txn_df.empty or stl_df.empty or "txn_ref" not in stl_df.columns:
        return [], txn_df.copy(), stl_df.copy()

    # Build settlement lookup by txn_ref -> amount (Decimal)
    stl_lookup: dict[str, Decimal] = {}
    stl_id_lookup: dict[str, str] = {}
    for _, row in stl_df.iterrows():
        ref = str(row["txn_ref"])
        stl_lookup[ref] = _to_decimal(row["amount"])
        stl_id_lookup[ref] = str(row["settlement_id"])

    matched_txn_ids: list[str] = []
    matched_stl_refs: list[str] = []

    for _, row in txn_df.iterrows():
        tid = str(row["txn_id"])
        if tid in stl_lookup:
            txn_amt = _to_decimal(row["amount"])
            stl_amt = stl_lookup[tid]
            if txn_amt == stl_amt:
                matched_txn_ids.append(tid)
                matched_stl_refs.append(tid)

    unmatched_txn = txn_df[~txn_df["txn_id"].isin(matched_txn_ids)].copy()
    unmatched_stl = stl_df[~stl_df["txn_ref"].isin(matched_stl_refs)].copy()

    return matched_txn_ids, unmatched_txn, unmatched_stl


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — match_with_tolerance
# ══════════════════════════════════════════════════════════════════════════════

def match_with_tolerance(
    unmatched_txn: pd.DataFrame,
    unmatched_stl: pd.DataFrame,
) -> tuple[list[dict], list[str], list[str], pd.DataFrame, pd.DataFrame]:
    """
    Match remaining transactions where txn_id == txn_ref but amount
    differs by < ROUNDING_TOLERANCE. Flags as ROUNDING.
    Returns (gap_records, matched_txn_ids, matched_stl_ids,
             still_unmatched_txn, still_unmatched_stl).
    """
    gaps: list[dict] = []
    matched_txn_ids: list[str] = []
    matched_stl_ids: list[str] = []
    cumulative_drift = Decimal("0")

    # Guard: empty DataFrames — nothing to match
    if unmatched_txn.empty or unmatched_stl.empty or "txn_ref" not in unmatched_stl.columns:
        return gaps, matched_txn_ids, matched_stl_ids, unmatched_txn.copy(), unmatched_stl.copy()

    stl_lookup: dict[str, tuple[Decimal, str]] = {}
    for _, row in unmatched_stl.iterrows():
        ref = str(row["txn_ref"])
        stl_lookup[ref] = (_to_decimal(row["amount"]), str(row["settlement_id"]))

    for _, row in unmatched_txn.iterrows():
        tid = str(row["txn_id"])
        if tid in stl_lookup:
            stl_amt, stl_id = stl_lookup[tid]
            txn_amt = _to_decimal(row["amount"])
            diff = txn_amt - stl_amt
            if abs(diff) < ROUNDING_TOLERANCE and diff != Decimal("0"):
                cumulative_drift += diff
                gaps.append(_gap(
                    "ROUNDING",
                    {"txn_id": tid, "settlement_id": stl_id},
                    diff,
                    f"Transaction {tid}: platform amount {txn_amt} vs bank {stl_amt}. "
                    f"Difference of {diff} is within tolerance but contributes to aggregate drift. "
                    f"Running cumulative drift: {cumulative_drift}.",
                    0.95,
                ))
                matched_txn_ids.append(tid)
                matched_stl_ids.append(tid)

    still_unmatched_txn = unmatched_txn[~unmatched_txn["txn_id"].isin(matched_txn_ids)].copy()
    still_unmatched_stl = unmatched_stl[~unmatched_stl["txn_ref"].isin(matched_stl_ids)].copy()

    return gaps, matched_txn_ids, matched_stl_ids, still_unmatched_txn, still_unmatched_stl


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — classify_unmatched
# ══════════════════════════════════════════════════════════════════════════════

def classify_unmatched(
    unmatched_txn: pd.DataFrame,
    next_month_stl: pd.DataFrame | None = None,
) -> list[dict]:
    """
    For each unmatched transaction:
    - TIMING if a settlement exists in next-month data OR transaction date is
      within 2 business days of month end.
    - MISSING otherwise.
    """
    gaps: list[dict] = []

    # Build next-month settlement lookup by txn_ref
    next_month_refs: set[str] = set()
    if next_month_stl is not None and not next_month_stl.empty:
        next_month_refs = set(next_month_stl["txn_ref"].astype(str))

    for _, row in unmatched_txn.iterrows():
        tid = str(row["txn_id"])
        amt = _to_decimal(row["amount"])
        ts  = str(row.get("timestamp", ""))
        txn_type = str(row.get("type", "payment"))

        if txn_type == "refund":
            # Orphan refunds are handled separately; skip here
            continue

        if tid in next_month_refs:
            gaps.append(_gap(
                "TIMING",
                {"txn_id": tid},
                amt,
                f"Transaction {tid} (amount {amt}) has no settlement in October but a matching "
                f"settlement exists in November. This is a timing gap — the settlement crossed "
                f"the month boundary.",
                0.97,
            ))
        elif _is_last_2_business_days(ts):
            gaps.append(_gap(
                "TIMING",
                {"txn_id": tid},
                amt,
                f"Transaction {tid} (amount {amt}) occurred in the last 2 business days of October "
                f"and has no settlement yet. Expected to settle in November (1-2 business day lag).",
                0.90,
            ))
        else:
            gaps.append(_gap(
                "MISSING",
                {"txn_id": tid},
                amt,
                f"Transaction {tid} (amount {amt}) has no matching settlement and is not near "
                f"month end. This may indicate a failed settlement or data error.",
                0.80,
            ))

    return gaps


# ══════════════════════════════════════════════════════════════════════════════
# Step 5 — detect_orphan_refunds
# ══════════════════════════════════════════════════════════════════════════════

def detect_orphan_refunds(txn_df: pd.DataFrame) -> list[dict]:
    """
    Find refunds whose original_txn_id does not appear in transactions.
    """
    gaps: list[dict] = []
    if txn_df.empty or "txn_id" not in txn_df.columns:
        return gaps
    all_txn_ids = set(txn_df["txn_id"].astype(str))

    refunds = txn_df[txn_df["type"] == "refund"]
    for _, row in refunds.iterrows():
        orig_id = str(row.get("original_txn_id", ""))
        if not orig_id or orig_id.strip() == "":
            continue
        if orig_id not in all_txn_ids:
            amt = _to_decimal(row["amount"])
            gaps.append(_gap(
                "ORPHAN_REFUND",
                {"refund_txn_id": str(row["txn_id"]), "missing_original_txn_id": orig_id},
                amt,
                f"Refund {row['txn_id']} references original transaction {orig_id}, "
                f"which does not exist in the transactions dataset. This refund has no "
                f"corresponding original payment and cannot be validated.",
                0.99,
            ))

    return gaps


# ══════════════════════════════════════════════════════════════════════════════
# Main reconcile function
# ══════════════════════════════════════════════════════════════════════════════

def reconcile(
    txn_df: pd.DataFrame,
    stl_df: pd.DataFrame,
    next_month_stl_df: pd.DataFrame | None = None,
) -> dict:
    """
    Full reconciliation pipeline. Returns a summary dict with all gaps
    and the accounting equation:
        platform_total - bank_total = sum(gap_amounts) + unexplained
    """
    all_gaps: list[dict] = []

    # ── Step 1: Detect duplicates (and deduplicate for further analysis) ───────
    dup_gaps, clean_txn, clean_stl = detect_duplicates(txn_df, stl_df)
    all_gaps.extend(dup_gaps)

    # ── Step 2: Detect orphan refunds on the FULL (pre-dedup) dataset ─────────
    # (uses deduplicated txn to avoid double-counting)
    orphan_gaps = detect_orphan_refunds(clean_txn)
    all_gaps.extend(orphan_gaps)

    # ── Step 3: Exact matching ─────────────────────────────────────────────────
    matched_exact_ids, unmatched_txn, unmatched_stl = match_exact(clean_txn, clean_stl)

    # ── Step 4: Tolerance matching (rounding) ─────────────────────────────────
    rounding_gaps, _, _, unmatched_txn, unmatched_stl = match_with_tolerance(
        unmatched_txn, unmatched_stl
    )
    all_gaps.extend(rounding_gaps)

    # ── Step 5: Classify remaining unmatched transactions ─────────────────────
    # Exclude orphan refunds from timing/missing classification
    orphan_refund_ids = {
        g["ids"]["refund_txn_id"] for g in orphan_gaps
    }
    if unmatched_txn.empty or "txn_id" not in unmatched_txn.columns:
        unmatched_txn_non_orphan = unmatched_txn.copy()
    else:
        unmatched_txn_non_orphan = unmatched_txn[
            ~unmatched_txn["txn_id"].astype(str).isin(orphan_refund_ids)
        ]
    timing_missing_gaps = classify_unmatched(unmatched_txn_non_orphan, next_month_stl_df)
    all_gaps.extend(timing_missing_gaps)

    # ── Accounting equation ────────────────────────────────────────────────────
    # Platform total = sum of ALL transactions (including duplicates)
    platform_total = sum(_to_decimal(r["amount"]) for _, r in txn_df.iterrows())
    # Bank total = sum of ALL settlements (including duplicates)
    bank_total = sum(_to_decimal(r["amount"]) for _, r in stl_df.iterrows())
    difference = platform_total - bank_total

    # Sum gaps by type (amount_impact can be negative for rounding).
    # ORPHAN_REFUND is a data-quality flag: when the orphan refund has a
    # matching settlement it is already accounted for in the exact-match step
    # and contributes zero to the platform-bank numerical difference.
    # Therefore exclude ORPHAN_REFUND from the accounting equation totals.
    ACCOUNTING_GAP_TYPES = {"TIMING", "ROUNDING", "DUPLICATE", "MISSING"}
    gap_by_type: dict[str, Decimal] = defaultdict(Decimal)
    for g in all_gaps:
        gap_by_type[g["type"]] += _to_decimal(g["amount_impact"])

    total_explained = sum(
        v for t, v in gap_by_type.items() if t in ACCOUNTING_GAP_TYPES
    )
    unexplained = difference - total_explained

    # ── Build matched count stats ──────────────────────────────────────────────
    total_txns = len(txn_df)
    total_stls = len(stl_df)
    exact_matched = len(matched_exact_ids)
    rounding_matched = len(rounding_gaps)

    return {
        "platform_total": str(platform_total),
        "bank_total": str(bank_total),
        "difference": str(difference),
        "gap_by_type": {k: str(v) for k, v in gap_by_type.items()},
        "total_explained": str(total_explained),
        "unexplained": str(unexplained),
        "gaps": all_gaps,
        "stats": {
            "total_transactions": total_txns,
            "total_settlements": total_stls,
            "exact_matched": exact_matched,
            "rounding_matched": rounding_matched,
            "unmatched_transactions": len(unmatched_txn),
            "unmatched_settlements": len(unmatched_stl),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Ground truth recall
# ══════════════════════════════════════════════════════════════════════════════

def compute_recall(result: dict, ground_truth: dict) -> dict:
    """
    Compare detected gaps against planted ground truth.
    Returns per-type hit/miss information.
    """
    detected_types = set(g["type"] for g in result["gaps"])
    planted_types  = set(g["type"] for g in ground_truth.get("gaps", []))

    recall_rows = []
    for planted in ground_truth.get("gaps", []):
        ptype = planted["type"]
        hit = ptype in detected_types
        # Count detected items of this type
        detected_count = sum(1 for g in result["gaps"] if g["type"] == ptype)
        recall_rows.append({
            "planted_type": ptype,
            "planted_description": planted["description"],
            "planted_amount_impact": planted["amount_impact"],
            "detected": hit,
            "detected_count": detected_count,
        })

    # Any types detected but not planted
    extra_types = detected_types - planted_types
    for etype in extra_types:
        extra_count = sum(1 for g in result["gaps"] if g["type"] == etype)
        recall_rows.append({
            "planted_type": etype,
            "planted_description": "NOT PLANTED (false positive)",
            "planted_amount_impact": "0",
            "detected": True,
            "detected_count": extra_count,
        })

    overall_recall = sum(1 for r in recall_rows if r["detected"] and "NOT PLANTED" not in r["planted_description"]) / max(len(planted_types), 1)

    return {
        "rows": recall_rows,
        "overall_recall": overall_recall,
        "planted_types": list(planted_types),
        "detected_types": list(detected_types),
    }
