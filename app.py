import os
import re
import json
import shutil
from datetime import datetime, date

import pandas as pd
import streamlit as st

# ---------------------------
# Basic Config
# ---------------------------
st.set_page_config(page_title="Mantenimiento — Fichas", layout="wide")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Original Excel path in the repo (read-only on Streamlit Cloud)
EXCEL_PATH = os.path.join(APP_DIR, "Mantenimiento TA(5).xlsx")
# Writable runtime copy (we read/write this one)
RUNTIME_XLSX = os.path.join(DATA_DIR, "Mantenimiento TA(5).xlsx")

# ---------------------------
# Small helpers
# ---------------------------
def cache_decorator():
    if hasattr(st, "cache_data"):
        return st.cache_data(show_spinner=False)
    return st.cache(show_spinner=False)

def safe_key(name: str) -> str:
    s = str(name)
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in s)
    return s[:64] or "blank"

def ensure_runtime_excel() -> str:
    """Make sure we have a writable Excel copy under /data."""
    if not os.path.exists(RUNTIME_XLSX):
        if os.path.exists(EXCEL_PATH):
            try:
                shutil.copy2(EXCEL_PATH, RUNTIME_XLSX)
            except Exception as e:
                st.error(f"No se pudo copiar el Excel inicial: {e}")
                st.stop()
        else:
            st.error("No se encontró el Excel inicial 'Mantenimiento TA(5).xlsx' en el repositorio.")
            st.stop()
    return RUNTIME_XLSX

# ---------------------------
# Data I/O
# ---------------------------
@cache_decorator()
def load_data(_: str = "") -> pd.DataFrame:
    xlsx = ensure_runtime_excel()
    df = pd.read_excel(xlsx, sheet_name=0)
    # Normalize and basic columns expected by your app
    df.columns = [str(c).strip() for c in df.columns]
    required = ["Ficha", "Modelo", "Location", "Fecha Ultiimo Mantenimiento"]
    for col in required:
        if col not in df.columns:
            st.error(f"Falta la columna '{col}'. Columnas encontradas: {list(df.columns)}")
            st.stop()
    df["Ficha"] = df["Ficha"].astype(str).str.strip()
    df = df.dropna(subset=["Ficha"]).copy()
    # Parse date (día/mes/año)
    df["Fecha_parsed"] = pd.to_datetime(df["Fecha Ultiimo Mantenimiento"], dayfirst=True, errors="coerce")
    # Next maintenance projection
    df["Proximo_Mantenimiento"] = df["Fecha_parsed"] + pd.DateOffset(months=1, days=15)
    return df

