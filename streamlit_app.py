"""
Streamlit app — Payments Reconciliation Tool
Deploy: streamlit run streamlit_app.py
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

# ── project paths ──────────────────────────────────────────────────────────────
ROOT      = Path(__file__).resolve().parent
DATA_DIR  = ROOT / "data"
GT_PATH   = DATA_DIR / "ground_truth.json"
TXN_PATH  = DATA_DIR / "transactions.csv"
STL_PATH  = DATA_DIR / "settlements.csv"


def _ensure_data():
    DATA_DIR.mkdir(exist_ok=True)
    if not TXN_PATH.exists() or not STL_PATH.exists():
        import sys
        sys.path.insert(0, str(ROOT))
        from scripts.generate_data import generate_data
        generate_data()


def _load_default() -> tuple[pd.DataFrame, pd.DataFrame]:
    _ensure_data()
    return pd.read_csv(TXN_PATH, dtype=str), pd.read_csv(STL_PATH, dtype=str)


def _fmt(v) -> str:
    try:
        return f"${float(v):,.4f}"
    except Exception:
        return str(v)


def _run(txn_df, stl_df):
    from app.reconcile import reconcile, compute_recall
    result = reconcile(txn_df, stl_df)
    recall = {}
    if GT_PATH.exists():
        with open(GT_PATH) as f:
            gt = json.load(f)
        recall = compute_recall(result, gt)
    return result, recall


# ══════════════════════════════════════════════════════════════════════════════
# Page config
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Payments Reconciliation Tool",
    page_icon="⚡",
    layout="wide",
)

st.title("⚡ Payments Reconciliation Tool")
st.caption("October 2026 · Synthetic dataset · Four planted gap types")

with st.expander("📋 Assumptions", expanded=False):
    st.markdown("""
