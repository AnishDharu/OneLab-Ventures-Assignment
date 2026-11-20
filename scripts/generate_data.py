"""
Synthetic data generator for payments reconciliation tool.
Generates transactions.csv, settlements.csv, and ground_truth.json
for October 2026 with exactly four planted gap types.
"""

import csv
import json
import random
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import os

random.seed(42)

# ── constants ──────────────────────────────────────────────────────────────────
MONTH_START = date(2026, 10, 1)
MONTH_END   = date(2026, 10, 31)
NEXT_MONTH  = date(2026, 11, 1)
CURRENCIES  = ["USD"]
TYPES       = ["payment", "refund"]
NUM_PAYMENTS = 460  # clean baseline payments
OUTPUT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TRANSACTIONS_PATH = os.path.join(OUTPUT_DIR, "data", "transactions.csv")
SETTLEMENTS_PATH  = os.path.join(OUTPUT_DIR, "data", "settlements.csv")
GROUND_TRUTH_PATH = os.path.join(OUTPUT_DIR, "data", "ground_truth.json")


def _business_days_add(d: date, n: int) -> date:
    """Add n business days to date d."""
    current = d
    added = 0
    while added < n:
        current += timedelta(days=1)
        if current.weekday() < 5:  # Mon-Fri
            added += 1
    return current


def _random_date_in_month(start: date = MONTH_START, end: date = MONTH_END) -> date:
    delta = (end - start).days
    return start + timedelta(days=random.randint(0, delta))


def _random_datetime(d: date) -> datetime:
    h = random.randint(0, 23)
    m = random.randint(0, 59)
    s = random.randint(0, 59)
    return datetime(d.year, d.month, d.day, h, m, s)


def _random_amount() -> Decimal:
    base = Decimal(str(round(random.uniform(1.0, 999.0), 2)))
    return base


def _last_business_days(n: int = 2) -> list[date]:
    """Return the last n business days of October 2026."""
    days = []
    d = MONTH_END
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days


