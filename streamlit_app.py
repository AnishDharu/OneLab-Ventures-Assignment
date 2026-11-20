"""
Payments Reconciliation Dashboard — Streamlit
A polished, production-quality UI with Plotly charts, landing hero,
custom CSS theming, and multi-tab drill-down.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
GT_PATH  = DATA_DIR / "ground_truth.json"
TXN_PATH = DATA_DIR / "transactions.csv"
STL_PATH = DATA_DIR / "settlements.csv"

# ── Color palette ──────────────────────────────────────────────────────────────
COLORS = {
    "TIMING":        "#3B82F6",   # blue
    "ROUNDING":      "#F59E0B",   # amber
    "DUPLICATE":     "#8B5CF6",   # purple
    "ORPHAN_REFUND": "#EF4444",   # red
    "MISSING":       "#6B7280",   # gray
}
BG_DARK    = "#0F1117"
CARD_BG    = "#1A1D2E"
BORDER     = "#2E3155"
TEXT_MUTED = "#94A3B8"

# ══════════════════════════════════════════════════════════════════════════════
# Page config — MUST be first Streamlit call
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="PayRecon · Reconciliation Dashboard",
    page_icon="💳",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ══════════════════════════════════════════════════════════════════════════════
# Global CSS
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
/* ── Base ── */
[data-testid="stAppViewContainer"] { background: #0F1117; }
[data-testid="stSidebar"]          { background: #13162B; border-right: 1px solid #2E3155; }
[data-testid="stHeader"]           { background: transparent; }
.block-container { padding-top: 1.5rem; padding-bottom: 2rem; }

/* ── Hero ── */
.hero {
    background: linear-gradient(135deg, #1a1d2e 0%, #0f1117 60%, #1a0a2e 100%);
    border: 1px solid #2E3155;
    border-radius: 16px;
    padding: 2.5rem 2.8rem 2rem;
    margin-bottom: 1.5rem;
    position: relative;
    overflow: hidden;
}
.hero::before {
    content: "";
    position: absolute; inset: 0;
    background: radial-gradient(ellipse at 80% 50%, rgba(99,102,241,.12) 0%, transparent 65%);
    pointer-events: none;
}
.hero-badge {
    display: inline-block;
    background: rgba(99,102,241,.18);
    border: 1px solid rgba(99,102,241,.4);
    color: #a5b4fc;
    font-size: 0.72rem;
    font-weight: 700;
    letter-spacing: .08em;
    text-transform: uppercase;
    padding: .25rem .75rem;
    border-radius: 999px;
    margin-bottom: .9rem;
}
.hero h1 {
    font-size: 2.4rem !important;
    font-weight: 800 !important;
    color: #f1f5f9 !important;
    line-height: 1.2 !important;
    margin: 0 0 .5rem !important;
}
.hero h1 span { color: #818cf8; }
.hero p {
    color: #94a3b8;
    font-size: 1rem;
    margin: 0 0 1.4rem;
    max-width: 600px;
}
.hero-pills { display: flex; gap: .5rem; flex-wrap: wrap; }
.pill {
    display: inline-flex; align-items: center; gap: .35rem;
    background: rgba(255,255,255,.05);
    border: 1px solid rgba(255,255,255,.1);
    color: #cbd5e1;
    font-size: .78rem; font-weight: 600;
    padding: .28rem .75rem;
    border-radius: 999px;
}

/* ── Metric cards ── */
.kpi-row { display: grid; grid-template-columns: repeat(5,1fr); gap: .9rem; margin-bottom: 1.5rem; }
.kpi-card {
    background: #1A1D2E;
    border: 1px solid #2E3155;
    border-radius: 12px;
    padding: 1.1rem 1.2rem;
    position: relative;
    overflow: hidden;
}
.kpi-card::after {
    content: "";
    position: absolute; left: 0; top: 0; bottom: 0;
    width: 3px;
    border-radius: 3px 0 0 3px;
}
.kpi-card.blue::after   { background: #3B82F6; }
.kpi-card.purple::after { background: #8B5CF6; }
.kpi-card.amber::after  { background: #F59E0B; }
.kpi-card.red::after    { background: #EF4444; }
.kpi-card.green::after  { background: #22C55E; }
.kpi-card.teal::after   { background: #22D3EE; }
.kpi-label { font-size: .72rem; color: #64748b; text-transform: uppercase; letter-spacing: .07em; margin-bottom: .35rem; font-weight: 600; }
.kpi-value { font-size: 1.55rem; font-weight: 800; color: #f1f5f9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.kpi-delta { font-size: .75rem; margin-top: .25rem; }
.kpi-delta.good { color: #22c55e; }
.kpi-delta.bad  { color: #ef4444; }
.kpi-delta.muted{ color: #64748b; }

/* ── Section header ── */
.section-header {
    display: flex; align-items: center; gap: .6rem;
    font-size: 1rem; font-weight: 700; color: #e2e8f0;
    margin-bottom: 1rem;
}
.section-header .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #6366f1; flex-shrink: 0;
}

/* ── Equation banner ── */
.equation-banner {
    background: linear-gradient(90deg, rgba(99,102,241,.12) 0%, rgba(99,102,241,.04) 100%);
    border: 1px solid rgba(99,102,241,.3);
    border-radius: 10px;
    padding: .9rem 1.3rem;
    font-size: .88rem;
    margin-bottom: 1.5rem;
    color: #cbd5e1;
}
.equation-banner strong { color: #818cf8; }
.equation-banner .ok  { color: #22c55e; font-weight: 700; }
.equation-banner .bad { color: #ef4444; font-weight: 700; }

/* ── Gap type badge ── */
.gbadge {
    display: inline-block; padding: .18rem .6rem;
    border-radius: 5px; font-size: .72rem; font-weight: 700; white-space: nowrap;
}
.gbadge-TIMING       { background: rgba(59,130,246,.2);  color: #93c5fd; }
.gbadge-ROUNDING     { background: rgba(245,158,11,.2);  color: #fde68a; }
.gbadge-DUPLICATE    { background: rgba(139,92,246,.2);  color: #c4b5fd; }
.gbadge-ORPHAN_REFUND{ background: rgba(239,68,68,.2);   color: #fca5a5; }
.gbadge-MISSING      { background: rgba(107,114,128,.2); color: #9ca3af; }

/* ── Recall card ── */
.recall-card {
    background: #1A1D2E; border: 1px solid #2E3155;
    border-radius: 12px; padding: 1rem 1.3rem; display: flex;
    align-items: center; gap: 1rem;
}
.recall-icon { font-size: 2rem; }
.recall-info .label { font-size: .72rem; color: #64748b; text-transform: uppercase; letter-spacing: .07em; }
.recall-info .value { font-size: 1.3rem; font-weight: 800; }

/* ── Tabs ── */
[data-testid="stTabs"] { margin-top: .5rem; }

/* ── Sidebar ── */
.sb-title { font-size: 1rem; font-weight: 800; color: #e2e8f0; margin-bottom: 1rem; }
.sb-hint  { font-size: .75rem; color: #475569; line-height: 1.5; }

/* ── Hide default header cruft ── */
#MainMenu { visibility: hidden; }
footer    { visibility: hidden; }
header    { visibility: hidden; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════
def _fmt(v, decimals=2) -> str:
    try:
        return f"${float(v):,.{decimals}f}"
    except Exception:
        return str(v)

def _ensure_data():
    DATA_DIR.mkdir(exist_ok=True)
    if not TXN_PATH.exists() or not STL_PATH.exists():
        import sys; sys.path.insert(0, str(ROOT))
        from scripts.generate_data import generate_data
        generate_data()

def _load_default():
    _ensure_data()
    return pd.read_csv(TXN_PATH, dtype=str), pd.read_csv(STL_PATH, dtype=str)

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
# Sidebar
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown('<div class="sb-title">💳 PayRecon</div>', unsafe_allow_html=True)
    st.markdown("---")

    mode = st.radio(
        "Data source",
        ["🧪 Synthetic dataset", "📂 Upload CSVs"],
        label_visibility="collapsed",
    )

    if mode == "🧪 Synthetic dataset":
        st.markdown("**Synthetic · October 2026**")
        st.markdown(
            '<div class="sb-hint">500 transactions · 4 planted gaps<br>'
            'TIMING · ROUNDING · DUPLICATE · ORPHAN REFUND</div>',
            unsafe_allow_html=True,
        )
        st.markdown("")
        run_btn = st.button("▶ Run Reconciliation", type="primary", use_container_width=True)
        if run_btn:
            with st.spinner("Generating data & reconciling…"):
                txn_df, stl_df = _load_default()
                st.session_state["result"], st.session_state["recall"] = _run(txn_df, stl_df)
                st.session_state["txn_df"] = txn_df
                st.session_state["stl_df"] = stl_df
            st.success("Reconciliation complete!")
    else:
        txn_file = st.file_uploader("transactions.csv", type="csv", label_visibility="visible")
        stl_file = st.file_uploader("settlements.csv",  type="csv", label_visibility="visible")
        if txn_file and stl_file:
            if st.button("▶ Reconcile", type="primary", use_container_width=True):
                with st.spinner("Reconciling…"):
                    txn_df = pd.read_csv(txn_file, dtype=str)
                    stl_df = pd.read_csv(stl_file, dtype=str)
                    st.session_state["result"], st.session_state["recall"] = _run(txn_df, stl_df)
                    st.session_state["txn_df"] = txn_df
                    st.session_state["stl_df"] = stl_df
                st.success("Done!")

    st.markdown("---")

    # Assumptions
    with st.expander("📋 Assumptions"):
        st.markdown("""
