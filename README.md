
# Mantenimiento App (Streamlit)

This is a simple Streamlit app to track maintenance status by **Ficha** from your Excel file.

## How it works
- Reads `Mantenimiento TA(5).xlsx` located in the same folder.
- Colors each ficha:
  - **Green** if the Excel "Fecha Último Mantenimiento" is < 90 days ago.
  - **Red** if it's ≥ 90 days ago or missing.
- Click any ficha to open its detail page where you can:
  - Enter last maintenance date (app record),
  - Add notes,
  - Upload and view images.
- All per-ficha data saves under `./data/<Ficha>/metadata.json` and image files in the same folder.

## Run locally
```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open the URL Streamlit prints (usually `http://localhost:8501`).

## Adjust threshold
Use the number control at the top of the list view to change the "on time" threshold (default 90 days).
