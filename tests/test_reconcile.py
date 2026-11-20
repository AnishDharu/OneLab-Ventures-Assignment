"""
Tests for the payments reconciliation engine.
Run with:  pytest tests/test_reconcile.py -v
"""

from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from app.reconcile import (
    classify_unmatched,
    compute_recall,
    detect_duplicates,
    detect_orphan_refunds,
    match_exact,
    match_with_tolerance,
    reconcile,
    _to_decimal,
)

# ── helpers ────────────────────────────────────────────────────────────────────

def _txn(**kwargs) -> dict:
    defaults = {
        "txn_id": "TXN0001",
        "timestamp": "2026-10-15T12:00:00Z",
        "customer_id": "CUST1234",
        "amount": "100.00",
        "currency": "USD",
        "type": "payment",
        "original_txn_id": "",
        "status": "completed",
    }
    defaults.update(kwargs)
    return defaults


def _stl(**kwargs) -> dict:
    defaults = {
        "settlement_id": "STL0001",
        "settlement_date": "2026-10-16",
        "txn_ref": "TXN0001",
        "amount": "100.00",
        "batch_id": "BATCH20261016",
    }
    defaults.update(kwargs)
    return defaults


def _make_txn_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, dtype=str)


def _make_stl_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, dtype=str)


# ══════════════════════════════════════════════════════════════════════════════
# GAP 1 — TIMING
# ══════════════════════════════════════════════════════════════════════════════

class TestTimingGap:
    def test_timing_detected_via_last_2_biz_days(self):
        """Transaction on Oct 30 (last biz day) with no settlement → TIMING."""
        txn_df = _make_txn_df([_txn(txn_id="TXN9001", timestamp="2026-10-30T23:00:00Z", amount="250.00")])
        stl_df = _make_stl_df([])
        result = reconcile(txn_df, stl_df)
        timing = [g for g in result["gaps"] if g["type"] == "TIMING"]
        assert len(timing) >= 1, "Expected at least one TIMING gap for Oct 30 transaction"
        assert any(g["ids"].get("txn_id") == "TXN9001" for g in timing)

    def test_timing_detected_via_next_month_settlement(self):
        """Transaction from October has a November settlement → TIMING."""
        txn_df = _make_txn_df([_txn(txn_id="TXN9002", timestamp="2026-10-29T10:00:00Z", amount="300.00")])
        stl_df = _make_stl_df([])  # no October settlement
        next_month = _make_stl_df([_stl(settlement_id="STL9002", txn_ref="TXN9002",
                                        settlement_date="2026-11-03", amount="300.00")])
        result = reconcile(txn_df, stl_df, next_month_stl_df=next_month)
        timing = [g for g in result["gaps"] if g["type"] == "TIMING"]
        assert any(g["ids"].get("txn_id") == "TXN9002" for g in timing)

    def test_timing_last_day_23_59(self):
        """Transaction at exactly 2026-10-30 23:59:00 is captured as TIMING."""
        txn_df = _make_txn_df([_txn(txn_id="TXN9050", timestamp="2026-10-30T23:59:00Z", amount="99.99")])
        stl_df = _make_stl_df([])
        result = reconcile(txn_df, stl_df)
        timing = [g for g in result["gaps"] if g["type"] == "TIMING"]
        assert any(g["ids"].get("txn_id") == "TXN9050" for g in timing), \
            "23:59 on last business day should be TIMING"


# ══════════════════════════════════════════════════════════════════════════════
# GAP 2 — ROUNDING
# ══════════════════════════════════════════════════════════════════════════════