- **UTC** timestamps
- **USD** only — no FX
- **1–2 biz-day** settlement lag
- Tolerance: **< $0.01** → ROUNDING
- Match: `txn_id = txn_ref`
- **Decimal** math throughout
- Timing: last 2 biz days or next-month settlement
        """)

    st.markdown('<div class="sb-hint" style="margin-top:.5rem">Deploy: push to GitHub →<br><b>share.streamlit.io</b></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Hero / Landing
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""
<div class="hero">
  <div class="hero-badge">⚡ Month-End Reconciliation · October 2026</div>
  <h1>Payments <span>Reconciliation</span> Dashboard</h1>
  <p>Automatically detect timing gaps, rounding drift, duplicate entries, and orphan refunds
     between your platform ledger and bank settlements.</p>
  <div class="hero-pills">
    <span class="pill">🔵 TIMING</span>
    <span class="pill">🟡 ROUNDING</span>
    <span class="pill">🟣 DUPLICATE</span>
    <span class="pill">🔴 ORPHAN REFUND</span>
    <span class="pill">📐 Decimal precision</span>
    <span class="pill">🔎 33 tests · 100% recall</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Landing state ─────────────────────────────────────────────────────────────
if "result" not in st.session_state:
    # Show three feature cards before any run
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("""
<div class="kpi-card teal" style="padding:1.3rem 1.4rem; height:160px;">
  <div style="font-size:1.6rem;margin-bottom:.5rem">🔍</div>
  <div style="font-weight:700;color:#e2e8f0;margin-bottom:.3rem">Gap Detection</div>
  <div style="font-size:.82rem;color:#64748b">Automatically classify every mismatch into one of four gap categories with confidence scores.</div>
