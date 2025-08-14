
import os
import re
import json
from datetime import datetime, date

import pandas as pd
import streamlit as st

# Google Sheets
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Mantenimientos — Google Sheets", layout="wide")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

EXCLUDE_FICHAS = {"HONDO VALLE", "VILLA RIVA", "SAJOMA", "PINA", "AIC", "ENRIQUILLO"}
EXCLUDE_UP = {s.upper() for s in EXCLUDE_FICHAS}

def get_sheet_id() -> str:
    sid = st.secrets.get("SHEET_ID") if hasattr(st, "secrets") else None
    if not sid:
        sid = os.environ.get("SHEET_ID", "").strip()
    if not sid:
        st.error("Falta configurar SHEET_ID (en Secrets o variable de entorno).")
        st.stop()
    return sid

def get_gs_client():
    if hasattr(st, "secrets") and "GSERVICE_ACCOUNT_JSON" in st.secrets:
        try:
            info = json.loads(st.secrets["GSERVICE_ACCOUNT_JSON"])
        except Exception as e:
            st.error(f"GSERVICE_ACCOUNT_JSON inválido: {e}")
            st.stop()
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        return gspread.authorize(creds)
    json_path = os.path.join(APP_DIR, "service_account.json")
    if os.path.exists(json_path):
        info = json.load(open(json_path, "r", encoding="utf-8"))
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        return gspread.authorize(creds)
    st.error("No se encontraron credenciales. Agrega GSERVICE_ACCOUNT_JSON en Secrets o 'service_account.json'.")
    st.stop()

if "selected_ficha" not in st.session_state:
    st.session_state.selected_ficha = None

def go_list(): st.session_state.selected_ficha = None; st.rerun()
def go_detail(ficha: str): st.session_state.selected_ficha = ficha; st.rerun()

def cache_decorator():
    if hasattr(st, "cache_data"):
        return st.cache_data(show_spinner=False, ttl=60)
    return st.cache(show_spinner=False)

def safe_key(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(name))

def ficha_dir(ficha: str) -> str:
    d = os.path.join(DATA_DIR, safe_key(ficha))
    os.makedirs(d, exist_ok=True)
    return d

def meta_path(ficha: str) -> str:
    return os.path.join(ficha_dir(ficha), "metadata.json")

def load_metadata(ficha: str) -> dict:
    p = meta_path(ficha)
    if os.path.exists(p):
        try:
            return json.load(open(p, "r", encoding="utf-8"))
        except Exception:
            return {"records": []}
    return {"records": []}