| Assumption | Detail |
|---|---|
| Timestamps | UTC (`Z` suffix) |
| Currency | Single currency — USD only |
| Settlement lag | 1–2 **business** days (Mon–Fri, no holidays) |
| Rounding tolerance | Amount differences **< \$0.01** → ROUNDING |
| Match key | `txn_id` (platform) = `txn_ref` (bank) |
| Decimal precision | Python `Decimal` throughout — zero float arithmetic |
| Timing window | Last 2 business days of month, or next-month settlement exists |
""")

# ══════════════════════════════════════════════════════════════════════════════
# Sidebar — data source
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.header("Data Source")
    mode = st.radio("Use:", ["Generated synthetic data (Oct 2026)", "Upload your own CSVs"])

    txn_df = stl_df = None

    if mode == "Generated synthetic data (Oct 2026)":
        if st.button("▶ Run Reconciliation", type="primary", use_container_width=True):
            with st.spinner("Generating data & reconciling…"):
                txn_df, stl_df = _load_default()
                st.session_state["result"], st.session_state["recall"] = _run(txn_df, stl_df)
                st.session_state["txn_df"] = txn_df
                st.session_state["stl_df"] = stl_df
            st.success("Done!")
    else:
        txn_file = st.file_uploader("transactions.csv", type="csv")
        stl_file = st.file_uploader("settlements.csv", type="csv")
        if txn_file and stl_file:
            if st.button("▶ Reconcile Uploaded Files", type="primary", use_container_width=True):
                with st.spinner("Reconciling…"):
                    txn_df = pd.read_csv(txn_file, dtype=str)
                    stl_df = pd.read_csv(stl_file, dtype=str)
                    st.session_state["result"], st.session_state["recall"] = _run(txn_df, stl_df)
                    st.session_state["txn_df"] = txn_df
                    st.session_state["stl_df"] = stl_df
                st.success("Done!")

    st.divider()
    st.caption("Run `streamlit run streamlit_app.py` locally\nor push to GitHub and deploy on **Streamlit Community Cloud**.")

# ══════════════════════════════════════════════════════════════════════════════
# Main content — only shown after a run
# ══════════════════════════════════════════════════════════════════════════════
if "result" not in st.session_state:
    st.info("👈 Click **Run Reconciliation** in the sidebar to start.")
    st.stop()

result = st.session_state["result"]
recall = st.session_state.get("recall", {})

# ── 1. Headline metrics ────────────────────────────────────────────────────────
st.subheader("📊 Summary")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Platform Total", _fmt(result["platform_total"]))
c2.metric("Bank Total",     _fmt(result["bank_total"]))
diff_val = float(result["difference"])
c3.metric("Difference",    _fmt(result["difference"]),
          delta=f"{diff_val:+.4f}", delta_color="inverse")
c4.metric("Gaps Found",    len(result["gaps"]))
unexp_val = float(result["unexplained"])
c5.metric("Unexplained",   _fmt(result["unexplained"]),
          delta="✓ Fully explained" if abs(unexp_val) < 0.001 else "⚠ Residual",
          delta_color="normal" if abs(unexp_val) < 0.001 else "inverse")

# ── 2. Accounting equation ─────────────────────────────────────────────────────
gap_parts = "  +  ".join(f"**{t}** ({_fmt(v)})" for t, v in result["gap_by_type"].items())
unexp_icon = "✅" if abs(unexp_val) < 0.001 else "❌"
st.markdown(
    f"> **Accounting Equation:**  \n"
    f"> `{_fmt(result['platform_total'])}` (platform) − `{_fmt(result['bank_total'])}` (bank) "
    f"= **`{_fmt(result['difference'])}`**  \n"
    f"> = {gap_parts or '—'}  +  unexplained **`{_fmt(result['unexplained'])}`** {unexp_icon}"
)

st.divider()

# ── 3. Charts ──────────────────────────────────────────────────────────────────
col_chart, col_match = st.columns(2)

with col_chart:
    st.subheader("📉 Gap Breakdown by Type")
    gap_data = {t: abs(float(v)) for t, v in result["gap_by_type"].items() if float(v) != 0}
    if gap_data:
        chart_df = pd.DataFrame(
            {"Gap Type": list(gap_data.keys()), "Amount (\$)": list(gap_data.values())}
        ).set_index("Gap Type")
        st.bar_chart(chart_df, use_container_width=True)
    else:
        st.success("No gaps found — perfect reconciliation!")

with col_match:
    st.subheader("🎯 Match Status")
    stats = result.get("stats", {})
    exact   = stats.get("exact_matched", 0)
    rounding = stats.get("rounding_matched", 0)
    unmatched = stats.get("unmatched_transactions", 0)
    total = stats.get("total_transactions", 1)

    match_df = pd.DataFrame({
        "Category": ["Exact Match", "Tolerance Match", "Unmatched"],
        "Count": [exact, rounding, unmatched],
        "Pct": [f"{exact/total*100:.1f}%", f"{rounding/total*100:.1f}%", f"{unmatched/total*100:.1f}%"]
    })
    st.dataframe(match_df, hide_index=True, use_container_width=True)
    st.caption(f"Total transactions: {total} | Settlements: {stats.get('total_settlements',0)}")

st.divider()

# ── 4. Recall vs Ground Truth ──────────────────────────────────────────────────
if recall and recall.get("rows"):
    st.subheader("🎯 Recall vs Ground Truth — All Planted Gaps Caught?")
    recall_rows = []
    for r in recall["rows"]:
        is_fp = "NOT PLANTED" in r["planted_description"]
        status = "🟡 FALSE POSITIVE" if is_fp else ("✅ CAUGHT" if r["detected"] else "❌ MISSED")
        recall_rows.append({
            "Gap Type":        r["planted_type"],
            "Description":     r["planted_description"],
            "Amount Impact":   _fmt(r["planted_amount_impact"]),
            "Detected Count":  r["detected_count"],
            "Status":          status,
        })
    recall_df = pd.DataFrame(recall_rows)
    pct = recall.get("overall_recall", 0) * 100
    color = "🟢" if pct >= 100 else "🟡"
    st.dataframe(recall_df, hide_index=True, use_container_width=True)
    st.markdown(f"**Overall Recall: {color} {pct:.0f}%**")
    st.divider()

# ── 5. Gap details table ───────────────────────────────────────────────────────
st.subheader("🔍 Gap Details")

gaps = result.get("gaps", [])
if not gaps:
    st.success("✅ No gaps detected.")
else:
    # Filters
    fc1, fc2 = st.columns([2, 1])
    with fc1:
        search = st.text_input("🔎 Search IDs or explanations", placeholder="e.g. TXN01011")
    with fc2:
        all_types = sorted(set(g["type"] for g in gaps))
        type_filter = st.multiselect("Filter by type", all_types, default=all_types)

    # Build display df
    rows = []
    for g in gaps:
        ids_str = " | ".join(f"{k}: {v}" for k, v in g["ids"].items()) if isinstance(g["ids"], dict) else str(g["ids"])
        rows.append({
            "Type":          g["type"],
            "IDs":           ids_str,
            "Amount Impact": _fmt(g["amount_impact"]),
            "Explanation":   g["explanation"],
            "Confidence":    f"{g['confidence']*100:.0f}%",
        })

    gap_df = pd.DataFrame(rows)

    # Apply filters
    if type_filter:
        gap_df = gap_df[gap_df["Type"].isin(type_filter)]
    if search:
        mask = gap_df.apply(lambda r: search.lower() in r.to_string().lower(), axis=1)
        gap_df = gap_df[mask]

    st.dataframe(
        gap_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Type": st.column_config.TextColumn(width="small"),
            "Amount Impact": st.column_config.TextColumn(width="small"),
            "Confidence": st.column_config.TextColumn(width="small"),
            "Explanation": st.column_config.TextColumn(width="large"),
        }
    )
    st.caption(f"Showing {len(gap_df)} of {len(gaps)} gaps")

# ── 6. Raw data preview ────────────────────────────────────────────────────────
with st.expander("📄 View Raw Data"):
    t1, t2 = st.tabs(["Transactions", "Settlements"])
    with t1:
        if "txn_df" in st.session_state:
            st.dataframe(st.session_state["txn_df"], use_container_width=True, hide_index=True)
    with t2:
        if "stl_df" in st.session_state:
            st.dataframe(st.session_state["stl_df"], use_container_width=True, hide_index=True)
