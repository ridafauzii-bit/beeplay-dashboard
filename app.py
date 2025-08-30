# BEE PLAY FINANCIAL REPORT - Streamlit Dashboard (Income + Expenses + Budgets)
# Single-page layout, roles, inline edit/delete, CSV autosave + Google Sheets for:
# - transactions (worksheet: "transactions")
# - expenses (worksheet: "expenses")
# - settings (worksheet: "settings")
#
# Run locally:  python -m streamlit run app.py

import io
import json
import uuid
import calendar
from datetime import date, timedelta, datetime
from typing import Tuple, Dict, Optional

import numpy as np
import pandas as pd
import streamlit as st

# PDF + Excel
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

# Google Sheets (optional)
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="BEE PLAY Financial Report", layout="wide", page_icon="🎮")

# ---------- USERS ----------
USERS = {
    "staff1": {"password": "1234", "role": "staff"},  # input-only
    "team1":  {"password": "2025", "role": "team"},   # full
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
.stTextInput>div>div>input, .stNumberInput>div>div>input, .stDateInput>div>div>input, .stSelectbox>div>div>div, .stTextArea textarea {
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

# ---------- CONSTANTS ----------
EXPENSE_CATEGORIES = [
    "COGS (Snacks Purchase)",
    "Rent",
    "Internet",
    "Electricity",
    "Salaries",
    "Maintenance/Repairs",
    "Supplies & Cleaning",
    "Marketing",
    "Bank Fees",
    "Miscellaneous",
]
PAYMENT_OPTIONS = ["Cash", "QRIS"]

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

def excel_bytes(tx_df: pd.DataFrame, exp_df: pd.DataFrame, summary: Dict[str, float]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        tx = tx_df.copy()
        if "date" in tx.columns and pd.api.types.is_datetime64_any_dtype(tx["date"]):
            tx["date"] = tx["date"].dt.strftime("%Y-%m-%d")
        tx.to_excel(writer, sheet_name="Transactions", index=False)

        ex = exp_df.copy()
        if "date" in ex.columns and pd.api.types.is_datetime64_any_dtype(ex["date"]):
            ex["date"] = ex["date"].dt.strftime("%Y-%m-%d")
        ex.to_excel(writer, sheet_name="Expenses", index=False)

        summary_df = pd.DataFrame([{"Metric": k, "Value": v if isinstance(v, (int, float)) else str(v)} for k, v in summary.items()])
        summary_df.to_excel(writer, sheet_name="Summary", index=False)

        wb = writer.book
        ws = writer.sheets["Summary"]
        currency_fmt = wb.add_format({'num_format': '#,##0', 'align': 'left'})
        ws.set_column(0, 0, 38); ws.set_column(1, 1, 22, currency_fmt)
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
            "time_play": pd.Series([], dtype="object"),
            "package": pd.Series([], dtype="object"),
            "hours": pd.Series([], dtype="float"),
            "rental_revenue": pd.Series([], dtype="float"),
            "snack_type": pd.Series([], dtype="object"),
            "snack_revenue": pd.Series([], dtype="float"),
            "payment_method": pd.Series([], dtype="object"),
            "tx_id": pd.Series([], dtype="object"),
        })
    if "expenses" not in st.session_state:
        st.session_state.expenses = pd.DataFrame({
            "date": pd.to_datetime([], errors="coerce"),
            "category": pd.Series([], dtype="object"),
            "description": pd.Series([], dtype="object"),
            "amount": pd.Series([], dtype="float"),
            "exp_id": pd.Series([], dtype="object"),
        })
    if "config" not in st.session_state:
        st.session_state.config = {
            "brand": "BEE PLAY FINANCIAL REPORT",
            "num_consoles": 7,
            "open_hours": 14,
            "initial_capital": 0.0,
            "weekly_target_revenue": 500_000.0,
            "monthly_target_revenue": 1_000_000.0,
            "fixed_costs": {"Rent": 0.0, "Salaries": 0.0, "Internet": 0.0, "Electricity": 0.0, "Misc": 0.0},
            "variable_per_hour": 0.0,
            "snack_cogs_pct": 40.0,
            "budgets": {cat: 0.0 for cat in EXPENSE_CATEGORIES},
        }

def ensure_tx_ids():
    df = st.session_state.transactions
    if df.empty: return
    df = df.copy()
    if "tx_id" not in df.columns:
        df["tx_id"] = None
    mask = df["tx_id"].isna() | (df["tx_id"] == "")
    if mask.any():
        df.loc[mask, "tx_id"] = [str(uuid.uuid4()) for _ in range(mask.sum())]
    st.session_state.transactions = df

def ensure_exp_ids():
    df = st.session_state.expenses
    if df.empty: return
    df = df.copy()
    if "exp_id" not in df.columns:
        df["exp_id"] = None
    mask = df["exp_id"].isna() | (df["exp_id"] == "")
    if mask.any():
        df.loc[mask, "exp_id"] = [str(uuid.uuid4()) for _ in range(mask.sum())]
    st.session_state.expenses = df

def ensure_budget_keys():
    cfg = st.session_state.config
    for cat in EXPENSE_CATEGORIES:
        if cat not in cfg["budgets"]:
            cfg["budgets"][cat] = 0.0

# ---------- Local persistence ----------
def load_csv(path="transactions.csv"):
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        needed = ["date","package","hours","rental_revenue","snack_type","snack_revenue"]
        for c in needed:
            if c not in df.columns: return False
        if "time_play" not in df.columns:
            df["time_play"] = ""
        if "payment_method" not in df.columns:
            df["payment_method"] = "Cash"
        if "tx_id" not in df.columns:
            df["tx_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
        st.session_state.transactions = df
        ensure_tx_ids()
        return True
    except Exception:
        return False

def save_csv(path="transactions.csv"):
    try:
        st.session_state.transactions.to_csv(path, index=False)
        return True
    except Exception:
        return False

def load_expenses_csv(path="expenses.csv"):
    try:
        df = pd.read_csv(path, parse_dates=["date"])
        needed = ["date","category","description","amount"]
        for c in needed:
            if c not in df.columns: return False
        if "exp_id" not in df.columns:
            df["exp_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
        st.session_state.expenses = df
        ensure_exp_ids()
        return True
    except Exception:
        return False

def save_expenses_csv(path="expenses.csv"):
    try:
        st.session_state.expenses.to_csv(path, index=False)
        return True
    except Exception:
        return False

def load_settings_json(path="settings.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = st.session_state.config
        for k, v in data.items():
            if k == "fixed_costs":
                cfg["fixed_costs"].update(v)
            elif k == "budgets":
                cfg["budgets"].update(v)
            else:
                cfg[k] = v
        ensure_budget_keys()
        return True
    except Exception:
        return False

def save_settings_json(path="settings.json"):
    try:
        cfg = st.session_state.config
        data = {
            "brand": cfg["brand"],
            "num_consoles": cfg["num_consoles"],
            "open_hours": cfg["open_hours"],
            "initial_capital": cfg["initial_capital"],
            "weekly_target_revenue": cfg["weekly_target_revenue"],
            "monthly_target_revenue": cfg["monthly_target_revenue"],
            "fixed_costs": cfg["fixed_costs"],
            "variable_per_hour": cfg["variable_per_hour"],
            "snack_cogs_pct": cfg["snack_cogs_pct"],
            "budgets": cfg["budgets"],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

# ---------- Sample data ----------
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
            rand_hour = int(np.random.randint(8, 22))
            rand_min = int(np.random.choice([0, 15, 30, 45]))
            time_play = f"{rand_hour:02d}:{rand_min:02d}"
            pay = np.random.choice(PAYMENT_OPTIONS)
            rows.append([d, time_play, pkg, float(hrs), float(price), snack_type, float(snack_rev), pay, str(uuid.uuid4())])
    df = pd.DataFrame(rows, columns=[
        "date","time_play","package","hours","rental_revenue","snack_type","snack_revenue","payment_method","tx_id"
    ])
    st.session_state.transactions = df
    ensure_tx_ids()

    erows = []
    for d in rng:
        if np.random.rand() < 0.5:
            cat = np.random.choice(EXPENSE_CATEGORIES)
            amt = float(np.random.choice([50000, 100000, 150000, 200000, 250000]))
            erows.append([d, cat, f"Auto sample {cat}", amt, str(uuid.uuid4())])
    edf = pd.DataFrame(erows, columns=["date","category","description","amount","exp_id"])
    st.session_state.expenses = edf
    ensure_exp_ids()

# ---------- Google Sheets ----------
def _gs_client():
    try:
        creds_info = st.secrets["google_service_account"]
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        return gspread.authorize(creds)
    except Exception:
        return None

def gs_open(section="sheets", default_ws="transactions"):
    gc = _gs_client()
    if not gc: return None, None
    ss_id = st.secrets["sheets"]["spreadsheet_id"]  # one spreadsheet for all tabs
    ws_name = st.secrets.get(section, {}).get("worksheet_name", default_ws)
    sh = gc.open_by_key(ss_id)
    ws = sh.worksheet(ws_name)
    return sh, ws

# Transactions
def gs_load_transactions() -> bool:
    try:
        _, ws = gs_open("sheets", "transactions")
        if not ws: return False
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            st.session_state.transactions = st.session_state.transactions.iloc[0:0]
            ensure_tx_ids()
            return True
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        for c in ["hours","rental_revenue","snack_revenue"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)
        for col, default, typ in [
            ("package","",str),
            ("snack_type","",str),
            ("time_play","",str),
            ("payment_method","Cash",str),
        ]:
            if col not in df.columns:
                df[col] = default
            else:
                df[col] = df[col].astype(typ)
        if "tx_id" not in df.columns:
            df["tx_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
        st.session_state.transactions = df
        ensure_tx_ids()
        return True
    except Exception:
        return False

def gs_save_transactions() -> bool:
    try:
        _, ws = gs_open("sheets", "transactions")
        if not ws: return False
        df = st.session_state.transactions.copy()
        if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        cols = ["date","time_play","package","hours","rental_revenue","snack_type","snack_revenue","payment_method","tx_id"]
        for c in cols:
            if c not in df.columns: df[c] = ""
        df = df[cols]
        ws.clear()
        ws.append_row(cols)
        if len(df) > 0:
            ws.append_rows(df.values.tolist())
        return True
    except Exception:
        return False

# Expenses
def gs_load_expenses() -> bool:
    try:
        _, ws = gs_open("sheets_expenses", "expenses")
        if not ws: return False
        rows = ws.get_all_records()
        df = pd.DataFrame(rows)
        if df.empty:
            st.session_state.expenses = st.session_state.expenses.iloc[0:0]
            ensure_exp_ids()
            return True
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
        df["category"] = df["category"].astype(str)
        df["description"] = df["description"].astype(str)
        if "exp_id" not in df.columns:
            df["exp_id"] = [str(uuid.uuid4()) for _ in range(len(df))]
        st.session_state.expenses = df
        ensure_exp_ids()
        return True
    except Exception:
        return False

def gs_save_expenses() -> bool:
    try:
        _, ws = gs_open("sheets_expenses", "expenses")
        if not ws: return False
        df = st.session_state.expenses.copy()
        if "date" in df.columns and pd.api.types.is_datetime64_any_dtype(df["date"]):
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        cols = ["date","category","description","amount","exp_id"]
        for c in cols:
            if c not in df.columns: df[c] = ""
        df = df[cols]
        ws.clear()
        ws.append_row(["date","category","description","amount","exp_id"])
        if len(df) > 0:
            ws.append_rows(df.values.tolist())
        return True
    except Exception:
        return False

# Settings (key-value rows)
def gs_load_settings() -> bool:
    try:
        _, ws = gs_open("sheets_settings", "settings")
        if not ws: return False
        rows = ws.get_all_records()
        if not rows:
            return True
        kv = {r.get("key"): r.get("value") for r in rows if "key" in r and "value" in r}
        cfg = st.session_state.config

        def to_num(v):
            try:
                if v is None or v == "": return 0.0
                if isinstance(v, (int, float)): return float(v)
                if isinstance(v, str) and v.strip().isdigit(): return float(v)
                return float(v)
            except Exception:
                return v

        for simple in ["brand","num_consoles","open_hours","initial_capital","weekly_target_revenue","monthly_target_revenue","variable_per_hour","snack_cogs_pct"]:
            if simple in kv:
                val = to_num(kv[simple])
                cfg[simple] = val if isinstance(val, float) else kv[simple]

        for fc in ["Rent","Salaries","Internet","Electricity","Misc"]:
            key = f"fixed_{fc}"
            if key in kv:
                cfg["fixed_costs"][fc] = to_num(kv[key])

        for cat in EXPENSE_CATEGORIES:
            bkey = f"budget_{cat}"
            if bkey in kv:
                cfg["budgets"][cat] = to_num(kv[bkey])

        ensure_budget_keys()
        return True
    except Exception:
        return False

def gs_save_settings() -> bool:
    try:
        _, ws = gs_open("sheets_settings", "settings")
        if not ws: return False
        cfg = st.session_state.config
        rows = []

        def add(k, v):
            rows.append({"key": k, "value": v})

        add("brand", cfg["brand"])
        add("num_consoles", cfg["num_consoles"])
        add("open_hours", cfg["open_hours"])
        add("initial_capital", cfg["initial_capital"])
        add("weekly_target_revenue", cfg["weekly_target_revenue"])
        add("monthly_target_revenue", cfg["monthly_target_revenue"])
        add("variable_per_hour", cfg["variable_per_hour"])
        add("snack_cogs_pct", cfg["snack_cogs_pct"])
        for fc, val in cfg["fixed_costs"].items():
            add(f"fixed_{fc}", val)
        for cat, val in cfg["budgets"].items():
            add(f"budget_{cat}", val)

        df = pd.DataFrame(rows)
        ws.clear()
        ws.append_row(["key","value"])
        if len(df) > 0:
            ws.append_rows(df.values.tolist())
        return True
    except Exception:
        return False

# ---------- Autosave everything ----------
def autosave_all():
    try: st.session_state.transactions.to_csv("transactions.csv", index=False)
    except Exception: pass
    try: st.session_state.expenses.to_csv("expenses.csv", index=False)
    except Exception: pass
    try: save_settings_json("settings.json")
    except Exception: pass
    try: gs_save_transactions()
    except Exception: pass
    try: gs_save_expenses()
    except Exception: pass
    try: gs_save_settings()
    except Exception: pass

# ---------- Filters & Metrics ----------
def filter_by_range(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if df.empty: return df.copy()
    mask = (df['date'].dt.date >= start) & (df['date'].dt.date <= end)
    return df.loc[mask].copy()

def kpis_for_range_income(df_income: pd.DataFrame, cfg: dict, start: date, end: date) -> dict:
    rental_rev = df_income["rental_revenue"].sum() if not df_income.empty else 0.0
    snack_rev  = df_income["snack_revenue"].sum() if not df_income.empty else 0.0
    total_rev  = rental_rev + snack_rev
    hours_total = df_income["hours"].sum() if not df_income.empty else 0.0
    tx_count = len(df_income)

    days = (end - start).days + 1
    available_hours = cfg["num_consoles"] * cfg["open_hours"] * max(days, 1)
    utilization = (hours_total / available_hours * 100.0) if available_hours > 0 else 0.0
    utilization = min(utilization, 100.0)

    variable_from_hours = cfg["variable_per_hour"] * hours_total
    variable_from_snacks = (cfg["snack_cogs_pct"]/100.0) * snack_rev
    variable_total_planning = variable_from_hours + variable_from_snacks
    cm = total_rev - variable_total_planning
    cmr = (cm / total_rev) if total_rev > 0 else 0.0

    monthly_fixed_total = sum(cfg["fixed_costs"].values())
    bep_revenue_monthly = (monthly_fixed_total / cmr) if cmr > 0 else None

    return {
        "rental_revenue": rental_rev,
        "snack_revenue": snack_rev,
        "total_revenue": total_rev,
        "hours_total": hours_total,
        "tx_count": tx_count,
        "utilization_pct": utilization,
        "cmr": cmr,
        "bep_revenue_monthly": bep_revenue_monthly,
    }

def expenses_sum_in_range(df_exp: pd.DataFrame) -> float:
    return float(df_exp["amount"].sum()) if not df_exp.empty else 0.0

def monthly_expense_by_category(expenses_all: pd.DataFrame, month_start: date, month_end: date) -> pd.DataFrame:
    df = filter_by_range(expenses_all, month_start, month_end)
    if df.empty:
        return pd.DataFrame(columns=["category","amount"])
    out = df.groupby("category", as_index=False)["amount"].sum().sort_values("amount", ascending=False)
    return out

def cumulative_net_profit_including_expenses(cfg: dict) -> Tuple[float, float]:
    df_i = st.session_state.transactions
    df_e = st.session_state.expenses
    revenue = (df_i["rental_revenue"].sum() + df_i["snack_revenue"].sum()) if not df_i.empty else 0.0
    outcome = df_e["amount"].sum() if not df_e.empty else 0.0
    net = revenue - outcome
    initial_capital = cfg.get("initial_capital", 0.0)
    roi_pct = (net / initial_capital * 100.0) if initial_capital > 0 else 0.0
    return float(net), float(roi_pct)

# ---------- AUTH ----------
def login_ui():
    st.markdown("## 🔐 Login")
    u = st.text_input("Username", key="login_user")
    p = st.text_input("Password", type="password", key="login_pass")
    if st.button("Sign in", key="btn_login"):
        user = USERS.get(u)
        if user and user["password"] == p:
            st.session_state.auth = {"username": u, "role": user["role"]}
            st.success(f"Welcome, {u} ({user['role']})")
            st.rerun()
        else:
            st.error("Invalid username or password")

def require_auth() -> Optional[str]:
    return st.session_state.auth["role"]

# ---------- UI BLOCKS ----------
def sidebar_settings(cfg):
    with st.sidebar:
        st.markdown("## ⚙️ Settings")
        st.markdown(f"**Brand:** {cfg['brand']}")
        st.divider()

        st.markdown("### 🏪 Operations")
        cfg["num_consoles"] = st.number_input("Number of consoles", 1, 100, int(cfg["num_consoles"]), key="set_num_consoles")
        cfg["open_hours"]   = st.number_input("Operating hours per day", 1, 24, int(cfg["open_hours"]), key="set_open_hours")

        st.markdown("### 💰 Targets")
        cfg["weekly_target_revenue"]  = st.number_input("Weekly target revenue (IDR)", 0, 100_000_000, int(cfg["weekly_target_revenue"]), key="set_week_target")
        cfg["monthly_target_revenue"] = st.number_input("Monthly target revenue (IDR)", 0, 500_000_000, int(cfg["monthly_target_revenue"]), key="set_month_target")

        st.markdown("### 🧾 Fixed Monthly Costs (IDR)")
        new_fixed = {}
        for k in ["Rent","Salaries","Internet","Electricity","Misc"]:
            new_fixed[k] = float(st.number_input(k, 0, 1_000_000_000, int(cfg["fixed_costs"].get(k,0)), key=f"fc_{k}"))
        cfg["fixed_costs"] = new_fixed

        st.markdown("### 🧪 Variable Costs (planning)")
        cfg["variable_per_hour"] = float(st.number_input("Variable cost per rental hour (IDR)", 0, 100_000, int(cfg["variable_per_hour"]), key="var_per_hour"))
        cfg["snack_cogs_pct"]    = float(st.slider("Snack COGS (% of snack revenue)", 0.0, 100.0, float(cfg["snack_cogs_pct"]), 1.0, key="snack_cogs_pct"))

        st.markdown("### 🏗️ Capital")
        cfg["initial_capital"] = float(st.number_input("Initial capital (IDR)", 0, 5_000_000_000, int(cfg["initial_capital"]), key="init_capital"))

        st.markdown("### 🧩 Budgets (monthly per category)")
        for cat in EXPENSE_CATEGORIES:
            cfg["budgets"][cat] = float(st.number_input(f"Budget • {cat}", 0, 5_000_000_000, int(cfg["budgets"].get(cat, 0)), key=f"bud_{cat}"))

        st.divider()
        st.markdown("### 💾 Data")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            if st.button("📥 Load Data (all)", key="btn_load_all"):
                ok_tx = gs_load_transactions() or load_csv()
                ok_ex = gs_load_expenses() or load_expenses_csv()
                ok_st = gs_load_settings() or load_settings_json()
                if ok_tx or ok_ex or ok_st:
                    st.success("Loaded data")
                else:
                    st.error("Failed to load")
        with c2:
            if st.button("📤 Save Data (all)", key="btn_save_all"):
                ok_tx = gs_save_transactions() or save_csv()
                ok_ex = gs_save_expenses() or save_expenses_csv()
                ok_st = gs_save_settings() or save_settings_json()
                if ok_tx or ok_ex or ok_st:
                    st.success("Saved data")
                else:
                    st.error("Failed to save")
        with c3:
            if st.button("🧪 Load Sample Data", key="btn_sample"):
                add_sample_data()
                autosave_all()
                st.success("Sample data loaded")
        with c4:
            if st.button("🔽 Save Settings (local JSON)", key="btn_save_local_settings"):
                if save_settings_json():
                    st.success("Settings saved locally")
                else:
                    st.error("Failed to save settings")

        st.markdown("---")
        if st.button("🚪 Logout", key="btn_logout_sidebar"):
            st.session_state.auth = {"username": None, "role": None}
            st.success("Logged out.")
            st.rerun()

def upload_transactions_widget():
    st.markdown("### ⬆️ Upload Transactions (Excel/CSV)")
    f = st.file_uploader(
        "Choose .xlsx or .csv (date, time_play [HH:MM optional], package, hours, rental_revenue, snack_type, snack_revenue, payment_method [Cash/QRIS optional], [tx_id])",
        type=["xlsx","csv"], key="upl_tx")
    if f is not None:
        try:
            if f.name.lower().endswith(".xlsx"):
                df_new = pd.read_excel(f)
            else:
                df_new = pd.read_csv(f)
            df_new.columns = [c.lower().strip().replace(" ", "_") for c in df_new.columns]
            needed = ["date","package","hours","rental_revenue","snack_type","snack_revenue"]
            missing = [c for c in needed if c not in df_new.columns]
            if missing:
                st.error(f"Missing columns in file: {missing}")
                return
            df_new["date"] = pd.to_datetime(df_new["date"], errors="coerce", infer_datetime_format=True)
            if df_new["date"].isna().mean() > 0.5:
                df_new["date"] = pd.to_datetime(df_new["date"], errors="coerce", dayfirst=True, infer_datetime_format=True)
            for col in ["hours","rental_revenue","snack_revenue"]:
                df_new[col] = pd.to_numeric(df_new[col], errors="coerce").fillna(0.0)
            if "time_play" not in df_new.columns:
                df_new["time_play"] = ""
            else:
                df_new["time_play"] = df_new["time_play"].astype(str)
            if "payment_method" not in df_new.columns:
                df_new["payment_method"] = "Cash"
            else:
                df_new["payment_method"] = df_new["payment_method"].astype(str).str.strip().str.title()
                df_new.loc[~df_new["payment_method"].isin(PAYMENT_OPTIONS), "payment_method"] = "Cash"
            if "tx_id" not in df_new.columns:
                df_new["tx_id"] = [str(uuid.uuid4()) for _ in range(len(df_new))]
            cols = ["date","time_play","package","hours","rental_revenue","snack_type","snack_revenue","payment_method","tx_id"]
            st.session_state.transactions = pd.concat([st.session_state.transactions, df_new[cols]], ignore_index=True)
            ensure_tx_ids()
            autosave_all()
            st.success(f"Imported {len(df_new)} rows.")
            st.rerun()  # refresh immediately
        except Exception as e:
            st.error(f"Failed to read file: {e}")

def upload_expenses_widget():
    st.markdown("### ⬆️ Upload Expenses (Excel/CSV)")
    f = st.file_uploader("Choose .xlsx or .csv (date, category, description, amount, [exp_id])",
                         type=["xlsx","csv"], key="upl_exp")
    if f is not None:
        try:
            if f.name.lower().endswith(".xlsx"):
                df_new = pd.read_excel(f)
            else:
                df_new = pd.read_csv(f)
            df_new.columns = [c.lower().strip().replace(" ", "_") for c in df_new.columns]
            needed = ["date","category","description","amount"]
            missing = [c for c in needed if c not in df_new.columns]
            if missing:
                st.error(f"Missing columns in file: {missing}")
                return
            df_new["date"] = pd.to_datetime(df_new["date"], errors="coerce", infer_datetime_format=True)
            if df_new["date"].isna().mean() > 0.5:
                df_new["date"] = pd.to_datetime(df_new["date"], errors="coerce", dayfirst=True, infer_datetime_format=True)
            df_new["amount"] = pd.to_numeric(df_new["amount"], errors="coerce").fillna(0.0)
            df_new["category"] = df_new["category"].astype(str)
            df_new["description"] = df_new["description"].astype(str)
            if "exp_id" not in df_new.columns:
                df_new["exp_id"] = [str(uuid.uuid4()) for _ in range(len(df_new))]
            cols = ["date","category","description","amount","exp_id"]
            st.session_state.expenses = pd.concat([st.session_state.expenses, df_new[cols]], ignore_index=True)
            ensure_exp_ids()
            autosave_all()
            st.success(f"Imported {len(df_new)} expense rows.")
        except Exception as e:
            st.error(f"Failed to read expenses file: {e}")

def add_transaction_form():
    with st.expander("Add Transaction (manual entry)", expanded=True):
        col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([1.15,1.15,1.0,1.25,1.25,1.0,1.0,1.0])
        t_date = col1.date_input("Date", value=date.today(), key="tx_date")
        default_time = (datetime.now()).time().replace(second=0, microsecond=0)
        t_time = col2.time_input("Time Play (24h)", value=default_time, key="tx_time")
        t_package = col3.text_input("Package", key="tx_pkg")
        t_hours = float(col4.number_input("Hours", min_value=0.0, max_value=24.0, value=1.0, step=0.5, key="tx_hours"))
        t_rental = float(col5.number_input("Rental revenue (IDR)", min_value=0, value=0, step=1000, key="tx_rent"))
        t_snack_type = col6.text_input("Snack type", key="tx_snack_type")
        t_snack = float(col7.number_input("Snack revenue (IDR)", min_value=0, value=0, step=1000, key="tx_snack"))
        t_payment = col8.selectbox("Payment", PAYMENT_OPTIONS, index=0, key="tx_payment")
        b1, b2, _ = st.columns([1,1,6])
        if b1.button("➕ Add Transaction", key="btn_add_tx"):
            new_row = pd.DataFrame({
                "date":[pd.to_datetime(t_date)],
                "time_play":[t_time.strftime("%H:%M")],
                "package":[t_package],
                "hours":[t_hours],
                "rental_revenue":[t_rental],
                "snack_type":[t_snack_type],
                "snack_revenue":[t_snack],
                "payment_method":[t_payment],
                "tx_id":[str(uuid.uuid4())],
            })
            st.session_state.transactions = pd.concat([st.session_state.transactions, new_row], ignore_index=True)
            ensure_tx_ids()
            autosave_all()
            st.success("Transaction added & saved.")
            st.rerun()  # show in staff "today" immediately
        if b2.button("🧹 Clear Form", key="btn_clear_tx"):
            st.rerun()

def add_expense_form(role_is_team: bool):
    disabled = not role_is_team
    with st.expander("Add Expense (manual entry)", expanded=False):
        r1c1, r1c2, r1c3 = st.columns([1.2,1.6,1.2])
        e_date = r1c1.date_input("Date", value=date.today(), disabled=disabled, key="exp_date")
        e_category = r1c2.selectbox("Category", EXPENSE_CATEGORIES, disabled=disabled, key="exp_cat")
        e_amount = float(r1c3.number_input("Amount (IDR)", min_value=0, value=0, step=1000, disabled=disabled, key="exp_amt"))
        e_desc = st.text_area("Description (optional)", disabled=disabled, key="exp_desc")
        add_col, clr_col = st.columns([1,1])
        if add_col.button("➕ Add Expense", disabled=disabled, key="btn_add_exp"):
            new_e = pd.DataFrame({
                "date":[pd.to_datetime(e_date)],
                "category":[e_category],
                "description":[e_desc],
                "amount":[e_amount],
                "exp_id":[str(uuid.uuid4())],
            })
            st.session_state.expenses = pd.concat([st.session_state.expenses, new_e], ignore_index=True)
            ensure_exp_ids()
            autosave_all()
            st.success("Expense added.")
        if clr_col.button("🧹 Clear", disabled=disabled, key="btn_clear_exp"):
            st.rerun()

# ---------- MAIN ----------
init_state()
ensure_tx_ids()
ensure_exp_ids()
ensure_budget_keys()
cfg = st.session_state.config

# AUTH
role = require_auth()
if not role:
    login_ui()
    st.stop()

# HEADER
st.markdown(f"# 🎮 {cfg['brand']}")

# ---------- STAFF VIEW (input-only) ----------
if role == "staff":
    with st.sidebar:
        st.markdown("---")
        if st.button("🚪 Logout", key="btn_logout_staff"):
            st.session_state.auth = {"username": None, "role": None}
            st.success("Logged out.")
            st.rerun()

    st.info("**Staff Mode** — input and delete **today's** transactions. Expenses are hidden for staff.")
    add_transaction_form()
    upload_transactions_widget()

    st.subheader("🧾 Today's Transactions")

    # ===== Robust TODAY filter (handles ISO, DD/MM/YYYY, strings, datetimes) =====
    df_today = st.session_state.transactions.copy()
    if not df_today.empty:
        d0 = pd.to_datetime(df_today["date"], errors="coerce", infer_datetime_format=True)
        if d0.isna().mean() > 0.5:
            d0 = pd.to_datetime(df_today["date"], errors="coerce", dayfirst=True, infer_datetime_format=True)
        df_today["__date_dt"] = d0
        df_today = df_today[~df_today["__date_dt"].isna()].copy()
        df_today["__date_only"] = df_today["__date_dt"].dt.date
        df_today = df_today[df_today["__date_only"] == date.today()].copy()
        df_today["date"] = df_today["__date_dt"]

    # ---- Debug block (remove later if you want) ----
    st.caption("Debug: last 5 raw rows in session_state.transactions")
    if not st.session_state.transactions.empty:
        st.write(st.session_state.transactions.tail()[["date","time_play","package","payment_method"]])

    if df_today.empty:
        st.info("No transactions for today yet.")
    else:
        # Make it look like team table, but read-only fields + a deletable checkbox column
        view_today = df_today.sort_values("__date_dt", ascending=False).copy()
        view_today["date"] = pd.to_datetime(view_today["date"], errors="coerce").dt.date
        for c in ["hours","rental_revenue","snack_revenue"]:
            view_today[c] = pd.to_numeric(view_today[c], errors="coerce").fillna(0)
        view_today["__delete__"] = False

        cols_today = ["date","time_play","package","hours","rental_revenue","snack_type","snack_revenue","payment_method","tx_id","__delete__"]
        view_today = view_today[cols_today]

        edited_today = st.data_editor(
            view_today,
            use_container_width=True,
            num_rows="fixed",
            column_config={
                "date": st.column_config.DateColumn(format="YYYY-MM-DD", disabled=True),
                "time_play": st.column_config.TextColumn(disabled=True),
                "package": st.column_config.TextColumn(disabled=True),
                "hours": st.column_config.NumberColumn(step=0.5, min_value=0.0, disabled=True),
                "rental_revenue": st.column_config.NumberColumn(step=1000, min_value=0, disabled=True),
                "snack_type": st.column_config.TextColumn(disabled=True),
                "snack_revenue": st.column_config.NumberColumn(step=1000, min_value=0, disabled=True),
                "payment_method": st.column_config.TextColumn(disabled=True),
                "tx_id": st.column_config.TextColumn(disabled=True, help="Row ID (read-only)"),
                "__delete__": st.column_config.CheckboxColumn(label="✅ Delete?", help="Tick to delete this transaction", default=False),
            },
            key="staff_today_editor",
        )

        del_col1, del_col2 = st.columns([3,1])
        with del_col1:
            st.caption("Tick the rows to delete, then press the button.")
        with del_col2:
            do_delete = st.button("🗑️ Delete selected", use_container_width=True, key="btn_staff_delete")

        if do_delete:
            to_delete_ids = edited_today.loc[edited_today["__delete__"] == True, "tx_id"].astype(str).tolist()
            if not to_delete_ids:
                st.warning("No rows ticked for deletion.")
            else:
                master = st.session_state.transactions.copy()
                master["tx_id"] = master.get("tx_id", pd.Series([None]*len(master))).astype(str)
                before = len(master)
                today_ids = set(df_today["tx_id"].astype(str).tolist())
                to_delete_ids = [x for x in to_delete_ids if x in today_ids]  # safety: only today's rows
                master = master[~master["tx_id"].isin(to_delete_ids)].reset_index(drop=True)
                st.session_state.transactions = master
                ensure_tx_ids()
                autosave_all()
                after = len(master)
                st.success(f"Deleted {before - after} transaction(s).")
                st.rerun()

    if st.button("📥 Load Data", key="btn_staff_load"):
        ok = gs_load_transactions() or load_csv()
        if ok: st.success("Loaded")
        else: st.error("Failed to load")

    st.stop()

# ---------- TEAM VIEW (full dashboard) ----------
sidebar_settings(cfg)

# Period filters
colf = st.columns([2,2,2,4])
with colf[0]:
    period = st.selectbox("Period", ["Today","This Week","This Month","This Quarter","This Year","Custom Range"], index=2, key="period_sel")
with colf[1]:
    ref_date = st.date_input("Reference date", date.today(), key="ref_date")
with colf[2]:
    if period == "Custom Range":
        custom = st.date_input("Custom range", [date.today() - timedelta(days=7), date.today()], key="custom_range")
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

# Data slices
df_all = st.session_state.transactions
df_range = filter_by_range(df_all, start, end)

exp_all = st.session_state.expenses
exp_range = filter_by_range(exp_all, start, end)

# KPIs
k = kpis_for_range_income(df_range, cfg, start, end)
exp_sum = expenses_sum_in_range(exp_range)
net_cashflow = k["total_revenue"] - exp_sum
cumul_net, roi_pct = cumulative_net_profit_including_expenses(cfg)

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total Revenue", format_idr(k["total_revenue"]))
c2.metric("Snack Sales", format_idr(k["snack_revenue"]))
c3.metric("Transactions", k["tx_count"])
c4.metric("Utilization", f"{k['utilization_pct']:.1f}%")
c5.metric("BEP (Monthly Revenue, plan)", format_idr(k["bep_revenue_monthly"]) if k["bep_revenue_monthly"] else "N/A")

c6, c7, c8 = st.columns(3)
c6.metric("Expenses (Outcome)", format_idr(exp_sum))
c7.metric("Net Cashflow (Income − Outcome)", format_idr(net_cashflow))
c8.metric("ROI % (all-time, incl. expenses)", f"{roi_pct:.2f}%")

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

with st.expander("📊 Income vs Outcome (this period)", expanded=True):
    cmp_df = pd.DataFrame({
        "label": ["Income (Revenue)", "Outcome (Expenses)"],
        "value": [k["total_revenue"], exp_sum]
    }).set_index("label")
    st.bar_chart(cmp_df)

with st.expander("🧮 Expenses by Category (this period)", expanded=False):
    by_cat = exp_range.groupby("category", as_index=False)["amount"].sum().sort_values("amount", ascending=False) if not exp_range.empty else pd.DataFrame(columns=["category","amount"])
    st.dataframe(by_cat, use_container_width=True)

mo_start, mo_end = start_end_of_month(ref_date)
cat_month = monthly_expense_by_category(exp_all, mo_start, mo_end)
if not cat_month.empty:
    st.subheader(f"📅 Budget Status • {mo_start} → {mo_end}")
    budget_rows = []
    for _, r in cat_month.iterrows():
        cat = r["category"]; spent = float(r["amount"]); budget = float(cfg["budgets"].get(cat, 0.0))
        status = "Under"
        if budget > 0:
            ratio = spent / budget
            if ratio >= 1.0: status = "Over"
            elif ratio >= 0.9: status = "Near"
        budget_rows.append({"Category": cat, "Spent": spent, "Budget": budget, "Status": status})
    for cat, budget in cfg["budgets"].items():
        if cat not in cat_month["category"].tolist() and budget > 0:
            budget_rows.append({"Category": cat, "Spent": 0.0, "Budget": budget, "Status": "Under"})
    bdf = pd.DataFrame(budget_rows).sort_values(["Status","Spent"], ascending=[True, False])
    bdf["Spent"] = bdf["Spent"].map(format_idr)
    bdf["Budget"] = bdf["Budget"].map(format_idr)
    st.dataframe(bdf, use_container_width=True)

st.divider()

# ================== Transactions (in range) — EDIT & DELETE (TEAM) ==================
st.subheader("🧾 Transactions (in range) — edit & delete (Team)")

if df_range.empty:
    st.info("No transactions in the selected range.")
else:
    view_cols = ["date","time_play","package","hours","rental_revenue","snack_type","snack_revenue","payment_method","tx_id"]
    view = df_range[view_cols].copy()
    view["date"] = pd.to_datetime(view["date"], errors="coerce").dt.date
    for c in ["hours","rental_revenue","snack_revenue"]:
        view[c] = pd.to_numeric(view[c], errors="coerce")
    editable = (role == "team")
    edited_view = st.data_editor(
        view.sort_values("date", ascending=False),
        use_container_width=True,
        num_rows="fixed",
        disabled=not editable,
        column_config={
            "date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "hours": st.column_config.NumberColumn(step=0.5, min_value=0.0),
            "rental_revenue": st.column_config.NumberColumn(step=1000, min_value=0),
            "snack_revenue": st.column_config.NumberColumn(step=1000, min_value=0),
            "time_play": st.column_config.TextColumn(help="24h HH:MM"),
            "payment_method": st.column_config.TextColumn(help="Cash or QRIS"),
            "tx_id": st.column_config.TextColumn(disabled=True, help="Row ID (read-only)"),
        },
        key="tx_editor_inline",
    )
    edited_view["tx_id"] = edited_view["tx_id"].astype(str)

    label_df = edited_view.copy()
    label_df["date"] = pd.to_datetime(label_df["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    label_df["label"] = (
        label_df["date"] + " " + label_df["time_play"].astype(str) +
        " | " + label_df["package"].astype(str) +
        " | " + label_df["payment_method"].astype(str) +
        " | Rp" + label_df["rental_revenue"].fillna(0).astype(int).astype(str) +
        " | ID:" + label_df["tx_id"].str[-6:]
    )
    del_choices = {row["label"]: row["tx_id"] for _, row in label_df.iterrows()}

    dcol1, dcol2 = st.columns([3, 1])
    with dcol1:
        to_delete = st.multiselect("Select transaction(s) to delete", options=list(del_choices.keys()), disabled=not editable, key="tx_del_multisel")
    with dcol2:
        st.write("")
        delete_click = st.button("🗑️ Delete selected", use_container_width=True, disabled=not editable, key="btn_tx_delete")

    if editable and st.button("💾 Save changes", key="btn_tx_save"):
        master = st.session_state.transactions.copy()
        edited = edited_view.copy()
        edited["tx_id"] = edited["tx_id"].astype(str)
        master["tx_id"] = master.get("tx_id", pd.Series([None]*len(master))).astype(str)
        edited["date"] = pd.to_datetime(edited["date"], errors="coerce")
        for c in ["hours","rental_revenue","snack_revenue"]:
            edited[c] = pd.to_numeric(edited[c], errors="coerce")
        edited["payment_method"] = edited["payment_method"].astype(str).str.strip().str.title()
        edited.loc[~edited["payment_method"].isin(PAYMENT_OPTIONS), "payment_method"] = "Cash"
        ed_lookup = edited.set_index("tx_id")
        mask = master["tx_id"].isin(ed_lookup.index)
        for col in ["date","time_play","package","hours","rental_revenue","snack_type","snack_revenue","payment_method"]:
            master.loc[mask, col] = master.loc[mask, "tx_id"].map(ed_lookup[col])
        st.session_state.transactions = master.reset_index(drop=True)
        ensure_tx_ids()
        autosave_all()
        st.success("Changes saved.")
        st.rerun()

    if editable and delete_click:
        if not to_delete:
            st.warning("No rows selected.")
        else:
            del_ids = {str(del_choices[label]) for label in to_delete if label in del_choices}
            master = st.session_state.transactions.copy()
            master["tx_id"] = master.get("tx_id", pd.Series([None]*len(master))).astype(str)
            before = len(master)
            master = master[~master["tx_id"].isin(del_ids)].reset_index(drop=True)
            st.session_state.transactions = master
            ensure_tx_ids()
            autosave_all()
            after = len(master)
            st.success(f"Deleted {before - after} transaction(s).")
            st.rerun()

# Income input/upload (team area too)
st.markdown("### ➕ Add / Upload (Income)")
add_transaction_form()
upload_transactions_widget()

# ================== Expenses (Outcome) ==================
st.subheader("💸 Expenses (Outcome) — add, upload, edit & delete (Team)")

add_expense_form(role_is_team=(role == "team"))
if role == "team":
    upload_expenses_widget()
else:
    st.info("Staff cannot upload expenses.")

if exp_range.empty:
    st.info("No expenses in the selected range.")
else:
    e_view = exp_range[["date","category","description","amount","exp_id"]].copy()
    e_view["date"] = pd.to_datetime(e_view["date"], errors="coerce").dt.date
    e_view["amount"] = pd.to_numeric(e_view["amount"], errors="coerce").fillna(0.0)
    e_edit = st.data_editor(
        e_view.sort_values("date", ascending=False),
        use_container_width=True,
        num_rows="fixed",
        disabled=(role != "team"),
        column_config={
            "date": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "amount": st.column_config.NumberColumn(step=1000, min_value=0),
            "exp_id": st.column_config.TextColumn(disabled=True, help="Row ID (read-only)"),
        },
        key="exp_editor_inline",
    )
    e_edit["exp_id"] = e_edit["exp_id"].astype(str)
    elabel = e_edit.copy()
    elabel["date"] = pd.to_datetime(elabel["date"], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
    elabel["label"] = elabel["date"] + " | " + elabel["category"].astype(str) + " | Rp" + elabel["amount"].fillna(0).astype(int).astype(str) + " | ID:" + elabel["exp_id"].str[-6:]
    exp_del_choices = {row["label"]: row["exp_id"] for _, row in elabel.iterrows()}

    e1, e2 = st.columns([3,1])
    with e1:
        exp_to_del = st.multiselect("Select expense(s) to delete", options=list(exp_del_choices.keys()), disabled=(role!="team"), key="exp_del_multisel")
    with e2:
        st.write("")
        exp_del_click = st.button("🗑️ Delete selected (expenses)", use_container_width=True, disabled=(role!="team"), key="btn_exp_delete")

    if role == "team" and st.button("💾 Save expense changes", key="btn_exp_save"):
        master = st.session_state.expenses.copy()
        edited = e_edit.copy()
        edited["exp_id"] = edited["exp_id"].astype(str)
        master["exp_id"] = master.get("exp_id", pd.Series([None]*len(master))).astype(str)
        edited["date"] = pd.to_datetime(edited["date"], errors="coerce")
        edited["amount"] = pd.to_numeric(edited["amount"], errors="coerce").fillna(0.0)
        ed_lookup = edited.set_index("exp_id")
        mask = master["exp_id"].isin(ed_lookup.index)
        for col in ["date","category","description","amount"]:
            master.loc[mask, col] = master.loc[mask, "exp_id"].map(ed_lookup[col])
        st.session_state.expenses = master.reset_index(drop=True)
        ensure_exp_ids()
        autosave_all()
        st.success("Expense changes saved.")
        st.rerun()

    if role == "team" and exp_del_click:
        if not exp_to_del:
            st.warning("No rows selected.")
        else:
            del_ids = {str(exp_del_choices[label]) for label in exp_to_del if label in exp_del_choices}
            master = st.session_state.expenses.copy()
            master["exp_id"] = master.get("exp_id", pd.Series([None]*len(master))).astype(str)
            before = len(master)
            master = master[~master["exp_id"].isin(del_ids)].reset_index(drop=True)
            st.session_state.expenses = master
            ensure_exp_ids()
            autosave_all()
            after = len(master)
            st.success(f"Deleted {before - after} expense(s).")
            st.rerun()

# ---------- Export ----------
st.subheader("📦 Reports & Export")
summary_export = {
    "Period": f"{start} to {end}",
    "Transactions": int(k["tx_count"]),
    "Hours Total": float(df_range["hours"].sum()) if not df_range.empty else 0.0,
    "Rental Revenue": int(k["rental_revenue"]),
    "Snack Revenue": int(k["snack_revenue"]),
    "Total Revenue": int(k["total_revenue"]),
    "Expenses (Outcome)": int(exp_sum),
    "Net Cashflow": int(net_cashflow),
    "ROI % (all-time)": round(float(roi_pct), 2),
    "CM Ratio (%) (plan)": round(float(k["cmr"])*100.0, 2),
    "BEP Revenue (Monthly, plan)": int(k["bep_revenue_monthly"]) if k["bep_revenue_monthly"] is not None else "N/A",
}
if not exp_range.empty:
    top5 = exp_range.groupby("category", as_index=False)["amount"].sum().sort_values("amount", ascending=False).head(5)
    for i, row in top5.iterrows():
        summary_export[f"Top Expense {i+1}"] = f"{row['category']} — {format_idr(row['amount'])}"

def excel_bytes_wrapper():
    return excel_bytes(df_range, exp_range, summary_export)

def pdf_bytes_wrapper():
    lines = {k: (format_idr(v) if isinstance(v, (int,float)) and ("%" not in k) else str(v)) for k, v in summary_export.items()}
    return pdf_bytes("BEE PLAY - Financial Summary", lines)

colx, coly = st.columns(2)
with colx:
    x_bytes = excel_bytes_wrapper()
    st.download_button("⬇️ Download Excel (Transactions + Expenses + Summary)", x_bytes,
        file_name=f"BeePlay_Report_{start}_to_{end}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="dl_xlsx")
with coly:
    pdf_bytes_data = pdf_bytes_wrapper()
    st.download_button("⬇️ Download PDF (Summary)", pdf_bytes_data,
        file_name=f"BeePlay_Summary_{start}_to_{end}.pdf",
        mime="application/pdf", key="dl_pdf")

st.markdown("---")
st.markdown("**Notes**: Income = rental + snack sales. Expenses are recorded in the Expenses section. BEP & CM use planning inputs (fixed costs & variable settings). ROI includes actual expenses.")