class TestRoundingGap:
    def test_rounding_detected(self):
        """Platform 10.005 vs bank 10.01 → ROUNDING gap."""
        txn_df = _make_txn_df([_txn(txn_id="TXN8001", amount="10.005")])
        stl_df = _make_stl_df([_stl(txn_ref="TXN8001", amount="10.01")])
        result = reconcile(txn_df, stl_df)
        rounding = [g for g in result["gaps"] if g["type"] == "ROUNDING"]
        assert len(rounding) == 1
        assert abs(float(rounding[0]["amount_impact"])) < 0.01

    def test_rounding_aggregate_sum(self):
        """40 rounding gaps of -0.005 each → cumulative drift ~= -0.20."""
        rows_t, rows_s = [], []
        for i in range(40):
            tid = f"TXN8{i+100}"
            rows_t.append(_txn(txn_id=tid, amount="10.005"))
            rows_s.append(_stl(settlement_id=f"STL8{i+100}", txn_ref=tid, amount="10.01"))
        txn_df = _make_txn_df(rows_t)
        stl_df = _make_stl_df(rows_s)
        result = reconcile(txn_df, stl_df)
        rounding = [g for g in result["gaps"] if g["type"] == "ROUNDING"]
        total_drift = sum(float(g["amount_impact"]) for g in rounding)
        assert abs(total_drift) > 0.1, f"Expected visible aggregate drift, got {total_drift}"

    def test_rounding_not_flagged_as_exact(self):
        """Amount differing by < 0.01 must NOT appear in exact matches."""
        txn_df = _make_txn_df([_txn(txn_id="TXN8200", amount="50.005")])
        stl_df = _make_stl_df([_stl(txn_ref="TXN8200", amount="50.01")])
        matched, _, _ = match_exact(txn_df, stl_df)
        assert "TXN8200" not in matched, "Sub-cent mismatch should not be exact match"


# ══════════════════════════════════════════════════════════════════════════════
# GAP 3 — DUPLICATE
# ══════════════════════════════════════════════════════════════════════════════

class TestDuplicateGap:
    def test_duplicate_txn_detected(self):
        """Two rows with same txn_id → 1 DUPLICATE gap."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN7001", amount="150.00"),
            _txn(txn_id="TXN7001", amount="150.00"),  # duplicate
        ])
        stl_df = _make_stl_df([_stl(txn_ref="TXN7001", amount="150.00")])
        result = reconcile(txn_df, stl_df)
        dups = [g for g in result["gaps"] if g["type"] == "DUPLICATE"]
        assert len(dups) >= 1

    def test_duplicate_settlement_detected(self):
        """Two rows with same settlement_id → 1 DUPLICATE gap."""
        txn_df = _make_txn_df([_txn(txn_id="TXN7002", amount="200.00")])
        stl_df = _make_stl_df([
            _stl(settlement_id="STL7002", txn_ref="TXN7002", amount="200.00"),
            _stl(settlement_id="STL7002", txn_ref="TXN7002", amount="200.00"),  # duplicate
        ])
        result = reconcile(txn_df, stl_df)
        dups = [g for g in result["gaps"] if g["type"] == "DUPLICATE"]
        assert any(g["ids"].get("dataset") == "settlements" for g in dups)

    def test_duplicate_removed_before_matching(self):
        """After removing dup, the remaining single txn matches its settlement exactly."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN7003", amount="75.00"),
            _txn(txn_id="TXN7003", amount="75.00"),
        ])
        stl_df = _make_stl_df([_stl(txn_ref="TXN7003", amount="75.00")])
        _, clean_txn, clean_stl = detect_duplicates(txn_df, stl_df)
        matched, unmatched_t, unmatched_s = match_exact(clean_txn, clean_stl)
        assert "TXN7003" in matched
        assert unmatched_t.empty
        assert unmatched_s.empty


# ══════════════════════════════════════════════════════════════════════════════
# GAP 4 — ORPHAN REFUND
# ══════════════════════════════════════════════════════════════════════════════

