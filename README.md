# Payments Reconciliation Tool

A FastAPI + pandas web app that reconciles platform transaction records against bank settlements, finds the gaps, and explains them.

## Assumptions

| Assumption | Detail |
|---|---|
| **Timestamps** | All timestamps are UTC (`Z` suffix) |
| **Currency** | Single currency (USD) — no FX conversion |
| **Settlement lag** | 1–2 **business** days after transaction date |
| **Business days** | Monday–Friday; no holiday calendar applied |
| **Rounding tolerance** | Amount differences < \$0.01 are classified as ROUNDING, not mismatches |
| **Match key** | `txn_id` (platform) = `txn_ref` (bank); amounts compared separately |
| **Decimal precision** | Python `Decimal` throughout — floats are never used for money |
| **Month scope** | October 2026 for the synthetic dataset |
| **Timing window** | A transaction is TIMING if it's within the last 2 business days of the month OR a settlement exists in next month's data |

## Project Structure

```
pay-recon/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI app
│   └── reconcile.py     # Reconciliation engine
├── scripts/
│   └── generate_data.py # Synthetic data generator
├── static/
│   └── index.html       # Single-page UI
├── tests/
│   └── test_reconcile.py
├── data/                # Auto-generated CSVs (gitignored)
├── requirements.txt
├── render.yaml
└── README.md
```

## How to Run

```bash
# 1. Clone and install
git clone <repo-url>
cd pay-recon
pip install -r requirements.txt

# 2. Generate synthetic data (once)
python scripts/generate_data.py

# 3. Start the server
uvicorn app.main:app --reload --port 8000

# 4. Open the UI
open http://localhost:8000
```

The server auto-generates data on startup if CSVs are missing.

## How to Test

```bash
pytest tests/test_reconcile.py -v
```

The test suite covers:
- One test per planted gap type (TIMING, ROUNDING, DUPLICATE, ORPHAN_REFUND)
- Accounting equation: `platform_total - bank_total = Σ(gaps) + unexplained (≈ 0)`
- Clean data produces zero gaps
- Edge cases: empty file, negative amounts, refund > original, currency mismatch
- Decimal precision: no floats
- Adversarial: same amount/date but different IDs (false match risk), two refunds exceeding original, settlement before transaction date, last-day 23:59 UTC, weekend settlement lag

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Single-page UI |
| `/health` | GET | Health check |
| `/reconcile` | POST | Run reconciliation (uses generated CSVs or uploaded files) |
| `/results` | GET | Full gap list + ground-truth recall panel |

### Upload custom CSVs

```bash
curl -X POST http://localhost:8000/reconcile \
  -F "transactions_file=@transactions.csv" \
  -F "settlements_file=@settlements.csv"
```

## Planted Gap Types

| Type | What it is | Visibility |
|---|---|---|
| **TIMING** | 3–5 txns from Oct 29–30 whose settlements land in November | Per-transaction |
| **ROUNDING** | ~40 txns where platform holds \$X.005, bank rounds to \$X.01 | **Aggregate only** (each diff < \$0.01) |
| **DUPLICATE** | 1 duplicate txn row + 1 duplicate settlement row | Per-row |
| **ORPHAN_REFUND** | 2 refunds whose `original_txn_id` doesn't exist | Per-refund |

## Accounting Equation

The UI displays:

```
platform_total − bank_total = TIMING + ROUNDING + DUPLICATE + ORPHAN_REFUND + unexplained
```

Target: `unexplained = 0`. Any residual indicates an unclassified gap.

## Deploying to Render (Free Tier)

1. Push the repo to GitHub.
2. Go to [render.com](https://render.com) → **New → Web Service**.
3. Connect your GitHub repo.
4. Render reads `render.yaml` automatically — no manual config needed.
5. Click **Create Web Service**.
6. The build command installs deps and generates data; the start command launches uvicorn.

**Manual deploy commands:**

```bash
# If deploying manually or testing the production start:
pip install -r requirements.txt
python scripts/generate_data.py
PORT=10000 uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## What This Would Get Wrong in Production

1. **FX and multi-currency settlement**: the tool assumes a single currency (USD); real payment platforms settle in dozens of currencies, and the reconciliation must account for FX conversion rates at the precise settlement timestamp, which introduces legitimate rounding that is entirely separate from bank rounding errors.
2. **Partial settlements and fee deductions**: payment processors routinely net their fees from settlements, so a \$100 transaction may arrive as \$97.10 after a 2.9% + \$0.30 fee — the tool would flag every such row as a rounding gap rather than recognizing fee deduction as a valid match pattern.
3. **Non-linear retry and reversal chains**: a declined-then-retried payment generates multiple platform events (attempt, fail, retry, success) that may map to a single settlement or to no settlement at all; the current one-to-one txn_id → txn_ref matching model cannot represent these chains, causing cascading false positives in the MISSING and ORPHAN_REFUND categories.

---

> **Start command (for Render / Railway):**
> ```
> uvicorn app.main:app --host 0.0.0.0 --port $PORT
> ```