</div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("""
<div class="kpi-card purple" style="padding:1.3rem 1.4rem; height:160px;">
  <div style="font-size:1.6rem;margin-bottom:.5rem">📐</div>
  <div style="font-weight:700;color:#e2e8f0;margin-bottom:.3rem">Accounting Equation</div>
  <div style="font-size:.82rem;color:#64748b">Platform − Bank = Σ gaps + unexplained. Target: unexplained = $0.00.</div>
</div>""", unsafe_allow_html=True)
    with c3:
        st.markdown("""
<div class="kpi-card blue" style="padding:1.3rem 1.4rem; height:160px;">
  <div style="font-size:1.6rem;margin-bottom:.5rem">🎯</div>
  <div style="font-weight:700;color:#e2e8f0;margin-bottom:.3rem">Ground Truth Recall</div>
  <div style="font-size:.82rem;color:#64748b">Compare detected gaps against planted ground truth — visualise recall per gap type.</div>
</div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    st.info("👈  Click **Run Reconciliation** in the sidebar to load the synthetic dataset and start.")
    st.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Data
# ══════════════════════════════════════════════════════════════════════════════
result  = st.session_state["result"]
recall  = st.session_state.get("recall", {})
txn_df  = st.session_state.get("txn_df", pd.DataFrame())
stl_df  = st.session_state.get("stl_df", pd.DataFrame())

platform_total = float(result["platform_total"])
bank_total     = float(result["bank_total"])
difference     = float(result["difference"])
unexplained    = float(result["unexplained"])
gaps           = result.get("gaps", [])
stats          = result.get("stats", {})
gap_by_type    = result.get("gap_by_type", {})

exact_matched    = int(stats.get("exact_matched", 0))
rounding_matched = int(stats.get("rounding_matched", 0))
unmatched_txns   = int(stats.get("unmatched_transactions", 0))
total_txns       = int(stats.get("total_transactions", 1))
total_stls       = int(stats.get("total_settlements", 1))


# ══════════════════════════════════════════════════════════════════════════════
# KPI Cards Row
# ══════════════════════════════════════════════════════════════════════════════
match_rate = (exact_matched + rounding_matched) / max(total_txns, 1) * 100
unexp_ok   = abs(unexplained) < 0.01

k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    st.markdown(f"""
<div class="kpi-card teal">
  <div class="kpi-label">Platform Total</div>
  <div class="kpi-value">{_fmt(platform_total)}</div>
  <div class="kpi-delta muted">📒 {total_txns} transactions</div>
</div>""", unsafe_allow_html=True)

with k2:
    st.markdown(f"""
<div class="kpi-card blue">
  <div class="kpi-label">Bank Total</div>
  <div class="kpi-value">{_fmt(bank_total)}</div>
  <div class="kpi-delta muted">🏦 {total_stls} settlements</div>
</div>""", unsafe_allow_html=True)

with k3:
    diff_cls  = "bad" if abs(difference) > 0.001 else "good"
    diff_card = "red" if abs(difference) > 0.001 else "green"
    diff_msg  = "⚠ Books don't balance" if abs(difference) > 0.001 else "✓ Balanced"
    st.markdown(f"""
<div class="kpi-card {diff_card}">
  <div class="kpi-label">Difference</div>
  <div class="kpi-value">{_fmt(difference, 4)}</div>
  <div class="kpi-delta {diff_cls}">{diff_msg}</div>
</div>""", unsafe_allow_html=True)

with k4:
    st.markdown(f"""
<div class="kpi-card amber">
  <div class="kpi-label">Gaps Found</div>
  <div class="kpi-value">{len(gaps)}</div>
  <div class="kpi-delta muted">{len(set(g['type'] for g in gaps))} distinct types</div>
</div>""", unsafe_allow_html=True)

with k5:
    st.markdown(f"""
<div class="kpi-card {'green' if unexp_ok else 'red'}">
  <div class="kpi-label">Unexplained</div>
  <div class="kpi-value">{_fmt(unexplained, 4)}</div>
  <div class="kpi-delta {'good' if unexp_ok else 'bad'}">{'✓ Fully explained' if unexp_ok else '✗ Residual detected'}</div>
</div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Accounting equation ────────────────────────────────────────────────────────
parts_html = " &nbsp;+&nbsp; ".join(
    f'<strong style="color:{COLORS.get(t,"#94a3b8")}">{t}</strong> ({_fmt(float(v), 4)})'
    for t, v in gap_by_type.items()
)
unexp_cls  = "ok" if unexp_ok else "bad"
unexp_icon = "✅" if unexp_ok else "❌"
st.markdown(f"""
<div class="equation-banner">
  <strong>Accounting Equation: &nbsp;</strong>
  <span style="color:#22d3ee">{_fmt(platform_total)}</span>
  <span style="color:#94a3b8"> (platform) − </span>
  <span style="color:#22d3ee">{_fmt(bank_total)}</span>
  <span style="color:#94a3b8"> (bank) = </span>
  <strong style="color:#f8fafc">{_fmt(difference, 4)}</strong>
  <span style="color:#94a3b8"> = {parts_html or '—'} + unexplained </span>
  <span class="{unexp_cls}">{_fmt(unexplained, 4)} {unexp_icon}</span>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# Tabs
# ══════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs([
    "📊  Charts & Analysis",
    "🔍  Gap Explorer",
    "🎯  Recall vs Ground Truth",
    "📄  Raw Data",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Charts
# ─────────────────────────────────────────────────────────────────────────────
with tab1:
    col_l, col_r = st.columns(2, gap="medium")

    # ── Waterfall chart ────────────────────────────────────────────────────────
    with col_l:
        st.markdown('<div class="section-header"><div class="dot"></div>Gap Breakdown Waterfall</div>', unsafe_allow_html=True)

        wf_types  = list(gap_by_type.keys())
        wf_values = [float(v) for v in gap_by_type.values()]
        wf_colors = [COLORS.get(t, "#6b7280") for t in wf_types]

        if wf_types:
            # Build a proper waterfall with running total
            measures  = ["relative"] * len(wf_types) + ["total"]
            x_labels  = wf_types + ["TOTAL"]
            y_values  = wf_values + [sum(wf_values)]
            bar_colors = wf_colors + ["#22d3ee"]

            fig_wf = go.Figure(go.Waterfall(
                orientation="v",
                measure=measures,
                x=x_labels,
                y=y_values,
                text=[f"${abs(v):,.2f}" for v in y_values],
                textposition="outside",
                connector=dict(line=dict(color="#2e3155", width=1.5, dash="dot")),
                increasing=dict(marker=dict(color="#ef4444")),
                decreasing=dict(marker=dict(color="#22c55e")),
                totals=dict(marker=dict(color="#22d3ee")),
            ))
            fig_wf.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=20, b=10), height=320,
                font=dict(color="#94a3b8", size=11),
                xaxis=dict(showgrid=False, tickfont=dict(color="#94a3b8")),
                yaxis=dict(showgrid=True, gridcolor="#2e3155", tickfont=dict(color="#94a3b8"),
                           tickprefix="$"),
                showlegend=False,
            )
            st.plotly_chart(fig_wf, use_container_width=True, config={"displayModeBar": False})
        else:
            st.success("No gaps — perfectly balanced.")

    # ── Match donut chart ──────────────────────────────────────────────────────
    with col_r:
        st.markdown('<div class="section-header"><div class="dot" style="background:#22c55e"></div>Transaction Match Status</div>', unsafe_allow_html=True)

        donut_labels = ["Exact Match", "Tolerance Match", "Unmatched"]
        donut_values = [exact_matched, rounding_matched, unmatched_txns]
        donut_colors = ["#22c55e", "#f59e0b", "#ef4444"]

        fig_donut = go.Figure(go.Pie(
            labels=donut_labels,
            values=donut_values,
            hole=0.62,
            marker=dict(colors=donut_colors, line=dict(color="#0f1117", width=2)),
            textinfo="label+percent",
            textfont=dict(color="#94a3b8", size=11),
            hovertemplate="<b>%{label}</b><br>%{value} txns<br>%{percent}<extra></extra>",
        ))
        fig_donut.add_annotation(
            text=f"<b>{match_rate:.1f}%</b><br><span style='font-size:11px;fill:#64748b'>matched</span>",
            x=0.5, y=0.5, showarrow=False, align="center",
            font=dict(size=20, color="#f1f5f9"),
        )
        fig_donut.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=20, b=10), height=320,
            font=dict(color="#94a3b8"),
            legend=dict(orientation="v", x=1.02, y=0.5, font=dict(color="#94a3b8")),
            showlegend=True,
        )
        st.plotly_chart(fig_donut, use_container_width=True, config={"displayModeBar": False})

    # ── Bottom row: gap count bar + rounding drift timeline ──────────────────
    col_b1, col_b2 = st.columns(2, gap="medium")

    with col_b1:
        st.markdown('<div class="section-header"><div class="dot" style="background:#f59e0b"></div>Gap Count by Type</div>', unsafe_allow_html=True)
        gap_counts: dict[str, int] = {}
        for g in gaps:
            gap_counts[g["type"]] = gap_counts.get(g["type"], 0) + 1

        if gap_counts:
            fig_bar = go.Figure(go.Bar(
                x=list(gap_counts.keys()),
                y=list(gap_counts.values()),
                marker_color=[COLORS.get(t, "#6b7280") for t in gap_counts],
                text=list(gap_counts.values()),
                textposition="outside",
                textfont=dict(color="#94a3b8"),
            ))
            fig_bar.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=10, b=10), height=260,
                font=dict(color="#94a3b8", size=11),
                xaxis=dict(showgrid=False, tickfont=dict(color="#94a3b8")),
                yaxis=dict(showgrid=True, gridcolor="#2e3155", tickfont=dict(color="#94a3b8")),
                showlegend=False,
            )
            st.plotly_chart(fig_bar, use_container_width=True, config={"displayModeBar": False})

    with col_b2:
        st.markdown('<div class="section-header"><div class="dot" style="background:#8b5cf6"></div>Rounding Cumulative Drift</div>', unsafe_allow_html=True)
        rounding_gaps = [g for g in gaps if g["type"] == "ROUNDING"]
        if rounding_gaps:
            drifts   = [float(g["amount_impact"]) for g in rounding_gaps]
            cum_drift = []
            running   = 0.0
            for d in drifts:
                running += d
                cum_drift.append(running)

            fig_drift = go.Figure()
            fig_drift.add_trace(go.Scatter(
                x=list(range(1, len(cum_drift) + 1)),
                y=cum_drift,
                mode="lines+markers",
                line=dict(color="#8b5cf6", width=2.5),
                marker=dict(color="#8b5cf6", size=5),
                fill="tozeroy",
                fillcolor="rgba(139,92,246,.12)",
                hovertemplate="Txn #%{x}<br>Cumulative drift: $%{y:.4f}<extra></extra>",
            ))
            fig_drift.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                margin=dict(l=10, r=10, t=10, b=10), height=260,
                font=dict(color="#94a3b8", size=11),
                xaxis=dict(title="Rounding transaction #", showgrid=False, tickfont=dict(color="#94a3b8")),
                yaxis=dict(title="Drift ($)", showgrid=True, gridcolor="#2e3155",
                           tickfont=dict(color="#94a3b8"), tickprefix="$"),
                showlegend=False,
            )
            st.plotly_chart(fig_drift, use_container_width=True, config={"displayModeBar": False})
        else:
            st.info("No rounding gaps detected.")

    # ── Settlement timeline heatmap-style ─────────────────────────────────────
    if not txn_df.empty:
        st.markdown('<div class="section-header" style="margin-top:.5rem"><div class="dot" style="background:#22d3ee"></div>Transaction Volume by Day</div>', unsafe_allow_html=True)
        txn_df2 = txn_df.copy()
        txn_df2["date"] = txn_df2["timestamp"].str[:10]
        txn_df2["amount_f"] = pd.to_numeric(txn_df2["amount"], errors="coerce")
        daily = txn_df2.groupby("date").agg(count=("txn_id","count"), volume=("amount_f","sum")).reset_index()
        daily["date"] = pd.to_datetime(daily["date"])

        fig_timeline = go.Figure()
        fig_timeline.add_trace(go.Bar(
            x=daily["date"], y=daily["count"],
            name="# Transactions",
            marker_color="#3b82f6",
            opacity=0.85,
            yaxis="y",
        ))
        fig_timeline.add_trace(go.Scatter(
            x=daily["date"], y=daily["volume"],
            name="Volume ($)",
            line=dict(color="#22d3ee", width=2),
            mode="lines",
            yaxis="y2",
        ))
        fig_timeline.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=10, b=10), height=240,
            font=dict(color="#94a3b8", size=11),
            legend=dict(orientation="h", x=0, y=1.1, font=dict(color="#94a3b8")),
            xaxis=dict(showgrid=False, tickfont=dict(color="#94a3b8")),
            yaxis=dict(title="# Txns", showgrid=True, gridcolor="#2e3155",
                       tickfont=dict(color="#94a3b8")),
            yaxis2=dict(title="Volume ($)", overlaying="y", side="right",
                        showgrid=False, tickfont=dict(color="#22d3ee"), tickprefix="$"),
        )
        st.plotly_chart(fig_timeline, use_container_width=True, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Gap Explorer
# ─────────────────────────────────────────────────────────────────────────────
with tab2:
    if not gaps:
        st.success("✅ No gaps detected — perfect reconciliation!")
    else:
        # filter row
        fa, fb, fc = st.columns([2, 1.2, 0.8])
        with fa:
            search = st.text_input("🔎 Search IDs or explanations", placeholder="e.g. TXN01011 or orphan")
        with fb:
            all_types  = sorted(set(g["type"] for g in gaps))
            type_filter = st.multiselect("Gap type", all_types, default=all_types)
        with fc:
            conf_min = st.slider("Min confidence", 0, 100, 0, step=5)

        rows = []
        for g in gaps:
            ids_str = " | ".join(f"{k}: {v}" for k, v in g["ids"].items()) \
                      if isinstance(g["ids"], dict) else str(g["ids"])
            conf_pct = int(g["confidence"] * 100)
            rows.append({
                "Type":          g["type"],
                "IDs":           ids_str,
                "Amount Impact": float(g["amount_impact"]),
                "Explanation":   g["explanation"],
                "Conf %":        conf_pct,
            })

        gap_df = pd.DataFrame(rows)
        if type_filter:
            gap_df = gap_df[gap_df["Type"].isin(type_filter)]
        if search:
            gap_df = gap_df[gap_df.apply(lambda r: search.lower() in r.to_string().lower(), axis=1)]
        if conf_min:
            gap_df = gap_df[gap_df["Conf %"] >= conf_min]

        # Summary mini-metrics
        ma, mb, mc, md = st.columns(4)
        ma.metric("Showing", len(gap_df))
        mb.metric("Total", len(gaps))
        mc.metric("Net Impact", f"${gap_df['Amount Impact'].sum():,.4f}")
        md.metric("Avg Confidence", f"{gap_df['Conf %'].mean():.0f}%" if len(gap_df) else "—")

        # Color-code type column
        def _color_type(val):
            palette = {
                "TIMING": "background-color:rgba(59,130,246,.2);color:#93c5fd",
                "ROUNDING": "background-color:rgba(245,158,11,.2);color:#fde68a",
                "DUPLICATE": "background-color:rgba(139,92,246,.2);color:#c4b5fd",
                "ORPHAN_REFUND": "background-color:rgba(239,68,68,.2);color:#fca5a5",
                "MISSING": "background-color:rgba(107,114,128,.2);color:#9ca3af",
            }
            return palette.get(val, "")

        styled = gap_df.style.map(_color_type, subset=["Type"])

        st.dataframe(
            styled,
            hide_index=True,
            use_container_width=True,
            height=min(600, 56 + len(gap_df) * 36),
            column_config={
                "Amount Impact": st.column_config.NumberColumn(format="$%.4f"),
                "Conf %":        st.column_config.ProgressColumn(min_value=0, max_value=100, format="%d%%"),
                "Explanation":   st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(f"Showing {len(gap_df)} of {len(gaps)} gaps")

        # Per-type breakdown table
        st.markdown("---")
        st.markdown('<div class="section-header"><div class="dot" style="background:#f59e0b"></div>Amount Impact by Gap Type</div>', unsafe_allow_html=True)
        breakdown = gap_df.groupby("Type").agg(
            Count=("Type","count"),
            Total_Impact=("Amount Impact","sum"),
            Avg_Confidence=("Conf %","mean"),
        ).reset_index()
        breakdown["Total_Impact"] = breakdown["Total_Impact"].map(lambda x: f"${x:,.4f}")
        breakdown["Avg_Confidence"] = breakdown["Avg_Confidence"].map(lambda x: f"{x:.0f}%")
        st.dataframe(breakdown, hide_index=True, use_container_width=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — Recall vs Ground Truth
# ─────────────────────────────────────────────────────────────────────────────
with tab3:
    if not recall or not recall.get("rows"):
        st.info("Ground truth data not available. This panel works with the synthetic dataset.")
    else:
        pct = recall.get("overall_recall", 0) * 100
        pct_color = "#22c55e" if pct >= 100 else "#f59e0b"

        # Big recall score
        rc1, rc2, rc3 = st.columns([1, 1, 2])
        with rc1:
            st.markdown(f"""
<div class="kpi-card green">
  <div class="kpi-label">Overall Recall</div>
  <div class="kpi-value" style="color:{pct_color}">{pct:.0f}%</div>
  <div class="kpi-delta good">{'✓ All gaps caught' if pct >= 100 else '⚠ Some gaps missed'}</div>
</div>""", unsafe_allow_html=True)
        with rc2:
            planted = len([r for r in recall["rows"] if "NOT PLANTED" not in r["planted_description"]])
            caught  = len([r for r in recall["rows"] if r["detected"] and "NOT PLANTED" not in r["planted_description"]])
            st.markdown(f"""
<div class="kpi-card teal">
  <div class="kpi-label">Caught / Planted</div>
  <div class="kpi-value">{caught} / {planted}</div>
  <div class="kpi-delta muted">Gap types</div>
</div>""", unsafe_allow_html=True)
        with rc3:
            st.markdown(f"""
<div style="background:#1A1D2E; border:1px solid #2E3155; border-radius:12px; padding:1.1rem 1.3rem;">
  <div style="font-size:.8rem;color:#64748b;margin-bottom:.4rem;text-transform:uppercase;letter-spacing:.07em">What is Recall?</div>
  <div style="font-size:.85rem;color:#94a3b8">
    Recall measures the fraction of <em>planted</em> gaps the engine successfully detected.
    100% means every planted gap type (TIMING, ROUNDING, DUPLICATE, ORPHAN_REFUND)
    was caught. False positives are gaps reported but not planted.
  </div>
</div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Recall table with status badges
        recall_rows = []
        for r in recall["rows"]:
            is_fp  = "NOT PLANTED" in r["planted_description"]
            status = "🟡 FALSE POS" if is_fp else ("✅ CAUGHT" if r["detected"] else "❌ MISSED")
            recall_rows.append({
                "Gap Type":        r["planted_type"],
                "Description":     r["planted_description"],
                "Planted Amount":  float(r["planted_amount_impact"]),
                "Detected Count":  r["detected_count"],
                "Status":          status,
            })
        recall_df = pd.DataFrame(recall_rows)
        st.dataframe(
            recall_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Planted Amount": st.column_config.NumberColumn(format="$%.4f"),
                "Detected Count": st.column_config.NumberColumn(format="%d"),
            },
        )

        # Recall radar / bar chart
        st.markdown('<div class="section-header" style="margin-top:1rem"><div class="dot" style="background:#22c55e"></div>Detection Status per Planted Type</div>', unsafe_allow_html=True)
        planted_rows = [r for r in recall["rows"] if "NOT PLANTED" not in r["planted_description"]]
        fig_recall = go.Figure(go.Bar(
            x=[r["planted_type"] for r in planted_rows],
            y=[1 if r["detected"] else 0 for r in planted_rows],
            marker_color=["#22c55e" if r["detected"] else "#ef4444" for r in planted_rows],
            text=["✓ Caught" if r["detected"] else "✗ Missed" for r in planted_rows],
            textposition="inside",
            textfont=dict(color="#fff", size=13),
        ))
        fig_recall.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=10, b=10), height=200,
            font=dict(color="#94a3b8"),
            xaxis=dict(showgrid=False, tickfont=dict(color="#94a3b8")),
            yaxis=dict(showgrid=False, visible=False, range=[0, 1.3]),
            showlegend=False,
        )
        st.plotly_chart(fig_recall, use_container_width=True, config={"displayModeBar": False})