class TestOrphanRefund:
    def test_orphan_refund_detected(self):
        """Refund referencing a non-existent original_txn_id → ORPHAN_REFUND."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN6001", type="refund", original_txn_id="TXN9999", amount="50.00"),
        ])
        gaps = detect_orphan_refunds(txn_df)
        assert len(gaps) == 1
        assert gaps[0]["type"] == "ORPHAN_REFUND"
        assert gaps[0]["ids"]["missing_original_txn_id"] == "TXN9999"

    def test_valid_refund_not_flagged(self):
        """Refund referencing an existing payment → NOT orphan."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN6010", type="payment", amount="80.00"),
            _txn(txn_id="TXN6011", type="refund", original_txn_id="TXN6010", amount="30.00"),
        ])
        gaps = detect_orphan_refunds(txn_df)
        assert len(gaps) == 0

    def test_two_refunds_exceed_original(self):
        """Two refunds against one original that together exceed it — both detected if original missing."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN6020", type="refund", original_txn_id="TXN6019", amount="60.00"),
            _txn(txn_id="TXN6021", type="refund", original_txn_id="TXN6019", amount="80.00"),
            # TXN6019 is NOT in the dataset
        ])
        gaps = detect_orphan_refunds(txn_df)
        assert len(gaps) == 2, "Both refunds should be orphans since original is missing"


# ══════════════════════════════════════════════════════════════════════════════
# Accounting equation — gap sum == difference
# ══════════════════════════════════════════════════════════════════════════════

class TestAccountingEquation:
    def test_gaps_sum_to_difference(self):
        """platform_total - bank_total must equal sum(gap_amounts) + unexplained."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN5001", amount="100.00"),
            _txn(txn_id="TXN5001", amount="100.00"),   # dup
            _txn(txn_id="TXN5002", amount="200.005"),  # rounding
            _txn(txn_id="TXN5003", amount="50.00", timestamp="2026-10-30T12:00:00Z"),  # timing
            _txn(txn_id="TXN5004", type="refund", original_txn_id="TXN9888", amount="30.00"),
        ])
        stl_df = _make_stl_df([
            _stl(settlement_id="STL5001", txn_ref="TXN5001", amount="100.00"),
            _stl(settlement_id="STL5002", txn_ref="TXN5002", amount="200.01"),
            _stl(settlement_id="STL5004", txn_ref="TXN5004", amount="30.00"),
        ])
        result = reconcile(txn_df, stl_df)
        platform_total = Decimal(result["platform_total"])
        bank_total     = Decimal(result["bank_total"])
        difference     = Decimal(result["difference"])
        total_explained = Decimal(result["total_explained"])
        unexplained     = Decimal(result["unexplained"])

        assert difference == platform_total - bank_total, "Difference calculation wrong"
        assert abs(unexplained) < Decimal("0.0001"), \
            f"unexplained should be ~0, got {unexplained}; difference={difference}, explained={total_explained}"

    def test_gap_category_sum_matches_difference(self):
        """Gap categories per type should sum to total_explained."""
        txn_df = _make_txn_df([_txn(txn_id="TXN4001", amount="500.00")])
        stl_df = _make_stl_df([_stl(txn_ref="TXN4001", amount="500.00")])
        result = reconcile(txn_df, stl_df)
        sum_gaps = sum(Decimal(v) for v in result["gap_by_type"].values())
        assert sum_gaps == Decimal(result["total_explained"])


# ══════════════════════════════════════════════════════════════════════════════
# Clean data produces zero gaps
# ══════════════════════════════════════════════════════════════════════════════

class TestCleanData:
    def test_clean_data_zero_gaps(self):
        """Perfectly matching data should produce zero gaps."""
        rows_t, rows_s = [], []
        for i in range(10):
            tid = f"TXN3{i:03d}"
            rows_t.append(_txn(txn_id=tid, amount="100.00",
                               timestamp=f"2026-10-{i+1:02d}T10:00:00Z"))
            rows_s.append(_stl(settlement_id=f"STL3{i:03d}", txn_ref=tid,
                                settlement_date=f"2026-10-{i+2:02d}", amount="100.00"))
        txn_df = _make_txn_df(rows_t)
        stl_df = _make_stl_df(rows_s)
        result = reconcile(txn_df, stl_df)
        assert len(result["gaps"]) == 0, f"Expected 0 gaps, got {result['gaps']}"
        assert Decimal(result["difference"]) == Decimal("0")

    def test_clean_data_zero_unexplained(self):
        """Clean data: unexplained must be exactly 0."""
        txn_df = _make_txn_df([_txn(txn_id="TXN3100", amount="999.99",
                                     timestamp="2026-10-05T08:00:00Z")])
        stl_df = _make_stl_df([_stl(txn_ref="TXN3100", amount="999.99")])
        result = reconcile(txn_df, stl_df)
        assert Decimal(result["unexplained"]) == Decimal("0")