def generate_data():
    os.makedirs(os.path.join(OUTPUT_DIR, "data"), exist_ok=True)

    transactions = []
    settlements  = []
    txn_id_counter = 1000
    stl_id_counter = 5000
    batch_map: dict[date, str] = {}   # settle_date -> batch_id

    def next_txn_id():
        nonlocal txn_id_counter
        txn_id_counter += 1
        return f"TXN{txn_id_counter:05d}"

    def next_stl_id():
        nonlocal stl_id_counter
        stl_id_counter += 1
        return f"STL{stl_id_counter:05d}"

    def batch_for(d: date) -> str:
        if d not in batch_map:
            batch_map[d] = f"BATCH{d.strftime('%Y%m%d')}"
        return batch_map[d]

    # ── 1. CLEAN BASELINE (≈95%) ───────────────────────────────────────────────
    clean_txn_ids = []
    for _ in range(NUM_PAYMENTS):
        txn_id  = next_txn_id()
        txn_date = _random_date_in_month(MONTH_START, date(2026, 10, 28))  # leave room for timing gap
        ts       = _random_datetime(txn_date)
        amount   = _random_amount()
        settle_days = random.choice([1, 2])
        settle_date = _business_days_add(txn_date, settle_days)
        # keep settlement within October for clean baseline
        if settle_date > MONTH_END:
            settle_date = MONTH_END

        transactions.append({
            "txn_id": txn_id,
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "customer_id": f"CUST{random.randint(1000, 9999)}",
            "amount": str(amount),
            "currency": "USD",
            "type": "payment",
            "original_txn_id": "",
            "status": "completed",
        })
        settlements.append({
            "settlement_id": next_stl_id(),
            "settlement_date": settle_date.isoformat(),
            "txn_ref": txn_id,
            "amount": str(amount),
            "batch_id": batch_for(settle_date),
        })
        clean_txn_ids.append(txn_id)

    # ── 2. GAP 1: TIMING (3-5 txns from last 1-2 days, settlement next month) ──
    timing_days = _last_business_days(2)   # Oct 30, Oct 29
    num_timing  = random.randint(3, 5)     # will be 4 with seed 42
    timing_ids  = []
    timing_amount_impact = Decimal("0")

    for i in range(num_timing):
        txn_id = next_txn_id()
        txn_date = timing_days[i % len(timing_days)]
        ts = _random_datetime(txn_date)
        amount = _random_amount()
        # settlement falls in November
        settle_date = _business_days_add(txn_date, random.choice([1, 2]))
        # force it to November
        if settle_date <= MONTH_END:
            settle_date = date(2026, 11, 3)

        transactions.append({
            "txn_id": txn_id,
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "customer_id": f"CUST{random.randint(1000, 9999)}",
            "amount": str(amount),
            "currency": "USD",
            "type": "payment",
            "original_txn_id": "",
            "status": "completed",
        })
        # settlement is in NEXT month's data — omit from main settlements.csv
        timing_ids.append(txn_id)
        timing_amount_impact += amount

    # ── 3. GAP 2: ROUNDING (≈40 txns, bank rounds half-up, platform keeps sub-cent) ──
    num_rounding = 40
    rounding_ids = []
    rounding_drift = Decimal("0")

    for _ in range(num_rounding):
        txn_id  = next_txn_id()
        txn_date = _random_date_in_month(MONTH_START, date(2026, 10, 28))
        ts       = _random_datetime(txn_date)
        # Amount with 3 decimal places so bank rounds differently
        base = Decimal(str(round(random.uniform(1.0, 999.0), 2)))
        sub_cent = Decimal("0.005")
        platform_amount = base + sub_cent   # e.g. 10.005
        bank_amount = platform_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)  # 10.01
        drift = platform_amount - bank_amount  # -0.005 or 0
        settle_date = _business_days_add(txn_date, random.choice([1, 2]))
        if settle_date > MONTH_END:
            settle_date = MONTH_END

        transactions.append({
            "txn_id": txn_id,
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "customer_id": f"CUST{random.randint(1000, 9999)}",
            "amount": str(platform_amount),
            "currency": "USD",
            "type": "payment",
            "original_txn_id": "",
            "status": "completed",
        })
        settlements.append({
            "settlement_id": next_stl_id(),
            "settlement_date": settle_date.isoformat(),
            "txn_ref": txn_id,
            "amount": str(bank_amount),
            "batch_id": batch_for(settle_date),
        })
        rounding_ids.append(txn_id)
        rounding_drift += drift  # accumulates to ~-0.2 (40 * -0.005)

    # ── 4. GAP 3: DUPLICATE (2 dup txn rows + 1 dup settlement row) ──────────
    dup_txn_id  = clean_txn_ids[10]   # duplicate this existing transaction
    dup_stl_id  = settlements[20]["settlement_id"]   # duplicate this settlement

    # Find the original transaction to duplicate
    orig_txn = next(t for t in transactions if t["txn_id"] == dup_txn_id)
    orig_stl = settlements[20]

    transactions.append(dict(orig_txn))   # exact duplicate of txn row
    settlements.append(dict(orig_stl))    # exact duplicate of settlement row
    duplicate_amount = Decimal(orig_txn["amount"])

    # ── 5. GAP 4: ORPHAN REFUNDS (2 refunds with non-existent original_txn_id) ──
    orphan_refund_ids = []
    orphan_amount_impact = Decimal("0")

    for i in range(2):
        txn_id  = next_txn_id()
        txn_date = _random_date_in_month(MONTH_START, date(2026, 10, 25))
        ts       = _random_datetime(txn_date)
        amount   = _random_amount()
        orig_id  = f"TXN99{i+1:03d}"  # non-existent

        transactions.append({
            "txn_id": txn_id,
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "customer_id": f"CUST{random.randint(1000, 9999)}",
            "amount": str(amount),
            "currency": "USD",
            "type": "refund",
            "original_txn_id": orig_id,
            "status": "completed",
        })
        # Orphan refunds DO have settlements (bank paid them out)
        settle_date = _business_days_add(txn_date, 1)
        if settle_date > MONTH_END:
            settle_date = MONTH_END
        settlements.append({
            "settlement_id": next_stl_id(),
            "settlement_date": settle_date.isoformat(),
            "txn_ref": txn_id,
            "amount": str(amount),
            "batch_id": batch_for(settle_date),
        })
        orphan_refund_ids.append({"refund_txn_id": txn_id, "missing_original_txn_id": orig_id})
        orphan_amount_impact += amount

    # ── SHUFFLE ────────────────────────────────────────────────────────────────
    random.shuffle(transactions)
    random.shuffle(settlements)

    # ── WRITE CSVs ─────────────────────────────────────────────────────────────
    txn_fields = ["txn_id", "timestamp", "customer_id", "amount", "currency",
                  "type", "original_txn_id", "status"]
    stl_fields = ["settlement_id", "settlement_date", "txn_ref", "amount", "batch_id"]

    with open(TRANSACTIONS_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=txn_fields)
        w.writeheader()
        w.writerows(transactions)

    with open(SETTLEMENTS_PATH, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=stl_fields)
        w.writeheader()
        w.writerows(settlements)

    # ── GROUND TRUTH ───────────────────────────────────────────────────────────
    ground_truth = {
        "gaps": [
            {
                "type": "TIMING",
                "affected_ids": timing_ids,
                "amount_impact": str(timing_amount_impact),
                "description": f"{len(timing_ids)} transactions from Oct 29-30 whose settlements land in November",
            },
            {
                "type": "ROUNDING",
                "affected_ids": rounding_ids,
                "amount_impact": str(rounding_drift),
                "description": f"{len(rounding_ids)} transactions where platform holds sub-cent amount, bank rounds; cumulative drift",
            },
            {
                "type": "DUPLICATE",
                "affected_ids": {
                    "duplicate_txn_id": dup_txn_id,
                    "duplicate_settlement_id": dup_stl_id,
                },
                "amount_impact": str(duplicate_amount),
                "description": "1 transaction duplicated in transactions.csv; 1 settlement duplicated in settlements.csv",
            },
            {
                "type": "ORPHAN_REFUND",
                "affected_ids": orphan_refund_ids,
                "amount_impact": str(orphan_amount_impact),
                "description": "2 refunds whose original_txn_id does not exist in transactions",
            },
        ],
        "metadata": {
            "month": "2026-10",
            "total_transactions": len(transactions),
            "total_settlements": len(settlements),
            "clean_baseline_count": NUM_PAYMENTS,
        },
    }

    with open(GROUND_TRUTH_PATH, "w") as f:
        json.dump(ground_truth, f, indent=2)

    print(f"Generated {len(transactions)} transactions -> {TRANSACTIONS_PATH}")
    print(f"Generated {len(settlements)} settlements  -> {SETTLEMENTS_PATH}")
    print(f"Ground truth                             -> {GROUND_TRUTH_PATH}")
    print(f"\nPlanted gaps:")
    print(f"  TIMING:       {len(timing_ids)} txns, impact={timing_amount_impact}")
    print(f"  ROUNDING:     {len(rounding_ids)} txns, drift={rounding_drift}")
    print(f"  DUPLICATE:    txn={dup_txn_id}, stl={dup_stl_id}, amount={duplicate_amount}")
    print(f"  ORPHAN REFUND:{len(orphan_refund_ids)} refunds")


if __name__ == "__main__":
    generate_data()
