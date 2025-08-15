
import os
import json
import unicodedata
from datetime import datetime, date

import pandas as pd
import streamlit as st
import gspread
from google.oauth2 import service_account

# ========= CONFIG =========
EXCEL_PATH = "Mantenimiento TA(5).xlsx"
SHEET_ID = st.secrets["SHEET_ID"]            # your Google Sheet ID (in Secrets)
GSERVICE_ACCOUNT_JSON = st.secrets["GSERVICE_ACCOUNT_JSON"]  # service account JSON (in Secrets)

# ========= UTIL =========
def _norm(s: str) -> str:
    \"\"\"normalize text: lowercase, remove accents, collapse spaces and punctuation\"\"\"
    s = str(s).strip().lower()
    s = \"\".join(ch for ch in unicodedata.normalize(\"NFKD\", s) if not unicodedata.combining(ch))
    for ch in [\",\", \".\", \";\", \":\", \"-\", \"_\"]:
        s = s.replace(ch, \" \")
    s = \" \".join(s.split())
    return s

def detect_col(df: pd.DataFrame, aliases: list[str]) -> str:
    \"\"\"Find the first matching column in df for any of the aliases (robust match).\"\"\"
    norm_map = {col: _norm(col) for col in df.columns}
    alias_norms = [_norm(a) for a in aliases]
    for col, ncol in norm_map.items():
        if ncol in alias_norms:
            return col
    # try partial contains
    for col, ncol in norm_map.items():
        if any(a in ncol for a in alias_norms):
            return col
    raise KeyError(f\"No matching column found for aliases: {aliases} in columns={list(df.columns)}\")

# canonical aliases we will recognize
FICHA_ALIASES = [\"ficha\"]
MODELO_ALIASES = [\"modelo\"]
LOCATION_ALIASES = [\"location\", \"ubicacion\", \"ubicación\"]
DATE_ALIASES = [
    \"fecha ulTiiMo mantenimiento\",
    \"fecha ultimo mantenimiento\",
    \"fecha último mantenimiento\",
    \"fecha de ultimo mantenimiento\",
    \"fecha de último mantenimiento\",
]

# ========= IO =========
def load_excel() -> tuple[pd.DataFrame, dict]:
    df = pd.read_excel(EXCEL_PATH, sheet_name=0)
    # detect important columns
    ficha_col = detect_col(df, FICHA_ALIASES)
    modelo_col = detect_col(df, MODELO_ALIASES)
    location_col = detect_col(df, LOCATION_ALIASES)
    date_col = detect_col(df, DATE_ALIASES)

    # keep original names but also return them
    return df, {\"ficha\": ficha_col, \"modelo\": modelo_col, \"location\": location_col, \"date\": date_col}

def save_excel(df: pd.DataFrame):
    with pd.ExcelWriter(EXCEL_PATH, engine=\"openpyxl\", mode=\"w\") as writer:
        df.to_excel(writer, index=False)

def get_gs_client():
    creds_dict = json.loads(GSERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=[\"https://www.googleapis.com/auth/spreadsheets\",
                \"https://www.googleapis.com/auth/drive\"]
    )
    return gspread.authorize(creds)

def save_to_gsheet(df: pd.DataFrame, sheet_id: str):
    client = get_gs_client()
    sh = client.open_by_key(sheet_id)
    ws = sh.sheet1
    ws.clear()
    # convert any pandas timestamps to strings for Sheets
    df2 = df.copy()
    for c in df2.columns:
        if pd.api.types.is_datetime64_any_dtype(df2[c]):
            df2[c] = df2[c].dt.strftime(\"%d/%m/%Y\")
    ws.update([df2.columns.astype(str).tolist()] + df2.astype(str).values.tolist())

# ========= APP LOGIC =========
def compute_status(df: pd.DataFrame, cols: dict, threshold_days=90) -> pd.DataFrame:
    out = df.copy()
    # make a parsed date column
    out[\"_fecha_parsed\"] = pd.to_datetime(out[cols[\"date\"]], dayfirst=True, errors=\"coerce\")
    today = pd.Timestamp(date.today())

    out[\"Días desde último mant.\"] = (today - out[\"_fecha_parsed\"]).dt.days
    out[\"Estado\"] = out[\"Días desde último mant.\"].apply(
        lambda d: \"Verde\" if pd.notnull(d) and float(d) < float(threshold_days) else \"Rojo\"
    )
    out[\"Proximo_Mantenimiento\"] = out[\"_fecha_parsed\"] + pd.DateOffset(months=1, days=15)
    return out

def list_view(sheet_id: str):
    df, cols = load_excel()
    dfv = compute_status(df, cols, threshold_days=90)

    # show styled table
    show = dfv[[cols[\"ficha\"], cols[\"modelo\"], cols[\"location\"], cols[\"date\"], \"Proximo_Mantenimiento\", \"Días desde último mant.\", \"Estado\"]].copy()
    show = show.rename(columns={cols[\"ficha\"]:\"Ficha\", cols[\"modelo\"]:\"Modelo\", cols[\"location\"]:\"Location\", cols[\"date\"]:\"Fecha último mantenimiento\"})

    # style Estado column
    try:
        styler = show.style.applymap(
            lambda v: (\"background-color: #2e7d32; color: white\" if v == \"Verde\"
                       else (\"background-color: #c62828; color: white\" if v == \"Rojo\" else \"\")),
            subset=[\"Estado\"]
        )
        st.dataframe(styler, use_container_width=True)
    except Exception:
        st.dataframe(show, use_container_width=True)

    st.subheader(\"Abrir ficha\")
    cols_ = st.columns(4)
    for i, ficha in enumerate(show[\"Ficha\"].astype(str).tolist()):
        key = f\"open_{i}_{ficha.replace(' ','_')}\"
        with cols_[i % 4]:
            if st.button(f\"🗂️ {ficha}\", key=key):
                st.session_state[\"selected_ficha\"] = ficha
                st.rerun()

def detail_view(sheet_id: str, ficha: str):
    st.button(\"⬅️ Volver a la lista\", on_click=lambda: (st.session_state.update({\"selected_ficha\": None}), st.rerun()))
    df, cols = load_excel()
    row = df[df[cols[\"ficha\"]].astype(str).str.strip() == str(ficha).strip()]
    if row.empty:
        st.error(\"Ficha no encontrada en el Excel.\")
        return
    row = row.iloc[0]

    st.header(f\"Ficha: {ficha}\")
    c1, c2, c3 = st.columns(3)
    with c1: st.metric(\"Modelo\", str(row[cols[\"modelo\"]]))
    with c2: st.metric(\"Location\", str(row[cols[\"location\"]]))
    with c3:
        last = pd.to_datetime(row[cols[\"date\"]], dayfirst=True, errors=\"coerce\")
        st.metric(\"Fecha último mant.\", last.date().isoformat() if pd.notnull(last) else \"—\")

    st.subheader(\"📝 Nuevo mantenimiento\")
    with st.form(\"new_rec\"):
        fecha_rec = st.date_input(\"Fecha\", value=date.today())
        ok = st.form_submit_button(\"💾 Guardar\")
    if ok:
        # update the real date column in df
        df.loc[df[cols[\"ficha\"]].astype(str).str.strip() == str(ficha).strip(), cols[\"date\"]] = pd.to_datetime(fecha_rec).strftime(\"%d/%m/%Y\")
        save_excel(df)
        save_to_gsheet(df, sheet_id)
        st.success(\"Actualizado en Excel y sincronizado con Google Sheets.\")
        st.session_state[\"selected_ficha\"] = None
        st.rerun()

def main():
    st.title(\"📋 Mantenimiento — Excel + Google Sheets\")
    if \"selected_ficha\" not in st.session_state or st.session_state[\"selected_ficha\"] is None:
        list_view(SHEET_ID)
    else:
        detail_view(SHEET_ID, st.session_state[\"selected_ficha\"])

if __name__ == \"__main__\":
    main()