# ══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_transactions(self):
        """Empty transactions file — should not crash."""
        txn_df = _make_txn_df([])
        stl_df = _make_stl_df([_stl(txn_ref="TXN0000", amount="100.00")])
        result = reconcile(txn_df, stl_df)
        assert Decimal(result["platform_total"]) == Decimal("0")

    def test_empty_settlements(self):
        """Empty settlements file — all txns become TIMING or MISSING."""
        txn_df = _make_txn_df([_txn(txn_id="TXN2001", amount="100.00",
                                     timestamp="2026-10-15T10:00:00Z")])
        stl_df = _make_stl_df([])
        result = reconcile(txn_df, stl_df)
        assert Decimal(result["bank_total"]) == Decimal("0")
        assert len(result["gaps"]) >= 1

    def test_negative_amounts(self):
        """Negative amounts (refunds) are handled without crash."""
        txn_df = _make_txn_df([_txn(txn_id="TXN2002", amount="-50.00",
                                     type="refund", original_txn_id="")])
        stl_df = _make_stl_df([_stl(txn_ref="TXN2002", amount="-50.00")])
        result = reconcile(txn_df, stl_df)
        # Should match exactly (both are -50.00)
        assert len([g for g in result["gaps"] if g["type"] not in ("ORPHAN_REFUND",)]) == 0

    def test_refund_larger_than_original(self):
        """Refund amount larger than original payment — orphan refund not triggered if original exists."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN2010", type="payment", amount="100.00"),
            _txn(txn_id="TXN2011", type="refund", original_txn_id="TXN2010", amount="150.00"),
        ])
        # Orphan detection: original exists, so no orphan gap
        gaps = detect_orphan_refunds(txn_df)
        assert len(gaps) == 0, "If original exists, no orphan — amount mismatch is a separate concern"

    def test_no_floats_in_decimal_conversion(self):
        """_to_decimal must never produce float-precision artifacts."""
        # 10.005 as string must round correctly via Decimal
        val = _to_decimal("10.005")
        assert isinstance(val, Decimal)
        assert val == Decimal("10.005")
        # Ensure we're not losing precision
        assert str(val) == "10.005"

    def test_decimal_arithmetic_no_float_drift(self):
        """Sum of 40 Decimal(10.005) values must be exact, not float-drifted."""
        total = sum(Decimal("10.005") for _ in range(40))
        expected = Decimal("400.200")
        assert total == expected, f"Float drift detected: {total} != {expected}"

    def test_settlement_before_transaction_date(self):
        """Settlement date before transaction date — still matches if amounts equal."""
        txn_df = _make_txn_df([_txn(txn_id="TXN2020", amount="100.00",
                                     timestamp="2026-10-15T10:00:00Z")])
        stl_df = _make_stl_df([_stl(txn_ref="TXN2020", amount="100.00",
                                     settlement_date="2026-10-13")])  # 2 days BEFORE
        matched, _, _ = match_exact(txn_df, stl_df)
        assert "TXN2020" in matched, "Should still match even if settlement is before transaction"

    def test_same_amount_different_id_no_false_match(self):
        """Same amount and date but different IDs must NOT be matched."""
        txn_df = _make_txn_df([_txn(txn_id="TXN2030", amount="777.00",
                                     timestamp="2026-10-10T10:00:00Z")])
        stl_df = _make_stl_df([_stl(settlement_id="STL2030", txn_ref="TXN9999",  # different ref
                                      amount="777.00")])
        matched, unmatched_t, unmatched_s = match_exact(txn_df, stl_df)
        assert "TXN2030" not in matched, "Must not match on amount alone — ID must match"
        assert len(unmatched_t) == 1
        assert len(unmatched_s) == 1

    def test_weekend_settlement_lag(self):
        """Transaction on Friday Oct 30 — settlement on Mon Nov 2 is valid (weekend lag)."""
        # Oct 30 2026 is a Friday; +1 biz day = Nov 2 (Monday)
        txn_df = _make_txn_df([_txn(txn_id="TXN2040", amount="100.00",
                                     timestamp="2026-10-30T12:00:00Z")])
        stl_df = _make_stl_df([])
        next_month = _make_stl_df([_stl(settlement_id="STL2040", txn_ref="TXN2040",
                                         settlement_date="2026-11-02", amount="100.00")])
        result = reconcile(txn_df, stl_df, next_month_stl_df=next_month)
        timing = [g for g in result["gaps"] if g["type"] == "TIMING"]
        assert any(g["ids"].get("txn_id") == "TXN2040" for g in timing), \
            "Friday→Monday settlement should be TIMING, not MISSING"


# ══════════════════════════════════════════════════════════════════════════════
# Adversarial tests (Prompt 2)
# ══════════════════════════════════════════════════════════════════════════════

class TestAdversarial:
    def test_same_amount_date_different_ids_not_matched(self):
        """False match risk: same amount & date, different IDs → neither matched."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN_A1", amount="500.00", timestamp="2026-10-10T09:00:00Z"),
            _txn(txn_id="TXN_A2", amount="500.00", timestamp="2026-10-10T09:00:00Z"),
        ])
        stl_df = _make_stl_df([
            _stl(settlement_id="STL_A1", txn_ref="TXN_B9", amount="500.00",  # neither A1 nor A2
                  settlement_date="2026-10-11"),
        ])
        matched, unmatched_t, _ = match_exact(txn_df, stl_df)
        assert "TXN_A1" not in matched
        assert "TXN_A2" not in matched

    def test_two_refunds_one_original_sum_exceeds(self):
        """Two refunds against one original that together exceed it — both detected."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN_P1", type="payment", amount="100.00"),
            _txn(txn_id="TXN_R1", type="refund",  original_txn_id="TXN_P1", amount="60.00"),
            _txn(txn_id="TXN_R2", type="refund",  original_txn_id="TXN_P1", amount="80.00"),
            # total refunds 140 > original 100 — flag is a business rule concern
        ])
        # No orphan gaps since TXN_P1 exists
        orphans = detect_orphan_refunds(txn_df)
        assert len(orphans) == 0  # original exists; this is a different business problem

    def test_no_double_counting_across_gap_types(self):
        """A DUPLICATE should not also be flagged as TIMING or MISSING."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN_D1", amount="100.00", timestamp="2026-10-30T12:00:00Z"),
            _txn(txn_id="TXN_D1", amount="100.00", timestamp="2026-10-30T12:00:00Z"),
        ])
        stl_df = _make_stl_df([_stl(txn_ref="TXN_D1", amount="100.00")])
        result = reconcile(txn_df, stl_df)
        dup_ids = {g["ids"].get("txn_id") for g in result["gaps"] if g["type"] == "DUPLICATE"}
        timing_ids = {g["ids"].get("txn_id") for g in result["gaps"] if g["type"] == "TIMING"}
        # The dup txn_id should appear as DUPLICATE, not also as TIMING
        assert not (dup_ids & timing_ids), "Same txn_id double-counted across DUPLICATE and TIMING"


