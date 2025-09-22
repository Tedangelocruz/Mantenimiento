import os
import re
import json
import shutil
from io import BytesIO
from datetime import datetime, date, timedelta
import mimetypes

import pandas as pd
import streamlit as st

# Google Cloud imports
try:
    from google.oauth2.service_account import Credentials
    from google.cloud import storage
    from google.api_core.exceptions import NotFound
except Exception as e:
    st.warning("google-cloud-storage not installed. Add 'google-cloud-storage>=2.16' to requirements.txt")
    raise

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
# Secrets / GCS helpers
# ---------------------------
def _load_gcs_secrets():
    """
    Returns (service_account_info_dict, bucket_name)
    Supports one of two formats in Streamlit Secrets:
      A) Nested tables:
         [gcp]
         bucket = "my-bucket"
         [gcp.gcp_service_account]
         type = "service_account"
         ...
      B) Flat keys:
         GCS_BUCKET = "my-bucket"
         GSERVICE_ACCOUNT_JSON = "{...json...}"
    """
    # A) Recommended nested format
    if "gcp" in st.secrets and "gcp_service_account" in st.secrets["gcp"]:
        sa_info = st.secrets["gcp"]["gcp_service_account"]
        bucket = st.secrets["gcp"].get("bucket")
        if not bucket:
            st.error("Missing [gcp].bucket in Secrets.")
            st.stop()
        return sa_info, bucket

    # B) Flat format (JSON string)
    if "GSERVICE_ACCOUNT_JSON" in st.secrets and "GCS_BUCKET" in st.secrets:
        try:
            sa_info = json.loads(st.secrets["GSERVICE_ACCOUNT_JSON"])
        except Exception:
            st.error("GSERVICE_ACCOUNT_JSON is not valid JSON.")
            st.stop()
        bucket = st.secrets["GCS_BUCKET"]
        return sa_info, bucket

    st.error("Missing credentials: add either [gcp] + [gcp.gcp_service_account] with 'bucket', or GSERVICE_ACCOUNT_JSON + GCS_BUCKET in Secrets.")
    st.stop()


@st.cache_resource(show_spinner=False)
def _gcs_client_and_bucket():
    sa_info, bucket_name = _load_gcs_secrets()
    creds = Credentials.from_service_account_info(sa_info)
    client = storage.Client(credentials=creds, project=sa_info.get("project_id"))
    bucket = client.bucket(bucket_name)
    return client, bucket


def gcs_upload_bytes(path_key, data, content_type=None):
    """Upload bytes to GCS at path_key. Returns GCS blob path_key."""
    _, bucket = _gcs_client_and_bucket()
    blob = bucket.blob(path_key)
    if not content_type:
        content_type = mimetypes.guess_type(path_key)[0] or "application/octet-stream"
    blob.upload_from_string(data, content_type=content_type)
    return path_key


def gcs_signed_url(path_key, minutes=60):
    """Create a short-lived signed URL to view the blob (no public ACL needed)."""
    client, bucket = _gcs_client_and_bucket()
    blob = bucket.blob(path_key)
    return blob.generate_signed_url(expiration=timedelta(minutes=minutes), method="GET")


def gcs_read_text(path_key):
    _, bucket = _gcs_client_and_bucket()
    blob = bucket.blob(path_key)
    if not blob.exists():
        return None
    return blob.download_as_text(encoding="utf-8")


def gcs_write_text(path_key, text):
    _, bucket = _gcs_client_and_bucket()
    blob = bucket.blob(path_key)
    blob.upload_from_string(text, content_type="application/json; charset=utf-8")


def gcs_list(prefix):
    """List object names under a prefix."""
    client, bucket = _gcs_client_and_bucket()
    return [b.name for b in client.list_blobs(bucket, prefix=prefix)]


def gcs_delete(path_key):
    """Delete a blob if it exists; ignore NotFound."""
    _, bucket = _gcs_client_and_bucket()
    blob = bucket.blob(path_key)
    try:
        blob.delete()
    except NotFound:
        pass


# ---------------------------
# Session-state navigation & edit/delete state
# ---------------------------
if "selected_ficha" not in st.session_state:
    st.session_state.selected_ficha = None  # None -> list view; string -> detail view

# Track which record is being edited / deleted per ficha
if "editing_rec_id" not in st.session_state:
    st.session_state.editing_rec_id = None

if "deleting_rec_id" not in st.session_state:
    st.session_state.deleting_rec_id = None


def go_list():
    st.session_state.selected_ficha = None
    st.session_state.editing_rec_id = None
    st.session_state.deleting_rec_id = None
    st.rerun()


