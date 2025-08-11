
import os
import re
import json
from datetime import datetime, date
import pandas as pd
import streamlit as st

# ---------------------------
# Config
# ---------------------------
st.set_page_config(page_title="Mantenimiento - Fichas", layout="wide")

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
EXCEL_PATH = os.path.join(APP_DIR, "Mantenimiento TA(5).xlsx")
os.makedirs(DATA_DIR, exist_ok=True)

# Fichas a excluir (case-insensitive)
EXCLUDE_FICHAS = {"HONDO VALLE", "VILLA RIVA", "SAJOMA", "PINA", "AIC", "ENRIQUILLO"}
EXCLUDE_FICHAS_UPPER = {s.upper() for s in EXCLUDE_FICHAS}

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
            st.error("Falta la columna requerida: '{}' en el Excel. Columnas encontradas: {}".format(col, list(df.columns)))
            st.stop()
    # Parse date (día primero)
    df["Fecha_parsed"] = pd.to_datetime(df["Fecha Ultiimo Mantenimiento"], dayfirst=True, errors="coerce")
    # Normalizar Ficha
    df["Ficha"] = df["Ficha"].astype(str).str.strip()
    df.loc[df["Ficha"].isin(["nan", "NaN", "None", ""]), "Ficha"] = None
    df = df.dropna(subset=["Ficha"]).copy()
    # Excluir fichas
    df = df[~df["Ficha"].str.upper().isin(EXCLUDE_FICHAS_UPPER)].copy()
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
                # Ensure structure for images
                if "images" not in data:
                    data["images"] = {}
                return data
        except Exception:
            return {"images": {}}
    return {"images": {}}

def save_metadata(ficha: str, meta: dict) -> None:
    md_path = metadata_path(ficha)
    with open(md_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def list_images(ficha: str):
    d = ficha_dir(ficha)
    return sorted([fn for fn in os.listdir(d) if fn.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))])

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
    def colorize(val):
        if val == "Verde":
            return "background-color: #2e7d32; color: white"
        if val == "Rojo":
            return "background-color: #c62828; color: white"
        return ""
    try:
        return df.style.applymap(lambda v: colorize(v) if isinstance(v, str) else "", subset=pd.IndexSlice[:, ["Estado"]])
    except Exception:
        return df

# Query params helpers
def get_query_params():
    if hasattr(st, "query_params"):
        return dict(st.query_params)
    elif hasattr(st, "experimental_get_query_params"):
        return st.experimental_get_query_params()
    return {}

def set_query_params(**params):
    if hasattr(st, "query_params"):
        st.query_params.clear()
        for k, v in params.items():
            st.query_params[k] = v
    elif hasattr(st, "experimental_set_query_params"):
        st.experimental_set_query_params(**params)

# ---------------------------
# Image Card Component
# ---------------------------
def image_card(ficha: str, image_filename: str, meta: dict, col):
    """
    Renders one image card with preview + editable date + caption + save/delete buttons.
    Stores per-image data in meta["images"][image_filename] = {display_date, uploaded_at, caption}
    """
    img_rel = os.path.join(ficha_dir(ficha), image_filename)
    info = meta.get("images", {}).get(image_filename, {})

    # Defaults: uploaded_at from file mtime, display_date default to uploaded_at or today
    if "uploaded_at" not in info:
        try:
            ts = datetime.fromtimestamp(os.path.getmtime(img_rel)).isoformat(timespec="seconds")
        except Exception:
            ts = datetime.now().isoformat(timespec="seconds")
        info["uploaded_at"] = ts
    if "display_date" not in info:
        try:
            info["display_date"] = date.fromisoformat(info["uploaded_at"][:10]).isoformat()
        except Exception:
            info["display_date"] = date.today().isoformat()
    if "caption" not in info:
        info["caption"] = ""

    # Persist defaults back to meta (in case they were missing)
    meta.setdefault("images", {})
    meta["images"][image_filename] = info

    # Unique keys per image
    key_base = f"{ficha}_{image_filename}".replace(".", "_").replace(" ", "_")

    with col:
        st.image(img_rel, use_container_width=True)
        st.caption(f"**Archivo:** {image_filename}")
        # Inputs
        disp_date = st.date_input("Fecha de imagen", value=date.fromisoformat(info["display_date"]), key=f"date_{key_base}")
        caption = st.text_input("Descripción (opcional)", value=info["caption"], key=f"cap_{key_base}")
        c1, c2 = st.columns([1,1])
        with c1:
            if st.button("💾 Guardar", key=f"save_{key_base}"):
                info["display_date"] = disp_date.isoformat()
                info["caption"] = caption
                meta["images"][image_filename] = info
                save_metadata(ficha, meta)
                st.success("Imagen actualizada.")
                set_query_params(page="detail", ficha=ficha)
        with c2:
            if st.button("🗑️ Borrar", key=f"del_{key_base}"):
                try:
                    os.remove(img_rel)
                except Exception:
                    pass
                # Remove from metadata
                meta["images"].pop(image_filename, None)
                save_metadata(ficha, meta)
                st.warning("Imagen eliminada.")
                set_query_params(page="detail", ficha=ficha)