def save_metadata(ficha: str, meta: dict):
    p = meta_path(ficha)
    json.dump(meta, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

@cache_decorator()
def load_df_from_sheet(sheet_id: str) -> pd.DataFrame:
    client = get_gs_client()
    ws = client.open_by_key(sheet_id).sheet1
    rows = ws.get_all_records()
    df = pd.DataFrame(rows)
    df.columns = [str(c).strip() for c in df.columns]
    required = ["Ficha", "Modelo", "Location", "Fecha Ultiimo Mantenimiento"]
    for col in required:
        if col not in df.columns:
            st.error(f"Falta columna '{col}'. Columnas: {list(df.columns)}")
            st.stop()
    df["Ficha"] = df["Ficha"].astype(str).str.strip()
    df = df.dropna(subset=["Ficha"]).copy()
    df = df[~df["Ficha"].str.upper().isin(EXCLUDE_UP)].copy()
    df["Fecha_parsed"] = pd.to_datetime(df["Fecha Ultiimo Mantenimiento"], dayfirst=True, errors="coerce")
    df["Proximo_Mantenimiento"] = df["Fecha_parsed"] + pd.DateOffset(months=1, days=15)
    return df

def save_df_to_sheet(sheet_id: str, df: pd.DataFrame):
    client = get_gs_client()
    ws = client.open_by_key(sheet_id).sheet1
    df2 = df.fillna("")
    ws.clear()
    ws.update([df2.columns.tolist()] + df2.astype(str).values.tolist())

def update_sheet_date(sheet_id: str, ficha: str, new_date: date) -> bool:
    df = load_df_from_sheet(sheet_id)
    base_cols = [c for c in df.columns if c not in ("Fecha_parsed","Proximo_Mantenimiento")]
    core = df[base_cols].copy()
    mask = core["Ficha"].astype(str).str.strip() == str(ficha).strip()
    if not mask.any():
        st.warning("Ficha no encontrada en Google Sheets.")
        return False
    core.loc[mask, "Fecha Ultiimo Mantenimiento"] = new_date.strftime("%d/%m/%Y")
    try:
        save_df_to_sheet(sheet_id, core)
        try: load_df_from_sheet.clear()
        except Exception: pass
        return True
    except Exception as e:
        st.error(f"Error al guardar en Google Sheets: {e}")
        return False

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

    def to_estado(val):
        try:
            if pd.isna(val):
                return "Rojo"
            return "Verde" if float(val) < float(threshold_days) else "Rojo"
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
        # Optional: tint próximo mantenimiento by proximity
        try:
            today = pd.Timestamp(date.today())
            soon = today + pd.Timedelta(days=15)
            def tint(v):
                try:
                    d = pd.to_datetime(v)
                    if d < today: return "background-color:#ffcccc"
                    if d <= soon: return "background-color:#fff4cc"
                    return "background-color:#ccffcc"
                except Exception:
                    return ""
            if "Próximo Mantenimiento" in df.columns:
                styler = styler.applymap(tint, subset=["Próximo Mantenimiento"])
            elif "Proximo_Mantenimiento" in df.columns:
                styler = styler.applymap(tint, subset=["Proximo_Mantenimiento"])
        except Exception:
            pass
        return styler
    except Exception:
        return df

def list_view(sheet_id: str):
    st.subheader("Listado de fichas (Google Sheets)")
    thr = st.number_input("Umbral de días para estar 'al día'", min_value=1, max_value=365, value=90, step=1)

    df = compute_status(load_df_from_sheet(sheet_id), thr)
    table = df[[
        "Ficha","Modelo","Location","Fecha Ultiimo Mantenimiento","Proximo_Mantenimiento","Días desde último mant.","Estado"
    ]].copy()
    table = table.rename(columns={"Proximo_Mantenimiento": "Próximo Mantenimiento"})

    with st.expander("🔎 Buscar y filtrar", expanded=True):
        q = st.text_input("Buscar (Ficha/Modelo/Location)").strip().lower()
        estados = sorted([x for x in table["Estado"].dropna().unique().tolist()])
        pick_estado = st.multiselect("Estado", estados, default=estados)
    ft = table.copy()
    if q:
        ft = ft[ft.apply(lambda r: any(q in str(r[c]).lower() for c in ["Ficha","Modelo","Location"]), axis=1)]
    if pick_estado:
        ft = ft[ft["Estado"].isin(pick_estado)]

    try:
        ft["Próximo Mantenimiento"] = pd.to_datetime(ft["Próximo Mantenimiento"]).dt.date
    except Exception:
        pass

    # ✅ Apply styling for Estado / Próximo Mantenimiento
    styled = style_status(ft)
    try:
        st.dataframe(styled, use_container_width=True)
    except Exception:
        st.dataframe(ft, use_container_width=True)

    st.divider()
    st.subheader("Abrir ficha")
    cols = st.columns(4)
    # ✅ Unique keys to avoid DuplicateWidgetID
    for i, ficha in enumerate(ft["Ficha"].tolist()):
        with cols[i % 4]:
            if st.button(f"🗂️ {ficha}", key=f"open_{i}_{safe_key(ficha)}"):
                go_detail(str(ficha))

def detail_view(sheet_id: str, ficha: str):
    st.button("⬅️ Volver a la lista", on_click=go_list)
    st.header(f"Ficha: {ficha}")

    df = load_df_from_sheet(sheet_id)
    row = df[df["Ficha"] == ficha].head(1)
    if row.empty:
        st.error("Ficha no encontrada en Google Sheets.")
        return

    c1, c2, c3 = st.columns(3)
    with c1: st.metric("Modelo", str(row["Modelo"].iloc[0]))
    with c2: st.metric("Location", str(row["Location"].iloc[0]))
    with c3:
        f = row["Fecha_parsed"].iloc[0]
        st.metric("Fecha último mant. (Sheets)", f.date().isoformat() if pd.notnull(f) else "—")

    st.subheader("📝 Nuevo mantenimiento")
    with st.form("new_rec"):
        fecha_rec = st.date_input("Fecha del mantenimiento", value=date.today())
        tipo = st.selectbox("Tipo", ["MP1","MP2","MP3","MP4"])
        notas = st.text_area("Notas", "")
        piezas = st.text_area("Piezas consumidas", "")
        ok = st.form_submit_button("💾 Guardar")
    if ok:
        meta = load_metadata(ficha)
        rec_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        meta.setdefault("records", []).append({
            "id": rec_id, "fecha": fecha_rec.isoformat(), "maintenance_type": tipo,
            "notas": notas, "parts_consumed": piezas, "images": [],
            "created_at": datetime.now().isoformat(timespec="seconds")
        })
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_metadata(ficha, meta)

        if update_sheet_date(sheet_id, ficha, fecha_rec):
            st.success("Guardado y Google Sheets actualizado.")
            go_list()
        else:
            st.warning("Se guardó el registro local, pero no se pudo actualizar Google Sheets.")

    st.divider()
    st.subheader("📚 Historial")
    meta = load_metadata(ficha)
    for rec in meta.get("records", []):
        with st.container(border=True):
            st.markdown(f"**Fecha:** {rec.get('fecha','—')}  |  **Tipo:** {rec.get('maintenance_type','—')}")
            if rec.get("notas"): st.markdown(f"**Notas:** {rec['notas']}")
            if rec.get("parts_consumed"): st.markdown(f"**Piezas:** {rec['parts_consumed']}")

def main():
    st.title("📋 Seguimiento de Mantenimientos — Google Sheets")
    SHEET_ID = get_sheet_id()
    if st.session_state.selected_ficha is None:
        list_view(SHEET_ID)
    else:
        detail_view(SHEET_ID, st.session_state.selected_ficha)

if __name__ == "__main__":
    main()
