import sqlite3
import json
import os
import tempfile
import requests
import streamlit as st
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

# Wrap sqlite3.connect to redirect database path to DB_DIR and set a 30-second timeout to prevent database locks
DB_DIR = os.environ.get("DB_DIR", ".")
if DB_DIR != "." and not os.path.exists(DB_DIR):
    try:
        os.makedirs(DB_DIR, exist_ok=True)
    except Exception:
        pass

_original_connect = sqlite3.connect
def secure_connect(database, *args, **kwargs):
    if database == 'store.db' or database == './store.db':
        database = os.path.join(DB_DIR, 'store.db')
    if 'timeout' not in kwargs:
        kwargs['timeout'] = 30.0
    return _original_connect(database, *args, **kwargs)
sqlite3.connect = secure_connect

st.set_page_config(page_title="The Green Oasis - Admin", layout="wide")

SAMPLE_CATALOG = {
    "Bangalore": [
        {"name": "Monstera Deliciosa", "price": 1200},
        {"name": "Snake Plant (Laurentii)", "price": 450},
    ],
    "Mangalore": [
        {"name": "Fiddle Leaf Fig", "price": 1500},
        {"name": "Peace Lily", "price": 350},
    ],
}


@st.cache_data
def load_orders():
    try:
        conn = sqlite3.connect("store.db")
        df = pd.read_sql_query("SELECT id, user_id, username, city, product, utr_no, address, status FROM orders ORDER BY id DESC", conn)
        conn.close()
        return df
    except Exception:
        # fallback sample data
        return pd.DataFrame([
            {"id": 1, "user_id": 0, "username": "alice", "city": "Bangalore", "product": "Monstera Deliciosa", "utr_no": "UTR123", "address": "123 Green St", "status": "DELIVERED"},
            {"id": 2, "user_id": 0, "username": "bob", "city": "Mangalore", "product": "Peace Lily", "utr_no": "UTR456", "address": "45 Ocean Ave", "status": "PENDING"},
        ])


