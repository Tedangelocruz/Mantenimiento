import os
import json
import pandas as pd
import streamlit as st
from datetime import datetime
from pathlib import Path
import gspread
from google.oauth2 import service_account

# ========= CONFIG =========
EXCEL_PATH = "Mantenimiento TA(5).xlsx"
SHEET_ID = st.secrets["SHEET_ID"]  # put your Google Sheet ID in secrets
GSERVICE_ACCOUNT_JSON = st.secrets["GSERVICE_ACCOUNT_JSON"]

# ========= HELPERS =========
def load_excel():
    return pd.read_excel(EXCEL_PATH)

def save_excel(df):
    with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, index=False)

def get_gs_client():
    creds_dict = json.loads(GSERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"]
    )
    return gspread.authorize(creds)

def save_to_gsheet(df, sheet_id):
    client = get_gs_client()
    sh = client.open_by_key(sheet_id)
    worksheet = sh.sheet1
    worksheet.clear()
    worksheet.update([df.columns.values.tolist()] + df.values.tolist())

def compute_status(df, threshold_days=30):
    df = df.copy()
    today = datetime.today()
    df["Días desde último mant."] = (
        pd.to_datetime(today) - pd.to_datetime(df["Fecha último mantenimiento"], errors="coerce")
    ).dt.days
    df["Estado"] = df["Días desde último mant."].apply(
        lambda d: "Verde" if pd.notnull(d) and int(d) < threshold_days else "Rojo"
    )
    return df

# ========= STREAMLIT =========
def list_view(sheet_id):
    df = load_excel()
    df = compute_status(df)
    st.dataframe(df.style.apply(lambda row: ["background-color: lightgreen" if row.Estado=="Verde" else "background-color: salmon"], axis=1))
    for idx, ficha in enumerate(df["Ficha"]):
        if st.button(f"🗂️ {ficha}", key=f"open_{idx}_{ficha}"):
            ficha_view(ficha, sheet_id)

def ficha_view(ficha, sheet_id):
    df = load_excel()
    row = df[df["Ficha"] == ficha].iloc[0]
    st.subheader(f"Ficha: {ficha}")
    st.write(row)

    new_date = st.date_input("Nuevo mantenimiento", datetime.today())
    if st.button("Guardar mantenimiento", key=f"save_{ficha}"):
        df.loc[df["Ficha"] == ficha, "Fecha último mantenimiento"] = pd.to_datetime(new_date)
        save_excel(df)
        save_to_gsheet(df, sheet_id)
        st.success("Mantenimiento actualizado y sincronizado con Google Sheets.")

def main():
    st.title("Mantenimiento")
    list_view(SHEET_ID)

if __name__ == "__main__":
    main()
