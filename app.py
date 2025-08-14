
import os
import re
import json
import shutil
from io import BytesIO
from datetime import datetime, date, timedelta
import pandas as pd
import streamlit as st

# ---------------------------
# Config
# ---------------------------
st.set_page_config(page_title="Mantenimiento - Fichas", layout="wide")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
EXCEL_PATH = os.path.join(APP_DIR, "Mantenimiento TA(5).xlsx")
BACKUP_DIR = os.path.join(APP_DIR, "backups")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# Fichas a excluir (case-insensitive)
EXCLUDE_FICHAS = {"HONDO VALLE", "VILLA RIVA", "SAJOMA", "PINA", "AIC", "ENRIQUILLO"}
EXCLUDE_FICHAS_UPPER = {s.upper() for s in EXCLUDE_FICHAS}

# ---------------------------
# Session-state navigation
# ---------------------------
if "selected_ficha" not in st.session_state:
    st.session_state.selected_ficha = None  # None -> list view; string -> detail view

def go_list():
    st.session_state.selected_ficha = None
    st.rerun()

def go_detail(ficha: str):
    st.session_state.selected_ficha = ficha
    st.rerun()

# ---------------------------
# Helpers
# ---------------------------
def cache_decorator():
    if hasattr(st, "cache_data"):
        return st.cache_data(show_spinner=False)
    return st.cache(show_spinner=False)

@cache_decorator()
def load_data(excel_path: str) -> pd.DataFrame:
    df = pd.read_excel(excel_path, sheet_name=0)
    df.columns = [str(c).strip() for c in df.columns]
    required = ["Ficha", "Modelo", "Location", "Fecha Ultiimo Mantenimiento"]
    for col in required:
        if col not in df.columns:
            st.error(f"Falta la columna requerida: '{col}'. Columnas encontradas: {list(df.columns)}")
            st.stop()
    # Parse date (día primero)
    df["Fecha_parsed"] = pd.to_datetime(df["Fecha Ultiimo Mantenimiento"], dayfirst=True, errors="coerce")
    # Normalizar Ficha
    df["Ficha"] = df["Ficha"].astype(str).str.strip()
    df.loc[df["Ficha"].isin(["nan", "NaN", "None", ""]), "Ficha"] = None
    df = df.dropna(subset=["Ficha"]).copy()
    # Excluir fichas (case-insensitive)
    df = df[~df["Ficha"].str.upper().isin(EXCLUDE_FICHAS_UPPER)].copy()
    # Próximo mantenimiento = Fecha_parsed + 1 mes + 15 días
    df["Proximo_Mantenimiento"] = df["Fecha_parsed"] + pd.DateOffset(months=1, days=15)
    return df

def compute_status(df: pd.DataFrame, threshold_days: int) -> pd.DataFrame:
    today = date.today()
    out = df.copy()
    out["Días desde último mant."] = (today - out["Fecha_parsed"].dt.date).apply(lambda x: x.days if pd.notnull(x) else None)
    def status(d):
        if d is None:
            return "Rojo"
        try:
            return "Verde" if int(d) < threshold_days else "Rojo"
        except Exception:
            return "Rojo"
    out["Estado"] = out["Días desde último mant."].apply(status)
    return out

def style_status(df: pd.DataFrame):
    # Color Estado + Próximo Mantenimiento (due soon highlighting)
    try:
        styler = df.style.applymap(
            lambda v: ("background-color: #2e7d32; color: white" if v == "Verde"
                       else ("background-color: #c62828; color: white" if v == "Rojo" else "")),
            subset=["Estado"]
        )
        # Color Proximo Mantenimiento
        today = pd.to_datetime(date.today())
        soon_cutoff = today + pd.Timedelta(days=15)
        def fmt_next(val):
            try:
                if pd.isna(val):
                    return ""
                d = pd.to_datetime(val)
                if d < today:
                    return "background-color: #ffcccc"  # red
                elif d <= soon_cutoff:
                    return "background-color: #fff4cc"  # yellow
                else:
                    return "background-color: #ccffcc"  # green
            except Exception:
                return ""
        if "Proximo Mantenimiento" in df.columns:
            styler = styler.applymap(fmt_next, subset=["Proximo Mantenimiento"])
        return styler
    except Exception:
        return df

def safe_key(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "_", str(name))