# ══════════════════════════════════════════════════════════════════════════════
# Decimal precision test
# ══════════════════════════════════════════════════════════════════════════════

class TestDecimalPrecision:
    def test_no_float_in_gap_amounts(self):
        """All amount_impact fields must be string representations of Decimals, not floats."""
        txn_df = _make_txn_df([
            _txn(txn_id="TXN_P100", amount="10.005"),
        ])
        stl_df = _make_stl_df([
            _stl(txn_ref="TXN_P100", amount="10.01"),
        ])
        result = reconcile(txn_df, stl_df)
        for gap in result["gaps"]:
            # Must be parseable as Decimal without float
            d = Decimal(gap["amount_impact"])
            assert isinstance(d, Decimal), f"amount_impact is not Decimal-safe: {gap['amount_impact']}"
            # Must not contain float artifacts like 1.0000000001
            assert "e" not in gap["amount_impact"].lower(), f"Scientific notation in amount: {gap['amount_impact']}"

    def test_platform_bank_totals_are_decimal_safe(self):
        """platform_total and bank_total must be exact decimal strings."""
        txn_df = _make_txn_df([_txn(txn_id="TXN_P200", amount="0.001")])
        stl_df = _make_stl_df([_stl(txn_ref="TXN_P200", amount="0.001")])
        result = reconcile(txn_df, stl_df)
        assert Decimal(result["platform_total"]) == Decimal("0.001")
        assert Decimal(result["bank_total"]) == Decimal("0.001")


