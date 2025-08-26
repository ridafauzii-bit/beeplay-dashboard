# BEE PLAY FINANCIAL REPORT - Streamlit Dashboard
# Roles + Sidebar Settings + Inline Edit/Delete in "Transactions (in range)"
# Run: python -m streamlit run app.py

import io
import uuid
import calendar
from datetime import date, timedelta
from typing import Tuple, Dict, Optional

import numpy as np
import pandas as pd
import streamlit as st

# PDF + Excel helpers
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

st.set_page_config(page_title="BEE PLAY Financial Report", layout="wide", page_icon="🎮")

# ---------- USERS ----------
USERS = {
    "staff1": {"password": "1234", "role": "staff"},  # input-only
    "team1":  {"password": "2025", "role": "team"},   # full access
}

# ---------- THEME ----------
THEME_CSS = """
<style>
:root { --bg:#0b0f19; --panel:#10172a; --input:#1e293b; --border:#334155;
        --primary:#8b5cf6; --accent:#22d3ee; --text:#f1f5f9; --muted:#cbd5e1; }
header, .stApp { background: var(--bg) !important; color: var(--text) !important; }
.block-container { padding-top: 1rem; }
h1,h2,h3,h4,h5 { color: var(--accent) !important; }
p, span, div, label { color: var(--text) !important; }
.stMarkdown p { color: var(--text) !important; }
.stButton>button { background: linear-gradient(90deg,var(--primary),var(--accent)) !important;
  border:0 !important; color:#fff !important; font-weight:600 !important; border-radius:10px !important; padding:.45em 1.2em !important; }
div[data-testid="stMetric"] { background: var(--panel) !important; padding:14px !important; border-radius:12px !important;
  border:1px solid rgba(255,255,255,.08) !important; color:var(--text) !important; }
div[data-testid="stMetricLabel"] { color: var(--muted) !important; }
hr { border-color: rgba(255,255,255,.2) !important; }
/* Inputs dark */
.stTextInput>div>div>input, .stNumberInput>div>div>input, .stDateInput>div>div>input, .stSelectbox>div>div>div {
  background: var(--input) !important; color: var(--text) !important; border:1px solid var(--border) !important; border-radius:8px !important;
}
/* Expander */
.streamlit-expanderHeader{ background:var(--panel) !important; color:var(--text) !important; font-weight:600 !important; border:1px solid var(--border) !important; border-radius:8px !important;}
.streamlit-expanderContent{ background:var(--panel) !important; border-left:1px solid var(--border) !important; border-right:1px solid var(--border) !important; border-bottom:1px solid var(--border) !important; border-radius:0 0 8px 8px !important;}
/* Table */
[data-testid="stDataFrame"] div[role="grid"]{ background:#0f172a !important; }
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)

# ---------- HELPERS ----------
def format_idr(x: float) -> str:
    try:
        if pd.isna(x): return "Rp0"
        x = float(x)
        return "Rp{:,.0f}".format(x).replace(",", ".")
    except Exception:
        return "Rp0"

def start_end_of_week(d: date):
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end

def start_end_of_month(d: date):
    start = d.replace(day=1)
    last_day = calendar.monthrange(d.year, d.month)[1]
    end = d.replace(day=last_day)
    return start, end

def daily_fixed_from_monthly(monthly_fixed: float) -> float:
    return (monthly_fixed * 12.0) / 365.0

def excel_bytes(transactions: pd.DataFrame, summary: Dict[str, float]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        tx = transactions.copy()
        if not tx.empty and pd.api.types.is_datetime64_any_dtype(tx["date"]):
            tx["date"] = tx["date"].dt.strftime("%Y-%m-%d")
        tx.to_excel(writer, sheet_name="Transactions", index=False)
        summary_df = pd.DataFrame([{"Metric": k, "Value": v if isinstance(v, (int, float)) else str(v)} for k, v in summary.items()])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)
        wb = writer.book
        ws = writer.sheets["Summary"]
        currency_fmt = wb.add_format({'num_format': '#,##0', 'align': 'left'})
        ws.set_column(0, 0, 32); ws.set_column(1, 1, 20, currency_fmt)
    return output.getvalue()

def pdf_bytes(title: str, lines: Dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    c.setFont("Helvetica-Bold", 18); c.drawString(20*mm, height - 25*mm, title)
    c.setFont("Helvetica", 11); y = height - 35*mm
    for k, v in lines.items():
        c.drawString(20*mm, y, f"{k}: {v}"); y -= 8*mm
        if y < 30*mm: c.showPage(); c.setFont("Helvetica", 11); y = height - 25*mm
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(20*mm, 15*mm, f"Generated {date.today().strftime('%Y-%m-%d')} • BEE PLAY FINANCIAL REPORT")
    c.showPage(); c.save()
    return buffer.getvalue()

def init_state():
    if "auth" not in st.session_state:
        st.session_state.auth = {"username": None, "role": None}
    if "transactions" not in st.session_state:
        st.session_state.transactions = pd.DataFrame({
            "date": pd.to_datetime([], errors="coerce"),
            "package": pd.Series([], dtype="object"),
            "hours": pd.Series([], dtype="float"),
            "rental_revenue": pd.Series([], dtype="float"),
            "snack_type": pd.Series([], dtype="object"),
            "snack_revenue": pd.Series([], dtype="float"),
            "tx_id": pd.Series([], dtype="object"),
        })
    if "config" not in st.session_state:
        st.session_state.config = {
            "brand": "BEE PLAY FINANCIAL REPORT",
            "num_consoles": 7,
            "open_hours": 14,
            "initial_capital": 0.0,
            "monthly_target_revenue": 1_000_000.0,
            "weekly_target_revenue": 500_000.0,
            "fixed_costs": {"Rent": 0.0, "Salaries": 0.0, "Internet": 0.0, "Electricity": 0.0, "Misc": 0.0},
            "variable_per_hour": 0.0,
            "snack_cogs_pct": 40.0,
        }

def ensure_tx_ids():
    df = st.session_state.transactions
    if df.empty:
        return
    df = df.copy()
    if "tx_id" not in df.columns:
        df["tx_id"] = None
    mask = df["tx_id"].isna() | (df["tx_id"] == "")
    if mask.any():
        df.loc[mask, "tx_id"] = [str(uuid.uuid4()) for _ in range(mask.sum())]
    st.session_state.transactions = df

def load_csv(path="transactions.csv"):
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        needed = ["date","package","hours","rental_revenue","snack_type","snack_revenue"]
        for col in needed:
            if col not in df.columns: return False
        if "tx_id" not in df.columns:
            df["tx_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
        st.session_state.transactions = df
        ensure_tx_ids()
        return True
    except Exception:
        return False

def save_csv(path="transactions.csv"):
    st.session_state.transactions.to_csv(path, index=False)

def add_sample_data():
    today = date.today()
    start = today - timedelta(days=20)
    rng = pd.date_range(start, periods=18, freq="D")
    np.random.seed(7)
    packages = ["PS4-1h","PS4-2h","PS5-1h","PS5-2h","VIP-3h"]
    snacks = ["Soda", "Popcorn", "Noodles", "Coffee", ""]
    rows = []
    for d in rng:
        tx_count = np.random.randint(3, 8)
        for _ in range(tx_count):
            pkg = np.random.choice(packages)
            hrs = 1 if "1h" in pkg else (2 if "2h" in pkg else 3)
            base = 12000 if "PS4" in pkg else 18000
            price = base * hrs
            snack_type = np.random.choice(snacks, p=[0.25,0.25,0.2,0.1,0.2])
            snack_rev = 0 if snack_type == "" else np.random.choice([5000, 10000, 15000], p=[0.5,0.35,0.15])
            rows.append([d, pkg, float(hrs), float(price), snack_type, float(snack_rev), str(uuid.uuid4())])
    df = pd.DataFrame(rows, columns=["date","package","hours","rental_revenue","snack_type","snack_revenue","tx_id"])
    st.session_state.transactions = df
    ensure_tx_ids()

def filter_today(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    today = date.today()
    return df[df["date"].dt.date == today].copy()

# ---------- AUTH ----------
def login_ui():
    st.markdown("## 🔐 Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")
    if st.button("Sign in"):
        user = USERS.get(u)
        if user and user["password"] == p:
            st.session_state.auth = {"username": u, "role": user["role"]}
            st.success(f"Welcome, {u} ({user['role']})")
            st.rerun()
        else:
            st.error("Invalid username or password")

def require_auth() -> Optional[str]:
    auth = st.session_state.auth
    return auth["role"]

# ---------- KPI / FINANCE ----------
def kpis_for_range(df: pd.DataFrame, cfg: dict, start: date, end: date) -> dict:
    rental_rev = df["rental_revenue"].sum() if not df.empty else 0.0
    snack_rev  = df["snack_revenue"].sum() if not df.empty else 0.0
    total_rev  = rental_rev + snack_rev
    hours_total = df["hours"].sum() if not df.empty else 0.0
    tx_count = len(df)

    days = (end - start).days + 1
    available_hours = cfg["num_consoles"] * cfg["open_hours"] * max(days, 1)
    utilization = (hours_total / available_hours * 100.0) if available_hours > 0 else 0.0
    utilization = min(utilization, 100.0)

    monthly_fixed_total = sum(cfg["fixed_costs"].values())
    fixed_daily = daily_fixed_from_monthly(monthly_fixed_total)
    fixed_alloc = fixed_daily * max(days, 1)

    variable_from_hours = cfg["variable_per_hour"] * hours_total
    variable_from_snacks = (cfg["snack_cogs_pct"]/100.0) * snack_rev
    variable_total = variable_from_hours + variable_from_snacks

    gross_profit = total_rev - variable_total
    operating_profit = gross_profit - fixed_alloc
    net_profit = operating_profit

    cm = total_rev - variable_total
    cmr = (cm / total_rev) if total_rev > 0 else 0.0
    bep_revenue_monthly = (monthly_fixed_total / cmr) if cmr > 0 else None

    return {
        "rental_revenue": rental_rev,
        "snack_revenue": snack_rev,
        "total_revenue": total_rev,
        "hours_total": hours_total,
        "tx_count": tx_count,
        "utilization_pct": utilization,
        "fixed_alloc": fixed_alloc,
        "variable_total": variable_total,
        "gross_profit": gross_profit,
        "net_profit": net_profit,
        "cmr": cmr,
        "bep_revenue_monthly": bep_revenue_monthly,
    }

def cumulative_profit_and_roi(cfg: dict) -> Tuple[float, float]:
    df = st.session_state.transactions
    if df.empty: return 0.0, 0.0
    df2 = df.copy()
    df2["date_only"] = df2["date"].dt.date
    daily = df2.groupby("date_only").agg({"rental_revenue":"sum","snack_revenue":"sum","hours":"sum"}).reset_index()
    monthly_fixed_total = sum(cfg["fixed_costs"].values())
    fixed_daily = daily_fixed_from_monthly(monthly_fixed_total)
    variable_from_hours = daily["hours"] * cfg["variable_per_hour"]
    variable_from_snacks = (cfg["snack_cogs_pct"]/100.0) * daily["snack_revenue"]
    var_total = variable_from_hours + variable_from_snacks
    gross_profit = (daily["rental_revenue"] + daily["snack_revenue"]) - var_total
    net_profit = gross_profit - fixed_daily
    cumulative = float(net_profit.sum())
    initial_capital = cfg.get("initial_capital", 0.0)
    roi_pct = (cumulative / initial_capital * 100.0) if initial_capital > 0 else 0.0
    return cumulative, float(roi_pct)

# ---------- UI BLOCKS ----------
def sidebar_settings(cfg):
    with st.sidebar:
        st.markdown("## ⚙️ Settings")
        st.markdown(f"**Brand:** {cfg['brand']}")
        st.divider()

        st.markdown("### 🏪 Operations")
        cfg["num_consoles"] = st.number_input("Number of consoles", 1, 100, cfg["num_consoles"])
        cfg["open_hours"]   = st.number_input("Operating hours per day", 1, 24, cfg["open_hours"])

        st.markdown("### 💰 Targets")
        cfg["weekly_target_revenue"]  = st.number_input("Weekly target revenue (IDR)", 0, 10_000_000, int(cfg["weekly_target_revenue"]))
        cfg["monthly_target_revenue"] = st.number_input("Monthly target revenue (IDR)", 0, 50_000_000, int(cfg["monthly_target_revenue"]))

        st.markdown("### 🧾 Fixed Monthly Costs (IDR)")
        new_fixed = {}
        for k, v in cfg["fixed_costs"].items():
            new_fixed[k] = float(st.number_input(k, 0, 1_000_000_000, int(v)))
        cfg["fixed_costs"] = new_fixed

        st.markdown("### 🧪 Variable Costs")
        cfg["variable_per_hour"] = float(st.number_input("Variable cost per rental hour (IDR)", 0, 100_000, int(cfg["variable_per_hour"])))
        cfg["snack_cogs_pct"]    = float(st.slider("Snack COGS (% of snack revenue)", 0.0, 100.0, cfg["snack_cogs_pct"], 1.0))

        st.markdown("### 🏗️ Capital")
        cfg["initial_capital"] = float(st.number_input("Initial capital (IDR)", 0, 5_000_000_000, int(cfg["initial_capital"])))

        st.divider()
        st.markdown("### 💾 Data")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            if st.button("📥 Load CSV"):
                ok = load_csv()
                st.success("Loaded transactions.csv") if ok else st.error("Failed to load transactions.csv")
        with col_b:
            if st.button("📤 Save CSV"):
                save_csv()
                st.success("Saved to transactions.csv")
        with col_c:
            if st.button("🧪 Load Sample Data"):
                add_sample_data()
                st.success("Sample data loaded")

        st.markdown("---")
        if st.button("🚪 Logout"):
            st.session_state.auth = {"username": None, "role": None}
            st.success("Logged out.")
            st.rerun()

def upload_widget():
    st.markdown("### ⬆️ Upload Transactions (Excel/CSV)")
    f = st.file_uploader("Choose .xlsx or .csv", type=["xlsx","csv"])
    if f is not None:
        try:
            if f.name.lower().endswith(".xlsx"):
                df_new = pd.read_excel(f)
            else:
                df_new = pd.read_csv(f)

            # normalize columns
            df_new.columns = [c.lower().strip().replace(" ", "_") for c in df_new.columns]
            needed = ["date","package","hours","rental_revenue","snack_type","snack_revenue"]
            missing = [c for c in needed if c not in df_new.columns]
            if missing:
                st.error(f"Missing columns in file: {missing}")
                return

            # types
            df_new["date"] = pd.to_datetime(df_new["date"], errors="coerce")
            for col in ["hours","rental_revenue","snack_revenue"]:
                df_new[col] = pd.to_numeric(df_new[col], errors="coerce").fillna(0.0)
            df_new["snack_type"] = df_new["snack_type"].astype(str)

            # assign tx_id if not provided
            if "tx_id" not in df_new.columns:
                df_new["tx_id"] = [str(uuid.uuid4()) for _ in range(len(df_new))]

            # append
            cols = ["date","package","hours","rental_revenue","snack_type","snack_revenue","tx_id"]
            st.session_state.transactions = pd.concat([st.session_state.transactions, df_new[cols]], ignore_index=True)
            ensure_tx_ids()
            st.success(f"Imported {len(df_new)} rows.")
        except Exception as e:
            st.error(f"Failed to read file: {e}")

def add_transaction_form():
    with st.expander("Add Transaction (manual entry)", expanded=True):
        col1, col2, col3, col4, col5, col6 = st.columns([1.6,1.6,1.1,1.6,1.6,1.2])
        t_date = col1.date_input("Date", value=date.today())
        t_package = col2.text_input("Package (e.g., PS5-2h)")
        t_hours = float(col3.number_input("Hours", min_value=0.0, max_value=24.0, value=1.0, step=0.5))
        t_rental = float(col4.number_input("Rental revenue (IDR)", min_value=0, value=0, step=1000))
        t_snack_type = col5.text_input("Snack type (e.g., Soda; Popcorn)")
        t_snack = float(col6.number_input("Snack revenue (IDR)", min_value=0, value=0, step=1000))
        b1, b2, _ = st.columns([1,1,6])
        if b1.button("➕ Add"):
            new_row = pd.DataFrame({
                "date":[pd.to_datetime(t_date)],
                "package":[t_package],
                "hours":[t_hours],
                "rental_revenue":[t_rental],
                "snack_type":[t_snack_type],
                "snack_revenue":[t_snack],
                "tx_id":[str(uuid.uuid4())],
            })
            st.session_state.transactions = pd.concat([st.session_state.transactions, new_row], ignore_index=True)
            ensure_tx_ids()
            st.success("Transaction added.")
        if b2.button("🧹 Clear Form"):
            st.rerun()

# ---------- MAIN ----------
init_state()
ensure_tx_ids()
cfg = st.session_state.config

# AUTH
role = require_auth()
if not role:
    login_ui()
    st.stop()

# HEADER
st.markdown(f"# 🎮 {cfg['brand']}")

# STAFF VIEW (input-only)
if role == "staff":
    with st.sidebar:
        st.markdown("---")
        if st.button("🚪 Logout"):
            st.session_state.auth = {"username": None, "role": None}
            st.success("Logged out.")
            st.rerun()

    st.info("**Staff Mode** — input transactions & upload files. Only today's records are shown.")
    add_transaction_form()
    upload_widget()

    df_today = filter_today(st.session_state.transactions)
    st.subheader("🧾 Today's Transactions")
    st.dataframe(
        df_today.sort_values("date", ascending=False).assign(
            date=lambda d: d["date"].dt.strftime("%Y-%m-%d"),
            rental_revenue=lambda d: d["rental_revenue"].map(format_idr),
            snack_revenue=lambda d: d["snack_revenue"].map(format_idr),
            hours=lambda d: d["hours"].round(2),
        ),
        use_container_width=True
    )
    c1, c2 = st.columns(2)
    with c1:
        if st.button("📤 Save CSV"):
            save_csv(); st.success("Saved to transactions.csv")
    with c2:
        if st.button("📥 Load CSV"):
            ok = load_csv(); st.success("Loaded transactions.csv") if ok else st.error("Failed to load transactions.csv")
    st.stop()

# TEAM VIEW (full dashboard)
def filter_by_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty: return df.copy()
    mask = (df['date'].dt.date >= start) & (df['date'].dt.date <= end)
    return df.loc[mask].copy()

# Sidebar settings (includes Initial Capital)
sidebar_settings(cfg)

# Period filters
colf = st.columns([2,2,2,4])
with colf[0]:
    period = st.selectbox("Period", ["Today","This Week","This Month","This Quarter","This Year","Custom Range"], index=2)
with colf[1]:
    ref_date = st.date_input("Reference date", date.today())
with colf[2]:
    if period == "Custom Range":
        custom = st.date_input("Custom range", [date.today() - timedelta(days=7), date.today()])
        start, end = custom[0], custom[1]
    elif period == "Today":
        start, end = ref_date, ref_date
    elif period == "This Week":
        start, end = start_end_of_week(ref_date)
    elif period == "This Month":
        start, end = start_end_of_month(ref_date)
    elif period == "This Quarter":
        q = (ref_date.month - 1)//3 + 1
        start = date(ref_date.year, 3*(q-1)+1, 1)
        last = calendar.monthrange(ref_date.year, 3*(q-1)+3)[1]
        end = date(ref_date.year, 3*(q-1)+3, last)
    elif period == "This Year":
        start, end = date(ref_date.year,1,1), date(ref_date.year,12,31)
with colf[3]:
    st.markdown(f"**Active Range:** {start} → {end}")

df_all = st.session_state.transactions
df_range = filter_by_range(df_all, start, end)

# KPIs
k = kpis_for_range(df_range, cfg, start, end)
cumul_profit, roi_pct = cumulative_profit_and_roi(cfg)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Revenue", format_idr(k["total_revenue"]))
c2.metric("Snack Sales", format_idr(k["snack_revenue"]))
c3.metric("Transactions", k["tx_count"])
c4.metric("Utilization", f"{k['utilization_pct']:.1f}%")
c5.metric("P&L (Net Profit)", format_idr(k["net_profit"]))

# Progress vs Targets
with st.expander("📊 Progress vs Targets (weekly & monthly)", expanded=False):
    wk_start, wk_end = start_end_of_week(ref_date)
    df_wk = filter_by_range(df_all, wk_start, wk_end)
    rev_week = df_wk["rental_revenue"].sum() + df_wk["snack_revenue"].sum()
    weekly_target = max(cfg["weekly_target_revenue"], 1.0)
    st.write(f"**This Week ({wk_start} → {wk_end})**  |  Revenue: {format_idr(rev_week)}  |  Target: {format_idr(weekly_target)}")
    st.progress(min(rev_week/weekly_target, 1.0))

    mo_start, mo_end = start_end_of_month(ref_date)
    df_mo = filter_by_range(df_all, mo_start, mo_end)
    rev_month = df_mo["rental_revenue"].sum() + df_mo["snack_revenue"].sum()
    monthly_target = max(cfg["monthly_target_revenue"], 1.0)
    st.write(f"**This Month ({mo_start} → {mo_end})**  |  Revenue: {format_idr(rev_month)}  |  Target: {format_idr(monthly_target)}")
    st.progress(min(rev_month/monthly_target, 1.0))

# Charts
left, right = st.columns([3,2])
with left:
    st.subheader("📈 Daily Revenue Trend")
    if df_range.empty:
        st.info("No data in selected range.")
    else:
        df_plot = (df_range.assign(day=lambda d: d["date"].dt.date)
                   .groupby("day")[["rental_revenue","snack_revenue"]].sum())
        df_plot["total_revenue"] = df_plot["rental_revenue"] + df_plot["snack_revenue"]
        st.line_chart(df_plot["total_revenue"])

with right:
    st.subheader("🥧 Revenue Breakdown (Pie)")
    if df_range.empty:
        st.info("No data in selected range.")
    else:
        total_r = k["rental_revenue"]; total_s = k["snack_revenue"]
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots()
        ax.pie([total_r, total_s], labels=["Rental","Snacks"], autopct="%1.1f%%", startangle=90)
        ax.axis('equal')
        st.pyplot(fig)

st.divider()

# ================== Transactions (in range) — EDIT & DELETE ==================
st.subheader("🧾 Transactions (in range) — edit & delete")

if df_range.empty:
    st.info("No transactions in the selected range.")
else:
    view_cols = ["date","package","hours","rental_revenue","snack_type","snack_revenue","tx_id"]
    view = df_range[view_cols].copy()

    # Editor-friendly types
    view["date"] = pd.to_datetime(view["date"], errors="coerce").dt.date
    for c in ["hours","rental_revenue","snack_revenue"]:
        view[c] = pd.to_numeric(view[c], errors="coerce")

    edited_view = st.data_editor(
        view.sort_values("date", ascending=False),
        use_container_width=True,
        num_rows="fixed",  # add via form/upload; here we edit existing
        column_config={
            "date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "hours": st.column_config.NumberColumn(step=0.5, min_value=0.0),
            "rental_revenue": st.column_config.NumberColumn(step=1000, min_value=0),
            "snack_revenue": st.column_config.NumberColumn(step=1000, min_value=0),
            "tx_id": st.column_config.TextColumn(disabled=True, help="Row ID (read-only)"),
        },
        key="tx_editor_inline",
    )

    # Build labels for deletion selector
    label_df = edited_view.copy()
    label_df["date"] = pd.to_datetime(label_df["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    label_df["label"] = (
        label_df["date"] + " | " +
        label_df["package"].astype(str) + " | Rp" +
        label_df["rental_revenue"].fillna(0).astype(int).astype(str) +
        " | ID:" + label_df["tx_id"].astype(str).str[-6:]
    )
    del_choices = {row["label"]: row["tx_id"] for _, row in label_df.iterrows()}
    dcol1, dcol2 = st.columns([3, 1])
    with dcol1:
        to_delete = st.multiselect("Select transaction(s) to delete", options=list(del_choices.keys()))
    with dcol2:
        st.write("")  # spacing
        delete_click = st.button("🗑️ Delete selected", use_container_width=True)

    # SAVE changes
    if st.button("💾 Save changes"):
        master = st.session_state.transactions.copy()
        edited = edited_view.copy()
        edited["date"] = pd.to_datetime(edited["date"], errors="coerce")
        for c in ["hours","rental_revenue","snack_revenue"]:
            edited[c] = pd.to_numeric(edited[c], errors="coerce")

        if "tx_id" not in master.columns:
            master["tx_id"] = None
        master_idx = master.set_index("tx_id")
        edited_idx = edited.set_index("tx_id")

        for col in ["date","package","hours","rental_revenue","snack_type","snack_revenue"]:
            master_idx.loc[edited_idx.index, col] = edited_idx[col]

        st.session_state.transactions = master_idx.reset_index()
        ensure_tx_ids()
        st.success("Changes saved.")
        st.rerun()

    # DELETE selected
    if delete_click:
        if not to_delete:
            st.warning("No rows selected.")
        else:
            del_ids = set(del_choices[label] for label in to_delete)
            before = len(st.session_state.transactions)
            st.session_state.transactions = st.session_state.transactions[
                ~st.session_state.transactions["tx_id"].isin(del_ids)
            ].reset_index(drop=True)
            ensure_tx_ids()
            after = len(st.session_state.transactions)
            st.success(f"Deleted {before - after} transaction(s).")
            st.rerun()
# ============================================================================

# Input & Upload (also available to team)
st.markdown("### ➕ Add / Upload")
add_transaction_form()
upload_widget()

# Export
st.subheader("📦 Reports & Export")
summary_export = {
    "Period": f"{start} to {end}",
    "Transactions": int(k["tx_count"]),
    "Hours Total": float(df_range["hours"].sum()) if not df_range.empty else 0.0,
    "Rental Revenue": int(k["rental_revenue"]),
    "Snack Revenue": int(k["snack_revenue"]),
    "Total Revenue": int(k["total_revenue"]),
    "Variable Costs": int(k["variable_total"]),
    "Fixed Allocated": int(k["fixed_alloc"]),
    "Gross Profit": int(k["gross_profit"]),
    "Net Profit": int(k["net_profit"]),
    "Utilization %": float(k["utilization_pct"]),
    "Contribution Margin Ratio (%)": round(float(k["cmr"])*100.0, 2),
    "BEP Revenue (Monthly)": int(k["bep_revenue_monthly"]) if k["bep_revenue_monthly"] is not None else "N/A",
}
cumul_profit, roi_pct = cumulative_profit_and_roi(cfg)
summary_export["Cumulative Profit (all-time)"] = int(cumul_profit)
summary_export["ROI % (all-time)"] = round(float(roi_pct), 2)

colx, coly = st.columns(2)
with colx:
    x_bytes = excel_bytes(df_range, summary_export)
    st.download_button("⬇️ Download Excel (Transactions + Summary)", x_bytes,
        file_name=f"BeePlay_Report_{start}_to_{end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
with coly:
    pdf_lines = {k: (format_idr(v) if isinstance(v, (int,float)) and ("%" not in k) else str(v)) for k, v in summary_export.items()}
    pdf_bytes_data = pdf_bytes("BEE PLAY - Financial Summary", pdf_lines)
    st.download_button("⬇️ Download PDF (Summary)", pdf_bytes_data,
        file_name=f"BeePlay_Summary_{start}_to_{end}.pdf",
        mime="application/pdf")

st.markdown("---")
st.markdown("**Tips**: Staff sees input-only view (today). Team sees full analytics. Use the sidebar to set Initial Capital, Costs, Targets, etc.")