def go_detail(ficha: str):
    st.session_state.selected_ficha = ficha
    st.session_state.editing_rec_id = None
    st.session_state.deleting_rec_id = None
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
        today_dt = pd.to_datetime(date.today())
        soon_cutoff = today_dt + pd.Timedelta(days=15)

        def fmt_next(val):
            try:
                if pd.isna(val):
                    return ""
                d = pd.to_datetime(val)
                if d < today_dt:
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


# ---------------------------
# Cloud storage paths for per-ficha data
# ---------------------------
def _ficha_prefix(ficha: str) -> str:
    return f"ficha-images/{safe_key(ficha)}/"


def metadata_path(ficha: str) -> str:
    return _ficha_prefix(ficha) + "metadata.json"


def load_metadata(ficha: str) -> dict:
    txt = gcs_read_text(metadata_path(ficha))
    if not txt:
        return {"records": []}
    try:
        data = json.loads(txt)
        if "records" not in data:
            data = migrate_old_metadata(data)
        return data
    except Exception:
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
    gcs_write_text(metadata_path(ficha), json.dumps(meta, ensure_ascii=False, indent=2))


def list_images_unassigned(ficha: str):
    """Images lying in the GCS prefix not linked to any record (for cleanup)."""
    prefix = _ficha_prefix(ficha)
    names = gcs_list(prefix)
    img_files = {name.split("/")[-1] for name in names if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))}
    linked = set()
    meta = load_metadata(ficha)
    for r in meta.get("records", []):
        for fn in r.get("images", []):
            linked.add(fn)
    return sorted(list(img_files - linked))