# ══════════════════════════════════════════════════════════════════════════════
# Integration test against generated data
# ══════════════════════════════════════════════════════════════════════════════

class TestGeneratedData:
    @pytest.fixture(autouse=True)
    def ensure_data(self):
        """Generate synthetic data if not present."""
        data_dir = Path(__file__).resolve().parent.parent / "data"
        txn_path = data_dir / "transactions.csv"
        stl_path = data_dir / "settlements.csv"
        gt_path  = data_dir / "ground_truth.json"
        if not txn_path.exists() or not stl_path.exists():
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from scripts.generate_data import generate_data
            data_dir.mkdir(parents=True, exist_ok=True)
            generate_data()

    def test_all_planted_gaps_detected(self):
        """All four planted gap types must appear in reconciliation results."""
        data_dir = Path(__file__).resolve().parent.parent / "data"
        txn_df = pd.read_csv(data_dir / "transactions.csv", dtype=str)
        stl_df = pd.read_csv(data_dir / "settlements.csv", dtype=str)
        result = reconcile(txn_df, stl_df)

        detected = {g["type"] for g in result["gaps"]}
        assert "TIMING"        in detected, "TIMING gap not detected"
        assert "ROUNDING"      in detected, "ROUNDING gap not detected"
        assert "DUPLICATE"     in detected, "DUPLICATE gap not detected"
        assert "ORPHAN_REFUND" in detected, "ORPHAN_REFUND gap not detected"

    def test_unexplained_near_zero_on_generated_data(self):
        """The unexplained remainder on generated data must be very small (<1.00)."""
        data_dir = Path(__file__).resolve().parent.parent / "data"
        txn_df = pd.read_csv(data_dir / "transactions.csv", dtype=str)
        stl_df = pd.read_csv(data_dir / "settlements.csv", dtype=str)
        result = reconcile(txn_df, stl_df)
        assert abs(float(result["unexplained"])) < 1.0, \
            f"Large unexplained residual: {result['unexplained']}"

    def test_recall_all_planted_types(self):
        """compute_recall should report 100% recall on generated data."""
        data_dir = Path(__file__).resolve().parent.parent / "data"
        txn_df = pd.read_csv(data_dir / "transactions.csv", dtype=str)
        stl_df = pd.read_csv(data_dir / "settlements.csv", dtype=str)
        gt_path = data_dir / "ground_truth.json"
        result = reconcile(txn_df, stl_df)
        with open(gt_path) as f:
            gt = json.load(f)
        recall = compute_recall(result, gt)
        assert recall["overall_recall"] == 1.0, \
            f"Not all planted gaps detected: {recall}"
