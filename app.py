import os
import json
import pandas as pd
import streamlit as st
from datetime import datetime, date
import gspread
from google.oauth2 import service_account
import unicodedata
import re

# ========= CONFIG =========
EXCEL_PATH = "Mantenimiento TA(5).xlsx"
SHEET_ID = st.secrets["SHEET_ID"]  # Google Sheet ID in Streamlit secrets
GSERVICE_ACCOUNT_JSON = st.secrets["GSERVICE_ACCOUNT_JSON"]

# ========= HELPERS =========
def normalize(text: str) -> str:
    """Normalize text: lowercase, remove accents, collapse spaces and punctuation"""
    if not isinstance(text, str):
        return ""
    text = text.lower()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return text.strip()

def match_col(df: pd.DataFrame, target: str):
    """Find best matching column in df for target word"""
    target_n = normalize(target)
    for col in df.columns:
        if target_n in normalize(col):
            return col
    return None

def safe_key(val: str) -> str:
    s = str(val)
    s = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in s)
    if len(s) > 60:
        s = s[:60]
    return s or "blank"

def get_gs_client():
    creds_dict = json.loads(GSERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets",
                            "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def load_df():
    if os.path.exists(EXCEL_PATH):
        df = pd.read_excel(EXCEL_PATH)
    else:
        client = get_gs_client()
        sheet = client.open_by_key(SHEET_ID).sheet1
        df = pd.DataFrame(sheet.get_all_records())
    return df

def save_df(df):
    # Save Excel locally
    df.to_excel(EXCEL_PATH, index=False)

    # Save to Google Sheets
    client = get_gs_client()
    sheet = client.open_by_key(SHEET_ID).sheet1
    sheet.clear()
    # Convert datetimes to strings for Sheets
    df2 = df.copy()
    for c in df2.columns:
        if pd.api.types.is_datetime64_any_dtype(df2[c]):
            df2[c] = df2[c].dt.strftime("%d/%m/%Y")
    sheet.update([df2.columns.values.tolist()] + df2.astype(str).values.tolist())

def compute_status(df: pd.DataFrame, threshold_days: int = 60):
    # detect likely date column name automatically
    fecha_col = match_col(df, "fecha ultimo mantenimiento")
    if not fecha_col:
        return df
    today = datetime.today()
    df = df.copy()
    df["_fecha_parsed"] = pd.to_datetime(df[fecha_col], dayfirst=True, errors="coerce")
    df["Días desde último mant."] = (pd.Timestamp(today) - df["_fecha_parsed"]).dt.days
    df["Estado"] = df["Días desde último mant."].apply(
        lambda d: "Verde" if (pd.notnull(d) and float(d) < float(threshold_days)) else "Rojo"
    )
    return df

# ========= VIEWS =========
def list_view(sheet_id: str):
    df = load_df()
    df = compute_status(df)

    ficha_col = match_col(df, "ficha")
    if not ficha_col:
        st.error("No se encontró columna de Ficha en tu Excel/Sheet.")
        st.write("Columnas detectadas:", list(df.columns))
        return

    st.subheader("Listado de fichas")
    cols = st.columns(4)
    for i, ficha in enumerate(df[ficha_col].astype(str).tolist()):
        key = f"open_{i}_{safe_key(ficha)}"
        with cols[i % 4]:
            if st.button(f"🗂️ {ficha}", key=key):
                st.session_state["selected_ficha"] = ficha
                st.rerun()

def detail_view(sheet_id: str, ficha: str):
    st.button("⬅️ Volver", on_click=lambda: (st.session_state.update({"selected_ficha": None}), st.rerun()))

    df = load_df()
    ficha_col = match_col(df, "ficha")
    if not ficha_col:
        st.error("No se encontró columna de Ficha.")
        return
    fecha_col = match_col(df, "fecha ultimo mantenimiento")
    if not fecha_col:
        st.error("No se encontró columna de fecha de mantenimiento.")
        return

    row_idx = df[df[ficha_col].astype(str).str.strip() == str(ficha).strip()].index
    if row_idx.empty:
        st.error("Ficha no encontrada")
        return

    st.header(f"Ficha: {ficha}")
    # Show row info
    st.dataframe(df.loc[row_idx, :], use_container_width=True)

    new_date = st.date_input("Nueva fecha de mantenimiento", value=date.today(), key=f"date_{safe_key(ficha)}")
    if st.button("Guardar", key=f"save_{safe_key(ficha)}"):
        df.loc[row_idx, fecha_col] = pd.to_datetime(new_date).strftime("%d/%m/%Y")
        save_df(df)
        st.success("Mantenimiento actualizado en Excel y sincronizado con Google Sheets ✅")
        st.session_state["selected_ficha"] = None
        st.rerun()

# ========= MAIN =========
def main():
    st.title("📋 Mantenimiento de Fichas")
    if "selected_ficha" not in st.session_state or st.session_state["selected_ficha"] is None:
        list_view(SHEET_ID)
    else:
        detail_view(SHEET_ID, st.session_state["selected_ficha"])

if __name__ == "__main__":
    main()