# ---------------------------
# Excel update helpers
# ---------------------------
def backup_excel() -> str:
    """Create a timestamped local backup of the Excel before writing, and push to GCS."""
    if not os.path.exists(EXCEL_PATH):
        return ""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.basename(EXCEL_PATH)
    name, ext = os.path.splitext(base)
    dst = os.path.join(BACKUP_DIR, f"{name}_backup_{ts}{ext}")
    try:
        shutil.copy2(EXCEL_PATH, dst)
        # Also copy to GCS for durability
        with open(dst, "rb") as f:
            gcs_upload_bytes(f"excel-backups/{os.path.basename(dst)}", f.read(),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        return dst
    except Exception:
        return ""


def update_excel_date(ficha: str, new_date: date) -> bool:
    """
    Update 'Fecha Ultiimo Mantenimiento' for the given ficha in the Excel file.
    Returns True if a row was updated and the file saved.
    NOTE: On Streamlit Cloud, local files are ephemeral; consider migrating to Google Sheets.
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


def _render_edit_form(ficha: str, rec: dict):
    st.markdown("### ✏️ Editar mantenimiento")
    with st.form(key=f"form_edit_{rec['id']}"):
        # Fecha
        try:
            parsed_fecha = pd.to_datetime(rec.get("fecha")).date()
        except Exception:
            parsed_fecha = date.today()
        new_fecha = st.date_input("Fecha del mantenimiento", value=parsed_fecha, key=f"edit_fecha_{rec['id']}")
        options = ["MP1", "MP2", "MP3", "MP4"]
        try:
            idx = options.index(rec.get("maintenance_type", "MP1"))
        except ValueError:
            idx = 0
        new_tipo = st.selectbox("Tipo de mantenimiento", options=options, index=idx, key=f"edit_tipo_{rec['id']}")
        new_notas = st.text_area("Notas / Detalles", value=rec.get("notas", ""), height=120, key=f"edit_notas_{rec['id']}")
        new_piezas = st.text_area("Piezas consumidas", value=rec.get("parts_consumed", ""), height=120, key=f"edit_piezas_{rec['id']}")

        st.markdown("**Imágenes actuales (marca para eliminar):**")
        imgs = rec.get("images", [])
        del_imgs = st.multiselect("Seleccionar imágenes a eliminar", options=imgs, default=[], key=f"edit_delimgs_{rec['id']}")

        st.markdown("**Agregar imágenes nuevas:**")
        up_new_files = st.file_uploader("Nuevas imágenes", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True,
                                        key=f"edit_uploader_{rec['id']}")

        update_excel = st.checkbox("Actualizar Excel con esta fecha editada", value=False, key=f"edit_update_excel_{rec['id']}")

        c1, c2, c3 = st.columns(3)
        submitted = c1.form_submit_button("💾 Guardar cambios")
        cancelled = c2.form_submit_button("Cancelar")
        if submitted:
            # Apply deletions
            for fn in del_imgs:
                gcs_delete(_ficha_prefix(ficha) + fn)
            # Keep remaining images
            remaining_imgs = [fn for fn in imgs if fn not in del_imgs]

            # Upload new images
            for up in up_new_files or []:
                ext = os.path.splitext(up.name)[1].lower()
                fname = f"{rec['id']}_{safe_key(os.path.splitext(up.name)[0])}{ext}"
                gcs_key = _ficha_prefix(ficha) + fname
                gcs_upload_bytes(gcs_key, up.getvalue(), content_type=up.type or None)
                remaining_imgs.append(fname)

            # Update record fields
            rec["fecha"] = new_fecha.isoformat()
            rec["maintenance_type"] = new_tipo
            rec["notas"] = new_notas
            rec["parts_consumed"] = new_piezas
            rec["images"] = remaining_imgs
            rec["modified_at"] = datetime.now().isoformat(timespec="seconds")

            # Persist metadata
            meta = load_metadata(ficha)
            # Replace this record by id
            meta["records"] = [rec if r.get("id") == rec["id"] else r for r in meta.get("records", [])]
            meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_metadata(ficha, meta)

            # Optionally update Excel
            if update_excel:
                ok = update_excel_date(ficha, new_fecha)
                if ok:
                    try:
                        load_data.clear()
                    except Exception:
                        pass
                    st.success("Cambios guardados y Excel actualizado.")
                else:
                    st.warning("Cambios guardados pero no se pudo actualizar el Excel.")
            else:
                st.success("Cambios guardados.")

            st.session_state.editing_rec_id = None
            st.rerun()

        if cancelled:
            st.session_state.editing_rec_id = None
            st.info("Edición cancelada.")
            st.rerun()


def _render_delete_form(ficha: str, rec: dict):
    st.markdown("### 🗑️ Eliminar mantenimiento")
    st.warning("Esta acción eliminará el mantenimiento y **todas** sus imágenes asociadas.")
    with st.form(key=f"form_delete_{rec['id']}"):
        upd_excel = st.checkbox("Después de eliminar, actualizar Excel a la última fecha restante (si existe)", value=False,
                                key=f"del_update_excel_{rec['id']}")
        c1, c2 = st.columns(2)
        do_delete = c1.form_submit_button("❗ Eliminar definitivamente")
        cancel = c2.form_submit_button("Cancelar")
        if do_delete:
            # Delete images
            for fn in rec.get("images", []) or []:
                gcs_delete(_ficha_prefix(ficha) + fn)
            # Remove record
            meta = load_metadata(ficha)
            meta["records"] = [r for r in meta.get("records", []) if r.get("id") != rec["id"]]
            meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_metadata(ficha, meta)

            # Optionally update Excel to latest remaining date
            if upd_excel and meta["records"]:
                try:
                    dates = [pd.to_datetime(r.get("fecha")) for r in meta["records"] if r.get("fecha")]
                    latest = max(dates).date() if dates else None
                    if latest:
                        ok = update_excel_date(ficha, latest)
                        if ok:
                            try:
                                load_data.clear()
                            except Exception:
                                pass
                            st.success("Eliminado. Excel actualizado a la última fecha restante.")
                        else:
                            st.warning("Eliminado. No se pudo actualizar el Excel.")
                    else:
                        st.info("Eliminado. No hay fechas válidas para actualizar el Excel.")
                except Exception:
                    st.warning("Eliminado. No se pudo determinar la última fecha para el Excel.")
            else:
                st.success("Mantenimiento eliminado.")
            st.session_state.deleting_rec_id = None
            st.rerun()
        if cancel:
            st.session_state.deleting_rec_id = None
            st.info("Eliminación cancelada.")
            st.rerun()


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
            tipo = st.selectbox("Tipo de mantenimiento", options=["MP1", "MP2", "MP3", "MP4"], index=0, key="rec_tipo")
        with colB:
            notas = st.text_area("Notas / Detalles", value="", height=120, placeholder="Trabajo realizado, observaciones...", key="rec_notas")
            piezas = st.text_area("Piezas consumidas", value="", height=120, placeholder="Lista de piezas/consumibles (uno por línea o separado por comas).", key="rec_piezas")

        st.markdown("**Evidencia fotográfica para este mantenimiento**")
        up_files = st.file_uploader("Agregar imágenes", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True, key="rec_uploader")

        saved = st.form_submit_button("💾 Guardar mantenimiento")
    if saved:
        rec_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        img_names = []
        for up in up_files or []:
            ext = os.path.splitext(up.name)[1].lower()
            fname = f"{rec_id}_{safe_key(os.path.splitext(up.name)[0])}{ext}"
            gcs_key = _ficha_prefix(ficha) + fname
            gcs_upload_bytes(gcs_key, up.getvalue(), content_type=up.type or None)
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
        meta["records"] = sorted(meta["records"], key=lambda r: (r.get("fecha") or "", r.get("id", "")), reverse=True)
        meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
        save_metadata(ficha, meta)

        # ---- update Excel with the new maintenance date ----
        ok = update_excel_date(ficha, fecha_rec)
        if ok:
            # Clear cache so the main list reloads fresh
            try:
                load_data.clear()
            except Exception:
                pass
            st.success("Mantenimiento guardado y Excel actualizado. Regresando a la lista principal...")
            go_list()
        else:
            st.warning("Se guardó el mantenimiento pero no se pudo actualizar el Excel. (En Cloud el Excel local es temporal).")

    st.divider()
    st.subheader("📚 Historial de mantenimientos (con fotos)")

    meta = load_metadata(ficha)
    records = meta.get("records", [])
    if not records:
        st.info("Sin registros guardados todavía. Usa el formulario de arriba para crear el primero.")
    else:
        for idx, rec in enumerate(records):
            try:
                c = st.container(border=True)
            except TypeError:
                c = st.expander("Registro", expanded=True)
            with c:
                top_cols = st.columns([2, 1, 1, 1])
                with top_cols[0]:
                    st.markdown(f"**Fecha:** {rec.get('fecha', '—')}")
                    st.markdown(f"**Notas:** {rec.get('notas', '')}")
                with top_cols[1]:
                    st.markdown(f"**Tipo:** {rec.get('maintenance_type', '—')}")
                with top_cols[2]:
                    img_count = len(rec.get('images', []))
                    st.markdown(f"**Imágenes:** {img_count}")
                with top_cols[3]:
                    st.markdown(f"**ID:** `{rec.get('id', '')}`")

                if rec.get("parts_consumed"):
                    st.markdown("**Piezas consumidas:**")
                    st.code(rec.get("parts_consumed", ""), language="")

                # Buttons: Edit / Delete
                bcols = st.columns([1, 1, 6])
                with bcols[0]:
                    if st.button("✏️ Editar", key=f"btn_edit_{rec['id']}"):
                        st.session_state.editing_rec_id = rec["id"]
                        st.session_state.deleting_rec_id = None
                        st.experimental_rerun()
                with bcols[1]:
                    if st.button("🗑️ Eliminar", key=f"btn_del_{rec['id']}"):
                        st.session_state.deleting_rec_id = rec["id"]
                        st.session_state.editing_rec_id = None
                        st.experimental_rerun()

                # Thumbnails grid from GCS (signed URLs)
                imgs = rec.get("images", [])
                if imgs:
                    cols = st.columns(3)
                    for i, fn in enumerate(imgs):
                        gcs_key = _ficha_prefix(ficha) + fn
                        try:
                            url = gcs_signed_url(gcs_key, minutes=30)
                            with cols[i % 3]:
                                st.image(url, use_column_width=True, caption=fn)
                        except Exception as e:
                            with cols[i % 3]:
                                st.warning(f"No se pudo mostrar {fn}: {e}")

                # Inline editor / delete confirmation
                if st.session_state.editing_rec_id == rec["id"]:
                    _render_edit_form(ficha, rec)
                if st.session_state.deleting_rec_id == rec["id"]:
                    _render_delete_form(ficha, rec)

    # Optional: show orphan images not linked to any record
    orphans = list_images_unassigned(ficha)
    if orphans:
        with st.expander("🧹 Imágenes sueltas (no asociadas a ningún registro)"):
            st.caption("Estas imágenes están en la nube pero no pertenecen a ningún mantenimiento guardado.")
            cols = st.columns(3)
            for i, fn in enumerate(orphans):
                gcs_key = _ficha_prefix(ficha) + fn
                url = gcs_signed_url(gcs_key, minutes=30)
                with cols[i % 3]:
                    st.image(url, use_column_width=True, caption=fn)


# ---------------------------
# Main (single-tab routing)
# ---------------------------
def list_view_entry():
    st.title("📋 Seguimiento de Mantenimientos por Ficha — una sola pestaña (con GCS + editar/eliminar)")

    # Quick secrets sanity check (hidden behind an expander)
    with st.expander("⚙️ Diagnóstico de credenciales (ocultar en producción)"):
        if st.button("Probar acceso a GCS"):
            try:
                client, bucket = _gcs_client_and_bucket()
                test_blob = f"healthchecks/{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                gcs_upload_bytes(test_blob, b"ok", "text/plain")
                url = gcs_signed_url(test_blob, minutes=1)
                st.success(f"Subida OK a gs://{bucket.name}/{test_blob}")
                st.write("Signed URL (temporal):", url)
            except Exception as e:
                st.error(f"Error probando GCS: {e}")

    if st.session_state.selected_ficha is None:
        list_view()
    else:
        detail_view(st.session_state.selected_ficha)


def main():
    list_view_entry()


if __name__ == "__main__":
    main()

