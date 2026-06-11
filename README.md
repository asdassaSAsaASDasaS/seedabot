# The Green Oasis — Streamlit Admin

Quick local dashboard using Streamlit. Shows order stats and a simple editable catalog (saved to session state).

Requirements
- Python 3.8+
- See `requirements.txt`

Run
```bash
source .venv/bin/activate
pip install -r requirements.txt
streamlit run streamlit_app.py
```

Notes
- Orders are read from `store.db` if present; otherwise sample orders are shown.
- Catalog edits are stored in the Streamlit session. Persisting to the DB can be added if needed.