def load_catalog_from_db():
    conn = sqlite3.connect("store.db")
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT,
        name TEXT,
        price INTEGER,
        gardener_id INTEGER,
        quantity INTEGER DEFAULT 1
    )''')
    conn.commit()
    # Add columns if not exist
    for col in ["gardener_id INTEGER", "quantity INTEGER DEFAULT 1"]:
        try: cur.execute(f"ALTER TABLE catalog ADD COLUMN {col}")
        except Exception: pass
    conn.commit()
    
    cur.execute("SELECT id, city, name, price, quantity FROM catalog ORDER BY id")
    rows = cur.fetchall()
    if not rows:
        # populate with sample data
        for city, items in SAMPLE_CATALOG.items():
            for item in items:
                cur.execute("INSERT INTO catalog (city, name, price, quantity) VALUES (?, ?, ?, 1)", (city, item["name"], item["price"]))
        conn.commit()
        cur.execute("SELECT id, city, name, price, quantity FROM catalog ORDER BY id")
        rows = cur.fetchall()
    # also ensure we include any cities added via the `locations` table
    try:
        cur.execute('''CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )''')
        conn.commit()
        cur.execute("SELECT name FROM locations ORDER BY name")
        loc_rows = cur.fetchall()
        locs = [r[0] for r in loc_rows]
    except Exception:
        locs = []

    conn.close()

    catalog = {}
    for _id, city, name, price, quantity in rows:
        qty = quantity if quantity is not None else 1
        catalog.setdefault(city, []).append({"id": _id, "name": name, "price": price, "quantity": qty})

    # ensure locations exist as keys even if no products yet
    for l in locs:
        if l not in catalog:
            catalog[l] = []

    return catalog


def persist_catalog_to_db(updates):
    conn = sqlite3.connect("store.db")
    cur = conn.cursor()
    for item_id, fields in updates.items():
        cur.execute(
            "UPDATE catalog SET name = ?, price = ?, quantity = ? WHERE id = ?",
            (fields["name"], int(fields["price"]), int(fields["quantity"]), int(item_id))
        )
    conn.commit()
    conn.close()


def ensure_session_catalog():
    if "catalog" not in st.session_state:
        st.session_state.catalog = load_catalog_from_db()


def slug(s: str) -> str:
    return s.replace(" ", "_").replace("/", "_")


def main():
    ensure_session_catalog()

    st.title("🌿 The Green Oasis — Admin Panel")

    df = load_orders()
    total = len(df)
    delivered = (df["status"] == "DELIVERED").sum() if "status" in df else 0
    pending = total - delivered

    col1, col2, col3 = st.columns(3)
    col1.metric("Total Orders", total)
    col2.metric("Delivered", delivered)
    col3.metric("Pending", pending)

    st.subheader("Orders")

    # helper: extract BOT_TOKEN from bot.py (avoid importing bot.py which starts the bot)
    def extract_bot_token():
        try:
            content = open("bot.py").read()
            for line in content.splitlines():
                if line.strip().startswith("BOT_TOKEN") and "=" in line:
                    # naive parse
                    parts = line.split("=", 1)[1].strip()
                    if parts.startswith('"') or parts.startswith("'"):
                        return parts.strip().strip('"').strip("'")
        except Exception:
            return None
        return None

    BOT_TOKEN = os.environ.get("BOT_TOKEN") or extract_bot_token()

    # show interactive order list with actions
    if df.empty:
        st.info("No orders yet.")
    else:
        for row in df.itertuples(index=False):
            oid = row.id
            user_id = int(row.user_id) if getattr(row, 'user_id', None) is not None else None
            username = row.username
            city = row.city
            product = row.product
            utr = row.utr_no
            address = getattr(row, 'address', '')
            status = row.status

            with st.expander(f"Order #{oid} — {product} — @{username} [{status}]"):
                rcol1, rcol2 = st.columns([3, 1])
                with rcol1:
                    st.markdown(f"**Customer:** @{username}")
                    st.markdown(f"**City:** {city}")
                    st.markdown(f"**Product:** {product}")
                    st.markdown(f"**UTR / Ref:** {utr}")
                    st.markdown(f"**Address:** {address}")
                    st.markdown(f"**Status:** {status}")
                with rcol2:
                    if st.button("Mark Delivered", key=f"deliver_{oid}"):
                        try:
                            conn = sqlite3.connect("store.db")
                            cur = conn.cursor()
                            cur.execute("UPDATE orders SET status = 'DELIVERED' WHERE id = ?", (oid,))
                            conn.commit()
                            conn.close()
                            # Notify user via HTTP POST
                            if BOT_TOKEN and user_id:
                                try:
                                    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                                    payload = {
                                        "chat_id": user_id,
                                        "text": f"📦 *Order Status Update*\n\nYour order *#{oid}* for *{product}* has been marked as *DELIVERED*! Thank you.",
                                        "parse_mode": "Markdown"
                                    }
                                    requests.post(url, json=payload, timeout=5)
                                except Exception:
                                    pass
                            st.success(f"Order #{oid} marked DELIVERED")
                            st.experimental_rerun()
                        except Exception as e:
                            st.error(f"Failed to update order: {e}")

                # Photo upload and send
                file_key = f"proof_{oid}"
                uploaded = st.file_uploader("Upload delivery proof photo", type=["png", "jpg", "jpeg"], key=file_key)
                if uploaded is not None:
                    st.image(uploaded)
                    if st.button("Upload & Send Proof to User", key=f"sendproof_{oid}"):
                        if not BOT_TOKEN:
                            st.error("Bot token not available (set BOT_TOKEN env var or add it to bot.py)")
                        elif not user_id:
                            st.error("User id not available for this order; cannot send photo.")
                        else:
                            try:
                                tf = tempfile.NamedTemporaryFile(delete=False)
                                tf.write(uploaded.getbuffer())
                                tf.flush()
                                tf.close()
                                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                                with open(tf.name, "rb") as f:
                                    files = {"photo": f}
                                    data = {"chat_id": user_id, "caption": f"🌿 Proof of delivery for Order #{oid} - {product}", "parse_mode": "Markdown"}
                                    resp = requests.post(url, files=files, data=data, timeout=20)
                                if resp.ok:
                                    # mark delivered
                                    conn = sqlite3.connect("store.db")
                                    cur = conn.cursor()
                                    cur.execute("UPDATE orders SET status = 'DELIVERED' WHERE id = ?", (oid,))
                                    conn.commit()
                                    conn.close()
                                    st.success("Proof sent and order marked DELIVERED")
                                    st.experimental_rerun()
                                else:
                                    st.error(f"Failed to send photo: {resp.status_code} {resp.text}")
                            except Exception as e:
                                st.error(f"Error sending photo: {e}")

    st.subheader("Catalog")
    catalog = st.session_state.catalog

    # Add location
    with st.expander("Add Location"):
        new_loc = st.text_input("New location name", key="new_location")
        if st.button("Add Location"):
            if new_loc:
                if new_loc not in catalog:
                    # persist to locations table so bot can read it
                    try:
                        conn = sqlite3.connect('store.db')
                        cur = conn.cursor()
                        cur.execute('''CREATE TABLE IF NOT EXISTS locations (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT UNIQUE
                        )''')
                        conn.commit()
                        cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (new_loc,))
                        conn.commit()
                        conn.close()
                    except Exception as e:
                        st.error(f"Failed to persist location: {e}")
                    # reload catalog from DB to keep canonical state
                    st.session_state.catalog = load_catalog_from_db()
                    st.success(f"Added location {new_loc}")
                else:
                    st.warning("Location already exists")

    # Add product
    with st.expander("Add Product"):
        locations = list(catalog.keys())
        if not locations:
            st.info("No locations available. Add a location first.")
        else:
            sel_loc = st.selectbox("Choose location", locations, key="add_prod_loc")
            p_name = st.text_input("Product name", key="add_prod_name")
            p_qty = st.number_input("Quantity", min_value=1, value=1, step=1, key="add_prod_qty")
            p_price = st.number_input("Price", min_value=0, value=100, step=1, key="add_prod_price")
            if st.button("Add Product"):
                # insert into DB
                conn = sqlite3.connect("store.db")
                cur = conn.cursor()
                cur.execute("INSERT INTO catalog (city, name, price, quantity) VALUES (?, ?, ?, ?)", (sel_loc, p_name, int(p_price), int(p_qty)))
                conn.commit()
                conn.close()
                # reload session catalog
                # ensure location exists in locations table as well
                try:
                    conn2 = sqlite3.connect('store.db')
                    cur2 = conn2.cursor()
                    cur2.execute('''CREATE TABLE IF NOT EXISTS locations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE
                    )''')
                    cur2.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (sel_loc,))
                    conn2.commit()
                    conn2.close()
                except Exception:
                    pass
                st.session_state.catalog = load_catalog_from_db()
                st.success("Product added")

    cols = st.columns([1, 3, 1, 1, 1])
    cols[0].write("Branch")
    cols[1].write("Product")
    cols[2].write("Quantity")
    cols[3].write("Price")
    cols[4].write("")

    # show products with editable fields
    for city, items in list(catalog.items()):
        st.markdown(f"**📍 {city}**")
        for item in items:
            _id = item.get("id")
            c0, c1, c2, c3, c4 = st.columns([0.5, 3, 1, 1, 1])
            name_key = f"name_{slug(city)}_{_id}"
            qty_key = f"qty_{slug(city)}_{_id}"
            price_key = f"price_{slug(city)}_{_id}"
            with c1:
                st.text_input("", value=item.get("name"), key=name_key, label_visibility="collapsed")
            with c2:
                st.number_input("", min_value=1, value=int(item.get("quantity", 1)), key=qty_key, step=1, label_visibility="collapsed")
            with c3:
                st.number_input("", min_value=0, value=int(item.get("price")), key=price_key, label_visibility="collapsed")
            with c4:
                if st.button("Delete", key=f"del_{_id}"):
                    try:
                        conn = sqlite3.connect("store.db")
                        cur = conn.cursor()
                        cur.execute("DELETE FROM catalog WHERE id = ?", (_id,))
                        conn.commit()
                        conn.close()
                        st.session_state.catalog = load_catalog_from_db()
                        st.experimental_rerun()
                    except Exception as e:
                        st.error(f"Failed to delete: {e}")

    st.markdown("---")

    if st.button("Save Catalog Changes"):
        # persist edits by reading current input widget values
        updated = {}
        for city, items in st.session_state.catalog.items():
            for item in items:
                _id = item.get("id")
                name_key = f"name_{slug(city)}_{_id}"
                qty_key = f"qty_{slug(city)}_{_id}"
                price_key = f"price_{slug(city)}_{_id}"
                new_name = st.session_state.get(name_key, item.get("name"))
                try:
                    new_qty = int(st.session_state.get(qty_key, item.get("quantity", 1)))
                except Exception:
                    new_qty = item.get("quantity", 1)
                try:
                    new_price = int(st.session_state.get(price_key, item.get("price")))
                except Exception:
                    new_price = item.get("price")
                updated[_id] = {"name": new_name, "price": new_price, "quantity": new_qty}
        try:
            persist_catalog_to_db(updated)
            # ensure locations table contains all cities
            try:
                conn3 = sqlite3.connect('store.db')
                cur3 = conn3.cursor()
                cur3.execute('''CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE
                )''')
                for c in st.session_state.catalog.keys():
                    cur3.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (c,))
                conn3.commit()
                conn3.close()
            except Exception:
                pass
            st.success("Catalog saved to store.db (bot will pick up changes).")
            st.session_state.catalog = load_catalog_from_db()
        except Exception as e:
            st.error(f"Failed to persist catalog: {e}")

    st.sidebar.header("Actions")
    if st.sidebar.button("Refresh Orders"):
        load_orders.clear()
        st.experimental_rerun()


if __name__ == "__main__":
    main()
