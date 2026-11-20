"""
FastAPI application for the payments reconciliation tool.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.reconcile import reconcile, compute_recall

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR        = Path(__file__).resolve().parent.parent
DATA_DIR        = BASE_DIR / "data"
STATIC_DIR      = BASE_DIR / "static"
TRANSACTIONS_CSV = DATA_DIR / "transactions.csv"
SETTLEMENTS_CSV  = DATA_DIR / "settlements.csv"
GROUND_TRUTH_JSON = DATA_DIR / "ground_truth.json"

# In-memory results cache
_last_result: dict = {}
_last_recall: dict = {}

# ── App ────────────────────────────────────────────────────────────────────────
app = FastAPI(title="Payments Reconciliation Tool", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ensure_data():
    """Generate synthetic data if CSVs don't exist."""
    if not TRANSACTIONS_CSV.exists() or not SETTLEMENTS_CSV.exists():
        import sys
        sys.path.insert(0, str(BASE_DIR))
        from scripts.generate_data import generate_data
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        generate_data()


def _run_reconcile(txn_df: pd.DataFrame, stl_df: pd.DataFrame) -> dict:
    """Run reconciliation and store result + recall in module cache."""
    global _last_result, _last_recall
    result = reconcile(txn_df, stl_df)

    # Load ground truth if available
    if GROUND_TRUTH_JSON.exists():
        with open(GROUND_TRUTH_JSON) as f:
            gt = json.load(f)
        _last_recall = compute_recall(result, gt)
    else:
        _last_recall = {}

    _last_result = result
    return result


# ── Startup ────────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    _ensure_data()
    # Run initial reconciliation on the generated data
    if TRANSACTIONS_CSV.exists() and SETTLEMENTS_CSV.exists():
        txn_df = pd.read_csv(TRANSACTIONS_CSV, dtype=str)
        stl_df = pd.read_csv(SETTLEMENTS_CSV, dtype=str)
        _run_reconcile(txn_df, stl_df)


# ── Health ─────────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "data_ready": TRANSACTIONS_CSV.exists()}


# ── Reconcile endpoint ─────────────────────────────────────────────────────────
@app.post("/reconcile")
async def reconcile_endpoint(
    transactions_file: Optional[UploadFile] = File(None),
    settlements_file:  Optional[UploadFile] = File(None),
):
    """
    POST /reconcile
    - If files are uploaded, use them.
    - Otherwise, use the generated CSVs.
    """
    try:
        if transactions_file and settlements_file:
            txn_content = await transactions_file.read()
            stl_content = await settlements_file.read()
            txn_df = pd.read_csv(io.BytesIO(txn_content), dtype=str)
            stl_df = pd.read_csv(io.BytesIO(stl_content), dtype=str)
        else:
            _ensure_data()
            if not TRANSACTIONS_CSV.exists() or not SETTLEMENTS_CSV.exists():
                raise HTTPException(status_code=404, detail="Data files not found.")
            txn_df = pd.read_csv(TRANSACTIONS_CSV, dtype=str)
            stl_df = pd.read_csv(SETTLEMENTS_CSV, dtype=str)

        result = _run_reconcile(txn_df, stl_df)
        return JSONResponse(content={"status": "ok", "summary": {
            "platform_total": result["platform_total"],
            "bank_total": result["bank_total"],
            "difference": result["difference"],
            "gap_by_type": result["gap_by_type"],
            "unexplained": result["unexplained"],
            "stats": result["stats"],
        }})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Results endpoint ───────────────────────────────────────────────────────────
@app.get("/results")
async def get_results():
    """GET /results — returns full gaps list + recall panel."""
    if not _last_result:
        raise HTTPException(status_code=404, detail="No reconciliation run yet. POST /reconcile first.")
    return JSONResponse(content={
        "result": _last_result,
        "recall": _last_recall,
    })


# ── Serve the SPA ──────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Static files not found</h1>", status_code=404)