# ---------------------------
# Main
# ---------------------------
def main():
    st.title("📋 Seguimiento de Mantenimientos por Ficha")
    st.caption("Verde: < 90 días desde el último mantenimiento · Rojo: ≥ 90 días o sin fecha")

    threshold = st.number_input("Umbral de días para estar 'al día'", min_value=1, max_value=365, value=90, step=1)

    q = get_query_params()
    page = (q.get("page", ["list"])[0] if isinstance(q.get("page"), list) else q.get("page", "list")) or "list"
    ficha_param = (q.get("ficha", [None])[0] if isinstance(q.get("ficha"), list) else q.get("ficha"))

    df = load_data(EXCEL_PATH)

    if page == "detail" and ficha_param:
        ficha = str(ficha_param)
        st.markdown('[← Volver a la lista](?page=list)')
        st.header(f"Ficha: {ficha}")

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

        # Editable maintenance record (app-side)
        meta = load_metadata(ficha)
        st.subheader("📝 Registro del último mantenimiento (editable)")
        default_date = None
        if isinstance(meta.get("fecha_ultima", None), str):
            try:
                default_date = date.fromisoformat(meta["fecha_ultima"])
            except Exception:
                default_date = None
        if default_date is None:
            default_date = date.today()

        with st.form(key="form_mant"):
            fecha_user = st.date_input("Fecha último mantenimiento (registro del app)", value=default_date, key="mant_date")
            notas = st.text_area("Notas / Detalles", value=meta.get("notas", ""), height=150, placeholder="Trabajo realizado, piezas cambiadas, etc.", key="mant_notes")
            saved = st.form_submit_button("💾 Guardar registro")
        if saved:
            meta["fecha_ultima"] = fecha_user.isoformat() if fecha_user else None
            meta["notas"] = notas
            meta["updated_at"] = datetime.now().isoformat(timespec="seconds")
            save_metadata(ficha, meta)
            st.success("Registro guardado.")
            set_query_params(page="detail", ficha=ficha)

        st.divider()
        st.subheader("🖼️ Evidencia (imágenes)")

        # Upload zone
        up_files = st.file_uploader("Agregar imágenes", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True)
        if st.button("⬆️ Subir imágenes"):
            save_dir = ficha_dir(ficha)
            added = 0
            for up in up_files or []:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                ext = os.path.splitext(up.name)[1].lower()
                fname = f"evidencia_{ts}{ext}"
                path = os.path.join(save_dir, fname)
                with open(path, "wb") as f:
                    f.write(up.getbuffer())
                # register metadata
                meta.setdefault("images", {})
                meta["images"][fname] = {
                    "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                    "display_date": date.today().isoformat(),
                    "caption": ""
                }
                added += 1
            if added:
                save_metadata(ficha, meta)
                st.success(f"{added} imagen(es) subida(s).")
                set_query_params(page="detail", ficha=ficha)

        # Gallery controls
        st.caption("Organiza y edita la fecha/nota de cada imagen. Usa 'Guardar' en cada tarjeta para persistir cambios.")
        sort_opt = st.selectbox("Ordenar por", ["Más reciente primero", "Más antiguo primero", "Nombre (A→Z)"], index=0)
        all_imgs = list_images(ficha)

        # Ensure all existing files are present in metadata (for first-time runs)
        for fn in all_imgs:
            meta.setdefault("images", {})
            if fn not in meta["images"]:
                meta["images"][fn] = {
                    "uploaded_at": datetime.fromtimestamp(os.path.getmtime(os.path.join(ficha_dir(ficha), fn))).isoformat(timespec="seconds"),
                    "display_date": date.today().isoformat(),
                    "caption": ""
                }
        # Remove metadata entries for images that no longer exist
        orphan_keys = [k for k in meta.get("images", {}).keys() if k not in set(all_imgs)]
        for k in orphan_keys:
            meta["images"].pop(k, None)
        if orphan_keys:
            save_metadata(ficha, meta)

        # Sort list
        def sort_key(fn):
            info = meta["images"].get(fn, {})
            dt = info.get("display_date") or (info.get("uploaded_at") or "")[:10]
            try:
                d = datetime.fromisoformat(dt if len(dt) > 10 else dt + " 00:00:00")
            except Exception:
                d = datetime.min
            return d, fn.lower()

        if sort_opt == "Más reciente primero":
            all_imgs = sorted(all_imgs, key=sort_key, reverse=True)
        elif sort_opt == "Más antiguo primero":
            all_imgs = sorted(all_imgs, key=sort_key, reverse=False)
        else:
            all_imgs = sorted(all_imgs, key=lambda x: x.lower())

        # Render gallery in a 3-column grid
        cols = st.columns(3)
        for i, fn in enumerate(all_imgs):
            image_card(ficha, fn, meta, cols[i % 3])

    else:
        st.subheader("Listado de fichas")
        df_status = compute_status(df, threshold)
        table = df_status[["Ficha", "Modelo", "Location", "Fecha Ultiimo Mantenimiento", "Fecha_parsed", "Días desde último mant.", "Estado"]].copy()
        table = table.rename(columns={
            "Fecha Ultiimo Mantenimiento": "Fecha (Excel)",
            "Fecha_parsed": "Fecha parseada"
        })
        table = table.sort_values(by=["Estado", "Días desde último mant.", "Ficha"], ascending=[True, False, True])

        styled = style_status(table)
        st.dataframe(styled, use_container_width=True)

        st.divider()
        st.subheader("Abrir ficha")
        st.caption("Haz clic en una ficha para ver/editar su registro, subir fotos y ver la galería.")

        cols = st.columns(4)
        for i, ficha in enumerate(table["Ficha"]):
            url = f"?page=detail&ficha={ficha}"
            with cols[i % 4]:
                st.markdown(f"[🗂️ {ficha}]({url})")

if __name__ == "__main__":
    main()