# ─────────────────────────────────────────────────────────────────────────────
# TAB 4 — Raw Data
# ─────────────────────────────────────────────────────────────────────────────
with tab4:
    dt1, dt2, dt3 = st.tabs(["Transactions", "Settlements", "Ground Truth"])

    with dt1:
        if not txn_df.empty:
            # Type distribution mini-chart
            tc1, tc2 = st.columns([3, 1])
            with tc1:
                search_t = st.text_input("Search transactions", placeholder="customer ID, type, status…", key="st_txn")
            with tc2:
                st.markdown(f"**{len(txn_df)} rows**")

            show_txn = txn_df.copy()
            if search_t:
                show_txn = show_txn[show_txn.apply(lambda r: search_t.lower() in r.to_string().lower(), axis=1)]
            st.dataframe(show_txn, hide_index=True, use_container_width=True, height=400)

            # Type breakdown
            type_counts = txn_df["type"].value_counts().reset_index()
            type_counts.columns = ["Type", "Count"]
            fig_types = px.pie(type_counts, names="Type", values="Count",
                               color_discrete_sequence=["#3b82f6","#8b5cf6"],
                               hole=0.5)
            fig_types.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                    margin=dict(l=0,r=0,t=0,b=0), height=180,
                                    font=dict(color="#94a3b8"), showlegend=True,
                                    legend=dict(font=dict(color="#94a3b8")))
            st.caption("Transaction type split:")
            st.plotly_chart(fig_types, use_container_width=True, config={"displayModeBar": False})

    with dt2:
        if not stl_df.empty:
            sc1, sc2 = st.columns([3, 1])
            with sc1:
                search_s = st.text_input("Search settlements", placeholder="batch_id, settlement_date…", key="st_stl")
            with sc2:
                st.markdown(f"**{len(stl_df)} rows**")

            show_stl = stl_df.copy()
            if search_s:
                show_stl = show_stl[show_stl.apply(lambda r: search_s.lower() in r.to_string().lower(), axis=1)]
            st.dataframe(show_stl, hide_index=True, use_container_width=True, height=400)

    with dt3:
        if GT_PATH.exists():
            with open(GT_PATH) as f:
                gt = json.load(f)
            st.json(gt)
        else:
            st.info("Ground truth file not found.")