def ficha_dir(ficha: str) -> str:
    d = os.path.join(DATA_DIR, safe_key(ficha))
    os.makedirs(d, exist_ok=True)
    return d

def metadata_path(ficha: str) -> str:
    return os.path.join(ficha_dir(ficha), "metadata.json")

def load_metadata(ficha: str) -> dict:
    md_path = metadata_path(ficha)
    if os.path.exists(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Backward compatibility: ensure modern structure
                if "records" not in data:
                    data = migrate_old_metadata(data)
                return data
        except Exception:
            return {"records": []}
    return {"records": []}

def migrate_old_metadata(old: dict) -> dict:
    """
    Convert legacy metadata with single fields + images into records[].
    Legacy keys: fecha_ultima, notas, maintenance_type, parts_consumed, images{}
    """
    if not isinstance(old, dict):
        return {"records": []}
    rec = {
        "id": datetime.now().strftime("%Y%m%d_%H%M%S"),
        "fecha": old.get("fecha_ultima") or date.today().isoformat(),
        "maintenance_type": old.get("maintenance_type") or "MP1",
        "notas": old.get("notas") or "",
        "parts_consumed": old.get("parts_consumed") or "",
        "images": sorted(list((old.get("images") or {}).keys())),
        "created_at": datetime.now().isoformat(timespec="seconds")
    }
    if rec["images"] is None:
        rec["images"] = []
    return {"records": [rec] if (rec["fecha"] or rec["notas"] or rec["images"]) else []}

def save_metadata(ficha: str, meta: dict) -> None:
    md_path = metadata_path(ficha)
    with open(md_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def list_images_unassigned(ficha: str):
    """Images lying in the folder not linked to any record (for cleanup)."""
    d = ficha_dir(ficha)
    files = set([fn for fn in os.listdir(d) if fn.lower().endswith((".png",".jpg",".jpeg",".webp"))])
    linked = set()
    meta = load_metadata(ficha)
    for r in meta.get("records", []):
        for fn in r.get("images", []):
            linked.add(fn)
    return sorted(list(files - linked))

# ---------------------------
# Excel update helpers
# ---------------------------
def backup_excel() -> str:
    """Create a timestamped backup of the Excel before writing."""
    if not os.path.exists(EXCEL_PATH):
        return ""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(EXCEL_PATH)
    name, ext = os.path.splitext(base)
    dst = os.path.join(BACKUP_DIR, f"{name}_backup_{ts}{ext}")
    try:
        shutil.copy2(EXCEL_PATH, dst)
        return dst
    except Exception:
        return ""

def update_excel_date(ficha: str, new_date: date) -> bool:
    """
    Update 'Fecha Ultiimo Mantenimiento' for the given ficha in the Excel file.
    Returns True if a row was updated and the file saved.
    """
    try:
        df = pd.read_excel(EXCEL_PATH, sheet_name=0)
    except Exception as e:
        st.error(f"No se pudo abrir el Excel: {e}")
        return False

    df.columns = [str(c).strip() for c in df.columns]
    if "Ficha" not in df.columns or "Fecha Ultiimo Mantenimiento" not in df.columns:
        st.error("El Excel no tiene las columnas requeridas ('Ficha', 'Fecha Ultiimo Mantenimiento').")
        return False

    # Normalizar Ficha para comparar
    df["Ficha"] = df["Ficha"].astype(str).str.strip()
    mask = df["Ficha"] == str(ficha).strip()

    if not mask.any():
        st.warning("Ficha no encontrada en el Excel; no se actualizó la fecha.")
        return False

    # Formato día/mes/año (coincide con la lectura dayfirst)
    new_str = new_date.strftime("%d/%m/%Y")
    df.loc[mask, "Fecha Ultiimo Mantenimiento"] = new_str

    # Backup y guardado
    backup_excel()
    try:
        # Mantener el resto de datos tal cual
        with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl", mode="w") as writer:
            df.to_excel(writer, index=False)
        return True
    except Exception as e:
        st.error(f"Error al guardar el Excel: {e}")
        return False

# ---------------------------
# Views
# ---------------------------
def list_view():
    st.subheader("Listado de fichas")
    threshold = st.number_input("Umbral de días para estar 'al día'", min_value=1, max_value=365, value=90, step=1, key="thr_list")
    df_load = load_data(EXCEL_PATH)
    df_status = compute_status(df_load, threshold)

    table = df_status[[
        "Ficha",
        "Modelo",
        "Location",
        "Fecha Ultiimo Mantenimiento",
        "Proximo_Mantenimiento",
        "Días desde último mant.",
        "Estado"
    ]].copy()

    table = table.rename(columns={
        "Fecha Ultiimo Mantenimiento": "Fecha (Excel)",
        "Proximo_Mantenimiento": "Proximo Mantenimiento"
    })

    with st.expander("🔎 Buscar y filtrar", expanded=True):
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        with c1:
            qtext = st.text_input("Buscar (Ficha/Modelo/Location)", value="", key="search").strip().lower()
        with c2:
            estados = sorted([x for x in table["Estado"].dropna().unique().tolist()])
            pick_estado = st.multiselect("Estado", estados, default=estados, key="f_est")
        with c3:
            locations = sorted([str(x) for x in table["Location"].fillna("").unique().tolist() if str(x) != ""])
            pick_loc = st.multiselect("Location", locations, default=locations, key="f_loc")
        with c4:
            modelos = sorted([str(x) for x in table["Modelo"].fillna("").unique().tolist() if str(x) != ""])
            pick_modelo = st.multiselect("Modelo", modelos, default=modelos, key="f_mod")

    ft = table.copy()
    if qtext:
        def matches(row):
            return any(qtext in str(row[c]).lower() for c in ["Ficha", "Modelo", "Location"])
        ft = ft[ft.apply(matches, axis=1)]
    if pick_estado:
        ft = ft[ft["Estado"].isin(pick_estado)]
    else:
        ft = ft[ft["Estado"].isna()]
    if pick_loc:
        ft = ft[ft["Location"].astype(str).isin(pick_loc)]
    else:
        ft = ft[ft["Location"].isna()]
    if pick_modelo:
        ft = ft[ft["Modelo"].astype(str).isin(pick_modelo)]
    else:
        ft = ft[ft["Modelo"].isna()]

    if "Proximo Mantenimiento" in ft.columns:
        try:
            ft["Proximo Mantenimiento"] = pd.to_datetime(ft["Proximo Mantenimiento"]).dt.date
        except Exception:
            pass

    ft = ft.sort_values(by=["Estado", "Proximo Mantenimiento", "Ficha"], ascending=[True, True, True])

    styled = style_status(ft)
    try:
        st.dataframe(styled, use_container_width=True)
    except Exception:
        st.dataframe(ft, use_container_width=True)

    st.divider()
    st.subheader("Abrir ficha")
    st.caption("Haz clic en una ficha para registrar un mantenimiento y ver el historial.")

    cols = st.columns(4)
    for i, ficha in enumerate(ft["Ficha"]):
        with cols[i % 4]:
            if st.button(f"🗂️ {ficha}", key=f"open_{ficha}"):
                go_detail(str(ficha))

def detail_view(ficha: str):
    st.button("⬅️ Volver a la lista", on_click=go_list)
    st.header(f"Ficha: {ficha}")

    df = load_data(EXCEL_PATH)
    row = df[df["Ficha"] == ficha].head(1)
    if row.empty:
        st.error("Ficha no encontrada en el Excel. Vuelve a la lista.")
        return

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Modelo", str(row["Modelo"].iloc[0]))
    with col2:
        st.metric("Location", str(row["Location"].iloc[0]))
    with col3:
        fecha_val = row["Fecha_parsed"].iloc[0]
        st.metric("Fecha último mantenimiento (Excel)", fecha_val.date().isoformat() if pd.notnull(fecha_val) else "—")

    # -------- Formulario para crear un NUEVO registro con imágenes --------
    meta = load_metadata(ficha)

    st.subheader("📝 Registro del último mantenimiento (crear nuevo)")
    with st.form(key="form_new_record"):
        colA, colB = st.columns(2)
        with colA:
            fecha_rec = st.date_input("Fecha del mantenimiento", value=date.today(), key="rec_fecha")
            tipo = st.selectbox("Tipo de mantenimiento", options=["MP1","MP2","MP3","MP4"], index=0, key="rec_tipo")
        with colB:
            notas = st.text_area("Notas / Detalles", value="", height=120, placeholder="Trabajo realizado, observaciones...", key="rec_notas")
            piezas = st.text_area("Piezas consumidas", value="", height=120, placeholder="Lista de piezas/consumibles (uno por línea o separado por comas).", key="rec_piezas")

        st.markdown("**Evidencia fotográfica para este mantenimiento**")
        up_files = st.file_uploader("Agregar imágenes", type=["png","jpg","jpeg","webp"], accept_multiple_files=True, key="rec_uploader")

        saved = st.form_submit_button("💾 Guardar mantenimiento")
    if saved:
        rec_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        save_dir = ficha_dir(ficha)
        img_names = []
        for up in up_files or []:
            ext = os.path.splitext(up.name)[1].lower()
            fname = f"{rec_id}_{safe_key(os.path.splitext(up.name)[0])}{ext}"
            path = os.path.join(save_dir, fname)
            with open(path, "wb") as f:
                f.write(up.getbuffer())
            img_names.append(fname)

        new_rec = {
            "id": rec_id,
            "fecha": fecha_rec.isoformat(),
            "maintenance_type": tipo,
            "notas": notas,
            "parts_consumed": piezas,
            "images": img_names,
            "created_at": datetime.now().isoformat(timespec="seconds")
        }
        meta.setdefault("records", [])
        meta["records"].append(new_rec)
        meta["records"] = sorted(meta["records"], key=lambda r: (r.get("fecha") or "", r.get("id","")), reverse=True)
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_metadata(ficha, meta)

        # ---- NEW: update Excel with the new maintenance date ----
        ok = update_excel_date(ficha, fecha_rec)
        if ok:
            # Clear cache so the main list reloads fresh
            try:
                load_data.clear()
            except Exception:
                pass
            st.success("Mantenimiento guardado y Excel actualizado. Regresando a la lista principal...")
            # Return to list so user immediately sees the updated green/red status
            go_list()
        else:
            st.warning("Se guardó el mantenimiento pero no se pudo actualizar el Excel.")

    st.divider()
    st.subheader("📚 Historial de mantenimientos (con fotos)")

    meta = load_metadata(ficha)
    records = meta.get("records", [])
    if not records:
        st.info("Sin registros guardados todavía. Usa el formulario de arriba para crear el primero.")
    else:
        for idx, rec in enumerate(records):
            with st.container(border=True):
                top_cols = st.columns([2,1,1,1])
                with top_cols[0]:
                    st.markdown(f"**Fecha:** {rec.get('fecha','—')}")
                    st.markdown(f"**Notas:** {rec.get('notas','')}")
                with top_cols[1]:
                    st.markdown(f"**Tipo:** {rec.get('maintenance_type','—')}")
                with top_cols[2]:
                    img_count = len(rec.get('images',[]))
                    st.markdown(f"**Imágenes:** {img_count}")
                with top_cols[3]:
                    st.markdown(f"**ID:** `{rec.get('id','')}`")

                if rec.get("parts_consumed"):
                    st.markdown("**Piezas consumidas:**")
                    st.code(rec.get("parts_consumed",""), language="")

                # Thumbnails grid
                imgs = rec.get("images", [])
                if imgs:
                    cols = st.columns(3)
                    for i, fn in enumerate(imgs):
                        img_path = os.path.join(ficha_dir(ficha), fn)
                        if os.path.exists(img_path):
                            with cols[i % 3]:
                                st.image(img_path, use_column_width=True, caption=fn)
                        else:
                            with cols[i % 3]:
                                st.warning(f"Archivo faltante: {fn}")

    # Optional: show orphan images not linked to any record
    orphans = list_images_unassigned(ficha)
    if orphans:
        with st.expander("🧹 Imágenes sueltas (no asociadas a ningún registro)"):
            st.caption("Estas imágenes están en la carpeta pero no pertenecen a ningún mantenimiento guardado.")
            cols = st.columns(3)
            for i, fn in enumerate(orphans):
                img_path = os.path.join(ficha_dir(ficha), fn)
                with cols[i % 3]:
                    st.image(img_path, use_column_width=True, caption=fn)

# ---------------------------
# Main (single-tab routing)
# ---------------------------
def main():
    st.title("📋 Seguimiento de Mantenimientos por Ficha — una sola pestaña")

    if st.session_state.selected_ficha is None:
        list_view()
    else:
        detail_view(st.session_state.selected_ficha)

if __name__ == "__main__":
    main()