def backup_excel(xlsx_path: str) -> None:
    try:
        backups = os.path.join(APP_DIR, "backups")
        os.makedirs(backups, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.basename(xlsx_path)
        name, ext = os.path.splitext(base)
        dst = os.path.join(backups, f"{name}_{ts}{ext}")
        shutil.copy2(xlsx_path, dst)
    except Exception:
        pass  # non-fatal

def update_excel_date(ficha: str, new_date: date) -> bool:
    """Update the last-maintenance date for a ficha in the writable runtime Excel."""
    xlsx = ensure_runtime_excel()
    try:
        df = pd.read_excel(xlsx, sheet_name=0)
    except Exception as e:
        st.error(f"No se pudo abrir el Excel: {e}")
        return False

    df.columns = [str(c).strip() for c in df.columns]
    if "Ficha" not in df.columns or "Fecha Ultiimo Mantenimiento" not in df.columns:
        st.error("El Excel no tiene 'Ficha' o 'Fecha Ultiimo Mantenimiento'.")
        return False

    df["Ficha"] = df["Ficha"].astype(str).str.strip()
    mask = df["Ficha"] == str(ficha).strip()
    if not mask.any():
        st.warning("Ficha no encontrada en el Excel.")
        return False

    df.loc[mask, "Fecha Ultiimo Mantenimiento"] = new_date.strftime("%d/%m/%Y")

    # Backup + write
    backup_excel(xlsx)
    try:
        with pd.ExcelWriter(xlsx, engine="openpyxl", mode="w") as writer:
            df.to_excel(writer, index=False)
        return True
    except Exception as e:
        st.error(f"Error al guardar el Excel: {e}")
        return False

# ---------------------------
# Status / Styling
# ---------------------------
def compute_status(df: pd.DataFrame, threshold_days: int) -> pd.DataFrame:
    today = date.today()
    out = df.copy()
    diffs = []
    for val in out["Fecha_parsed"]:
        try:
            if pd.isna(val):
                diffs.append(None)
            else:
                diffs.append((today - val.date()).days)
        except Exception:
            diffs.append(None)
    out["Días desde último mant."] = pd.to_numeric(diffs, errors="coerce")

    def to_estado(v):
        try:
            if pd.isna(v):
                return "Rojo"
            return "Verde" if float(v) < float(threshold_days) else "Rojo"
        except Exception:
            return "Rojo"

    out["Estado"] = out["Días desde último mant."].apply(to_estado)
    return out

def style_status(df: pd.DataFrame):
    try:
        styler = df.style.applymap(
            lambda v: ("background-color: #2e7d32; color: white" if v == "Verde"
                       else ("background-color: #c62828; color: white" if v == "Rojo" else "")),
            subset=["Estado"]
        )
        # Color "Proximo_Mantenimiento" by proximity
        try:
            today_ts = pd.Timestamp(date.today())
            soon = today_ts + pd.Timedelta(days=15)
            def tint(v):
                try:
                    d = pd.to_datetime(v)
                    if d < today_ts: return "background-color:#ffcccc"
                    if d <= soon: return "background-color:#fff4cc"
                    return "background-color:#ccffcc"
                except Exception:
                    return ""
            if "Proximo_Mantenimiento" in df.columns:
                styler = styler.applymap(tint, subset=["Proximo_Mantenimiento"])
        except Exception:
            pass
        return styler
    except Exception:
        return df

# ---------------------------
# Navigation
# ---------------------------
if "selected_ficha" not in st.session_state:
    st.session_state.selected_ficha = None

def go_list():
    st.session_state.selected_ficha = None
    st.rerun()

def go_detail(ficha: str):
    st.session_state.selected_ficha = ficha
    st.rerun()

# ---------------------------
# Views
# ---------------------------
def list_view():
    st.subheader("Listado de fichas")
    thr = st.number_input("Umbral de días para estar 'al día'", min_value=1, max_value=365, value=90, step=1)

    df = compute_status(load_data(), thr)
    table = df[[
        "Ficha","Modelo","Location","Fecha Ultiimo Mantenimiento","Proximo_Mantenimiento","Días desde último mant.","Estado"
    ]].copy()

    try:
        table["Proximo_Mantenimiento"] = pd.to_datetime(table["Proximo_Mantenimiento"]).dt.date
    except Exception:
        pass

    styled = style_status(table)
    try:
        st.dataframe(styled, use_container_width=True)
    except Exception:
        st.dataframe(table, use_container_width=True)

    st.divider()
    st.subheader("Abrir ficha")
    cols = st.columns(4)
    # ✅ Unique keys: keep same label, change only the key
    for i, ficha in enumerate(table["Ficha"].astype(str).tolist()):
        with cols[i % 4]:
            if st.button(f"🗂️ {ficha}", key=f"open_{i}_{safe_key(ficha)}"):
                go_detail(str(ficha))

def detail_view(ficha: str):
    st.button("⬅️ Volver a la lista", on_click=go_list)
    st.header(f"Ficha: {ficha}")

    df = load_data()
    row = df[df["Ficha"].astype(str).str.strip() == str(ficha).strip()].head(1)
    if row.empty:
        st.error("Ficha no encontrada en el Excel.")
        return

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("Modelo", str(row["Modelo"].iloc[0]))
    with c2: st.metric("Location", str(row["Location"].iloc[0]))
    with c3:
        f = row["Fecha_parsed"].iloc[0]
        st.metric("Fecha último mant. (Excel)", f.date().isoformat() if pd.notnull(f) else "—")

    st.subheader("📝 Nuevo mantenimiento")
    with st.form("new_rec"):
        fecha_rec = st.date_input("Fecha del mantenimiento", value=date.today())
        notas = st.text_area("Notas", "")
        piezas = st.text_area("Piezas consumidas", "")
        ok = st.form_submit_button("💾 Guardar")
    if ok:
        if update_excel_date(ficha, fecha_rec):
            try:
                load_data.clear()
            except Exception:
                pass
            st.success("Guardado en Excel.")
            go_list()
        else:
            st.warning("No se pudo actualizar el Excel.")

# ---------------------------
# Main
# ---------------------------
def main():
    st.title("📋 Seguimiento de Mantenimientos — MISMO APP (con escritura confiable)")
    if st.session_state.selected_ficha is None:
        list_view()
    else:
        detail_view(st.session_state.selected_ficha)

if __name__ == "__main__":
    main()
