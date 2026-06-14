import os
import sqlite3
import logging
import urllib.parse
import threading
import asyncio
import ssl
import aiosqlite
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
    TypeHandler,
    ApplicationHandlerStop
)
import flet as ft
import requests

# global application object (will be assigned in __main__)
app = None

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

# Helper to check if user is blocked
def is_blocked(user_id):
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,))
        blocked = cur.fetchone() is not None
        conn.close()
        return blocked
    except Exception:
        return False

# Global handler to reject all actions from blocked users immediately
async def block_check_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if user and is_blocked(user.id):
        if update.callback_query:
            try:
                await update.callback_query.answer("🚫 You are currently blocked from using this bot.", show_alert=True)
            except Exception:
                pass
        elif update.message:
            try:
                await update.message.reply_text("🚫 You are currently blocked from using this bot.")
            except Exception:
                pass
        raise ApplicationHandlerStop()

# --- MAC OS SSL FIX ---
ssl._create_default_https_context = ssl._create_unverified_context

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable not set in .env file or environment!")

try:
    ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID"))
except Exception:
    raise ValueError("ADMIN_CHAT_ID environment variable not set or invalid in .env file or environment!")

UPI_ID = os.environ.get("UPI_ID", "7259398790-4@ybl")
MERCHANT_NAME = "The Green Oasis" 

# --- STAGES FOR CONVERSATION HANDLERS ---
SELECT_CITY, SELECT_PRODUCT, SELECT_DELIVERY_METHOD, PROCESS_PAYMENT, GET_ADDRESS, ADMIN_DASHBOARD = range(6)
ADMIN_AWAITING_PHOTO = range(1)
# major states list for initial selection
MAJOR_STATES = [
    "Karnataka", "Maharashtra", "Tamil Nadu", "Kerala", "Telangana",
    "Delhi", "Gujarat", "Rajasthan", "West Bengal", "Andhra Pradesh"
]

# common major cities per state (used to show quick city choices)
MAJOR_STATE_CITIES = {
    "Karnataka": ["Bangalore", "Mysore", "Mangalore", "Hubli"],
    "Maharashtra": ["Mumbai", "Pune", "Nagpur", "Nashik"],
    "Tamil Nadu": ["Chennai", "Coimbatore", "Madurai", "Tiruchirappalli"],
    "Kerala": ["Kochi", "Thiruvananthapuram", "Kozhikode", "Kollam"],
    "Telangana": ["Hyderabad", "Warangal", "Nizamabad"],
    "Delhi": ["New Delhi", "Delhi"],
    "Gujarat": ["Ahmedabad", "Surat", "Vadodara"],
    "Rajasthan": ["Jaipur", "Jodhpur", "Udaipur"],
    "West Bengal": ["Kolkata", "Howrah", "Durgapur"],
    "Andhra Pradesh": ["Visakhapatnam", "Vijayawada", "Guntur"]
}

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    # 1. Orders table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            city TEXT,
            product TEXT,
            price INTEGER,
            utr_no TEXT UNIQUE,
            address TEXT,
            status TEXT DEFAULT 'PENDING',
            lat REAL,
            lon REAL,
            location_requested INTEGER DEFAULT 0,
            admin_photo_file_id TEXT,
            awaiting_admin_location INTEGER DEFAULT 0,
            admin_lat REAL,
            admin_lon REAL,
            gardener_id INTEGER,
            rating INTEGER,
            confirmed_by_user INTEGER DEFAULT 0,
            expires_at TEXT
        )
    ''')
    # Add columns if table existed (migration)
    for col in ["price INTEGER", "utr_no TEXT UNIQUE", "lat REAL", "lon REAL", "location_requested INTEGER DEFAULT 0",
                "admin_photo_file_id TEXT", "awaiting_admin_location INTEGER DEFAULT 0", "admin_lat REAL", "admin_lon REAL",
                "gardener_id INTEGER", "rating INTEGER", "confirmed_by_user INTEGER DEFAULT 0", "expires_at TEXT", "review TEXT"]:
        try: cursor.execute(f"ALTER TABLE orders ADD COLUMN {col}")
        except Exception: pass

    # 2. Catalog table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            name TEXT,
            price INTEGER,
            file_id TEXT,
            gardener_id INTEGER,
            quantity INTEGER DEFAULT 1
        )
    ''')
    for col in ["file_id TEXT", "gardener_id INTEGER", "quantity INTEGER DEFAULT 1"]:
        try: cursor.execute(f"ALTER TABLE catalog ADD COLUMN {col}")
        except Exception: pass

    # 3. Gardeners table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gardeners (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            phone TEXT,
            items TEXT,
            location TEXT,
            verified INTEGER DEFAULT 0,
            upi_id TEXT,
            total_sales INTEGER DEFAULT 0,
            avg_rating REAL DEFAULT 0,
            referral_code TEXT UNIQUE
        )
    ''')
    for col in ["upi_id TEXT", "total_sales INTEGER DEFAULT 0", "avg_rating REAL DEFAULT 0", "referral_code TEXT UNIQUE"]:
        try: cursor.execute(f"ALTER TABLE gardeners ADD COLUMN {col}")
        except Exception: pass

    # 4. Users table (for referrals/points)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            referred_by INTEGER,
            referral_count INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0,
            agreed_disclaimer INTEGER DEFAULT 0
        )
    ''')
    for col in ["referred_by INTEGER", "referral_count INTEGER DEFAULT 0", "points INTEGER DEFAULT 0", "agreed_disclaimer INTEGER DEFAULT 0"]:
        try: cursor.execute(f"ALTER TABLE users ADD COLUMN {col}")
        except Exception: pass

    # 5. States and Cities (Dynamic Locations)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_id INTEGER,
            name TEXT,
            UNIQUE(state_id, name),
            FOREIGN KEY(state_id) REFERENCES states(id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    ''')

    # 6. Blocked Users
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS blocked_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            reason TEXT
        )
    ''')
    conn.commit()

    # 7. Support tickets table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            admin_message_id INTEGER,
            user_message_id INTEGER,
            status TEXT DEFAULT 'OPEN',
            created_at TEXT
        )
    ''')
    conn.commit()

    # SEEDING: Populate from constants if empty
    cursor.execute("SELECT COUNT(1) FROM states")
    if cursor.fetchone()[0] == 0:
        for st, c_list in MAJOR_STATE_CITIES.items():
            cursor.execute("INSERT OR IGNORE INTO states (name) VALUES (?)", (st,))
            conn.commit()
            cursor.execute("SELECT id FROM states WHERE name = ?", (st,))
            sid = cursor.fetchone()[0]
            for c in c_list:
                cursor.execute("INSERT OR IGNORE INTO cities (state_id, name) VALUES (?, ?)", (sid, c))
                cursor.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (c,))
        conn.commit()
    conn.close()

# --- LOCAL CATALOG ---
def load_catalog_from_db():
    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id, city, name, price, gardener_id, quantity FROM catalog ORDER BY id")
        rows = cursor.fetchall()
    except Exception:
        try:
            cursor.execute("SELECT id, city, name, price, gardener_id FROM catalog ORDER BY id")
            rows = [r + (1,) for r in cursor.fetchall()]
        except Exception:
            cursor.execute("SELECT id, city, name, price FROM catalog ORDER BY id")
            rows = [(r[0], r[1], r[2], r[3], None, 1) for r in cursor.fetchall()]
    conn.close()

    catalog = {}
    for _id, city, name, price, gardener_id, quantity in rows:
        qty = quantity if quantity is not None else 1
        catalog.setdefault(city, []).append({"id": _id, "name": name, "price": price, "gardener_id": gardener_id, "quantity": qty})

    # fallback to hard-coded sample if DB empty
    if not catalog:
        catalog = {
            "Bangalore": [
                {"name": "Monstera Deliciosa", "price": 1200, "quantity": 1},
                {"name": "Snake Plant (Laurentii)", "price": 450, "quantity": 1},
            ],
            "Mangalore": [
                {"name": "Fiddle Leaf Fig", "price": 1500, "quantity": 1},
                {"name": "Peace Lily", "price": 350, "quantity": 1},
            ]
        }

    return catalog


async def send_user_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, sender: str = "Bot", alias: str = None):
    prefix = f"From {sender}"
    if sender.lower() == 'gardener' and alias:
        prefix = f"From Gardener ({alias})"
    try:
        await context.bot.send_message(chat_id=chat_id, text=f"{prefix}:\n\n{text}")
    except Exception:
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception:
            pass


async def send_user_photo(context: ContextTypes.DEFAULT_TYPE, chat_id: int, photo, caption: str = None, sender: str = "Bot", alias: str = None):
    prefix = f"From {sender}"
    if sender.lower() == 'gardener' and alias:
        prefix = f"From Gardener ({alias})"
    final_caption = f"{prefix}:\n\n{caption}" if caption else prefix
    try:
        await context.bot.send_photo(chat_id=chat_id, photo=photo, caption=final_caption, parse_mode="Markdown")
    except Exception:
        try:
            await context.bot.send_message(chat_id=chat_id, text=final_caption)
        except Exception:
            pass

# --- USER TELEGRAM FLOW ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    if is_blocked(user_id):
        await update.message.reply_text("🚫 You are currently blocked from using this bot.")
        return ConversationHandler.END

    init_db()

    # Clear user_data but preserve referral info if present
    ref_id = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0].split("_")[1])
            if referrer_id != user_id:
                ref_id = referrer_id
        except Exception:
            pass

    try:
        context.user_data.clear()
    except Exception:
        pass

    if ref_id:
        context.user_data['referred_by'] = ref_id

    # Check if user has already agreed to the disclaimer
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    cur.execute("SELECT agreed_disclaimer FROM users WHERE user_id = ? LIMIT 1", (user_id,))
    row = cur.fetchone()
    agreed = row[0] if row else 0
    conn.close()

    if not agreed:
        # Show disclaimer
        keyboard = [
            [
                InlineKeyboardButton("✅ Agree", callback_data="disclaimer_agree"),
                InlineKeyboardButton("❌ Reject", callback_data="disclaimer_reject")
            ]
        ]
        disclaimer_text = (
            "⚠️ *Disclaimer & Terms of Use*\n\n"
            "Please read and agree to the following terms to proceed:\n\n"
            "The admin(s) of this bot have **no liability or responsibility** towards how this bot is used, "
            "what products are listed, sourced or sold, or any scams/transactions that occur. "
            "By using this bot, you agree that you do so entirely at your own risk and are solely responsible "
            "for any transactions or interactions.\n\n"
            "Do you agree to these terms?"
        )
        await update.message.reply_text(disclaimer_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        return ConversationHandler.END

    # User already agreed; proceed to menu
    return await view_menu_callback(update, context)


async def disclaimer_agree_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    # Save agreement in DB
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("UPDATE users SET agreed_disclaimer = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    
    # Handle referral if saved in context
    referrer_id = context.user_data.get('referred_by')
    if referrer_id:
        try:
            # Check if this user is new (has no referred_by already recorded)
            cur.execute("SELECT referred_by FROM users WHERE user_id = ?", (user_id,))
            row = cur.fetchone()
            if row and row[0] is None:
                cur.execute("UPDATE users SET referred_by = ? WHERE user_id = ?", (referrer_id, user_id))
                cur.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
                conn.commit()
                # Notify referrer
                try:
                    await context.bot.send_message(chat_id=referrer_id, text="🎊 Someone just joined using your referral link! You'll earn points when they make a purchase.")
                except Exception: pass
        except Exception: pass
        context.user_data.pop('referred_by', None)
    
    conn.close()
    
    # Send menu
    return await view_menu_callback(update, context)


async def disclaimer_reject_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("❌ *Terms Rejected.*\n\nYou have rejected the terms and cannot use this bot. If you change your mind, send /start.", parse_mode="Markdown")
    return ConversationHandler.END


async def admin_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID: return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("📜 View Orders", callback_data='admin_view_orders')],
        [InlineKeyboardButton("📦 View Inventory", callback_data='admin_view_catalog')],
        [InlineKeyboardButton("🏙 Manage Cities", callback_data='admin_manage_locations')],
        [InlineKeyboardButton("✏️ Edit Products", callback_data='admin_edit_products')],
        [InlineKeyboardButton("👨‍🌾 Manage Gardeners", callback_data='admin_gardeners')],
        [InlineKeyboardButton("📢 Broadcast to Users", callback_data='admin_broadcast_users')],
        [InlineKeyboardButton("👨‍🌾 Broadcast to Gardeners", callback_data='admin_broadcast_gardeners')],
        [InlineKeyboardButton("🚫 Manage/Block Users", callback_data='admin_manage_blocked')],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data='view_menu')]
    ]
    await query.message.edit_text("🔧 *Admin Dashboard*\nSelect an action to perform:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return ADMIN_DASHBOARD


async def admin_broadcast_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID: return
    
    target = "users" if "users" in query.data else "gardeners"
    context.user_data['awaiting_admin_broadcast'] = target
    await query.message.reply_text(f"📣 *Broadcast to {target.capitalize()}*\nEnter the message you want to send to all {target}:", parse_mode="Markdown")


async def admin_manage_blocked_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID: return
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    # Unique users who have ordered
    cur.execute("SELECT DISTINCT user_id, username FROM orders")
    users_data = {row[0]: row[1] or f"User {row[0]}" for row in cur.fetchall()}
    # Plus all gardeners
    cur.execute("SELECT user_id, username FROM gardeners")
    for r in cur.fetchall():
        users_data[r[0]] = r[1] or f"Gardener {r[0]}"
    # Current blocked
    cur.execute("SELECT user_id FROM blocked_users")
    blocked_ids = [r[0] for r in cur.fetchall()]
    conn.close()
    
    text = "🚫 *Manage User Blocks*\nToggle user access below:"
    keyboard = []
    # Limit to most recent 15 for safety
    for uid, name in list(users_data.items())[:15]:
        is_b = uid in blocked_ids
        btn_text = f"{'🚫 Block' if not is_b else '✅ Unblock'} {name}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"admin_toggle_block_{uid}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_dashboard")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def admin_toggle_block_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID: return
    
    uid = int(query.data.split("_")[-1])
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blocked_users WHERE user_id = ?", (uid,))
    if cur.fetchone():
        cur.execute("DELETE FROM blocked_users WHERE user_id = ?", (uid,))
        status = "unblocked"
    else:
        cur.execute("INSERT INTO blocked_users (user_id) VALUES (?)", (uid,))
        status = "blocked"
    conn.commit()
    conn.close()
    
    await query.message.reply_text(f"User {uid} is now {status}.")
    await admin_manage_blocked_callback(update, context)


async def admin_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID: return
    
    target = context.user_data.get('awaiting_admin_broadcast')
    if not target:
        return
    
    msg = update.message.text
    logging.info(f"admin_text_router: Broadcasting message: {msg[:20]}...")
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    if target == "users": cur.execute("SELECT DISTINCT user_id FROM orders")
    else: cur.execute("SELECT user_id FROM gardeners")
    uids = [r[0] for r in cur.fetchall()]
    conn.close()
    
    logging.info(f"admin_text_router: Found {len(uids)} recipients for {target}")
    sent = 0
    for uid in set(uids):
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 *Broadcast Message*:\n\n{msg}", parse_mode="Markdown")
            sent += 1
        except Exception as e:
            logging.error(f"admin_text_router: Failed to send to {uid}: {e}")
    
    await update.message.reply_text(f"✅ Broadcast sent to {sent} {target}.")
    context.user_data.pop('awaiting_admin_broadcast', None)


async def update_order_status_and_notify(order_id: int, new_status: str, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Updates order status in database and notifies the customer via Telegram."""
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("SELECT user_id, product, price FROM orders WHERE id = ?", (order_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            logging.error(f"update_order_status_and_notify: Order #{order_id} not found.")
            return False
            
        user_id, product, price = row
        cur.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
        conn.commit()
        conn.close()
        
        status_descriptions = {
            'DELIVERED': "✅ *Delivered* (marked completed)",
            'CANCELLED': "❌ *Cancelled*",
            'PENDING': "⏳ *Pending*",
            'SHIPPED': "📦 *Shipped/On the way*"
        }
        status_label = status_descriptions.get(new_status, f"*{new_status}*")
        
        notification_text = (
            f"📦 *Order Status Update*\n\n"
            f"Your order *#{order_id}* for *{product}* (₹{price}) status has been updated to: {status_label}.\n\n"
            f"Use the menu to track your order details."
        )
        try:
            await context.bot.send_message(chat_id=user_id, text=notification_text, parse_mode="Markdown")
        except Exception as telegram_err:
            logging.error(f"Failed to send order status notification to user {user_id}: {telegram_err}")
            
        return True
    except Exception as e:
        logging.error(f"Error in update_order_status_and_notify: {e}")
        return False


async def admin_view_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id, username, city, product, utr_no, address, status FROM orders ORDER BY id DESC LIMIT 20")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="No orders found.")
        return
        
    text = "📜 *Recent Orders (Telegram Panel)*\n\n"
    buttons = []
    for r in rows:
        oid, usern, city, product, utr, addr, status = r
        status_emoji = "⏳" if status == "PENDING" else "✅" if status == "DELIVERED" else "📦" if status == "SHIPPED" else "❌"
        text += f"#{oid}: *{product}* • @{usern} • {city} • {status_emoji} {status}\n"
        buttons.append([InlineKeyboardButton(f"⚙️ Manage #{oid}", callback_data=f"admin_manage_order_{oid}")])
        
    buttons.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="admin_dashboard")])
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def admin_revenue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT price, city, product FROM orders WHERE status != 'CANCELLED'")
    orders = cur.fetchall()
    total = 0
    missing = 0
    for price, city, product in orders:
        if price is not None:
            total += price
        else:
            cur.execute("SELECT price FROM catalog WHERE city = ? AND name = ? LIMIT 1", (city, product))
            res = cur.fetchone()
            if res:
                total += int(res[0])
            else:
                missing += 1
    conn.close()
    msg = f"Total expected revenue: ₹{total}"
    if missing:
        msg += f"\n(Prices not found for {missing} order(s) — add catalog entries to compute exact total)"
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)


async def admin_add_location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    msg = (
        "To add a new city/location, use the command:\n"
        "/location_add CityName\n\nExample:\n/location_add Bangalore"
    )
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg)


async def admin_gardeners_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, username, phone, items, location, verified FROM gardeners ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    if not rows:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="No gardeners registered yet.")
        return
    parts = []
    buttons = []
    for r in rows:
        gid, uid, uname, phone, items, loc, verified = r
        status = "VERIFIED" if verified else "NOT VERIFIED"
        parts.append(f"#{gid} • @{uname or '-'} • {phone or '-'} • {loc or '-'} • {status}")
        if not verified:
            buttons.append([InlineKeyboardButton("Approve", callback_data=f"gardener_approve_{gid}")])
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="\n".join(parts[:50]))
    if buttons:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Approve gardeners below:", reply_markup=InlineKeyboardMarkup(buttons))
async def admin_manage_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    order_id = int(query.data.split("_")[-1])
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, username, city, product, price, utr_no, address, status FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        await query.message.reply_text("Order not found.")
        return
        
    oid, uid, username, city, product, price, utr, address, status = row
    
    text = (
        f"⚙️ *Order Management: Order #{oid}*\n\n"
        f"👤 *Customer:* @{username} (ID: `{uid}`)\n"
        f"📍 *City:* {city}\n"
        f"🌿 *Product:* {product}\n"
        f"💰 *Price:* ₹{price}\n"
        f"🔢 *UTR:* `{utr}`\n"
        f"🏠 *Delivery Address:* {address}\n"
        f"📊 *Current Status:* *{status}*\n\n"
        f"📸 To upload delivery proof photo for this order, click to copy the command below and send it to the chat:\n"
        f"`/deliver {oid}`"
    )
    
    buttons = []
    if status == 'PENDING':
        buttons.append([InlineKeyboardButton("❌ Cancel Order", callback_data=f"admin_cancel_order_{oid}")])
    elif status == 'DELIVERED':
        buttons.append([InlineKeyboardButton("❌ Cancel Order", callback_data=f"admin_cancel_order_{oid}")])
        
    buttons.append([InlineKeyboardButton("🔙 Back to Orders List", callback_data="admin_view_orders")])
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def admin_cancel_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    order_id = int(query.data.split("_")[-1])
    
    success = await update_order_status_and_notify(order_id, 'CANCELLED', context)
    if success:
        await query.message.reply_text(f"✅ Order #{order_id} has been CANCELLED and customer notified.")
    else:
        await query.message.reply_text(f"⚠️ Failed to cancel Order #{order_id}.")
        
    await admin_view_orders_callback(update, context)


async def admin_view_catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT,
        name TEXT,
        price INTEGER,
        file_id TEXT,
        gardener_id INTEGER,
        quantity INTEGER DEFAULT 1
    )''')
    conn.commit()
    
    cur.execute("""
        SELECT c.id, c.city, c.name, c.price, c.quantity, g.phone 
        FROM catalog c 
        LEFT JOIN gardeners g ON c.gardener_id = g.id 
        ORDER BY c.city, c.name
    """)
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="📦 Catalog/Inventory is empty.")
        return
        
    by_city = {}
    for cid, city, name, price, qty, g_alias in rows:
        by_city.setdefault(city, []).append((cid, name, price, qty, g_alias))
        
    text = "📦 *Current Inventory by City*\n\n"
    for city, items in by_city.items():
        text += f"📍 *{city}*:\n"
        for cid, name, price, qty, g_alias in items:
            gardener_info = f" (Gardener: {g_alias})" if g_alias else ""
            text += f"  • #{cid}: {name} - ₹{price} (Qty: {qty}){gardener_info}\n"
        text += "\n"
        
    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=chunk, parse_mode="Markdown")
    else:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, parse_mode="Markdown")


async def admin_manage_locations_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS locations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE
    )''')
    conn.commit()
    cur.execute("SELECT id, name FROM locations ORDER BY name")
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        msg = "🏙 *No cities registered.*\n\nUse `/location_add CityName` to add one."
        buttons = [[InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_dashboard")]]
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return
        
    text = "🏙 *Current Cities/Locations:*\n\n"
    buttons = []
    for lid, name in rows:
        text += f"• {name}\n"
        buttons.append([InlineKeyboardButton(f"❌ Delete {name}", callback_data=f"admin_delete_loc_{lid}")])
        
    text += "\nTo add a city, use:\n`/location_add CityName`"
    
    buttons.append([InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_dashboard")])
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def admin_delete_loc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    lid = int(query.data.split("_")[-1])
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT name FROM locations WHERE id = ?", (lid,))
    row = cur.fetchone()
    if row:
        name = row[0]
        cur.execute("DELETE FROM locations WHERE id = ?", (lid,))
        conn.commit()
        await query.message.reply_text(f"✅ Deleted location: {name}")
    else:
        await query.message.reply_text("Location not found.")
    conn.close()
    await admin_manage_locations_callback(update, context)


async def admin_edit_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    # show interactive catalog list with edit/delete buttons
    await query.message.reply_text("Loading catalog...")
    await admin_list_catalog_callback(update, context)


async def admin_list_catalog_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # can be triggered as callback or called directly
    query = update.callback_query if update.callback_query else None
    if query:
        await query.answer()
        if query.from_user.id != ADMIN_CHAT_ID:
            return
    # fetch catalog rows
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        city TEXT,
        name TEXT,
        price INTEGER
    )''')
    conn.commit()
    cur.execute("SELECT id, city, name, price FROM catalog ORDER BY id DESC LIMIT 200")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Catalog is empty.")
        return

    parts = []
    buttons = []
    for r in rows:
        _id, city, name, price = r
        parts.append(f"#{_id} • {city} • {name} • ₹{price}")
        buttons.append([
            InlineKeyboardButton("Edit", callback_data=f"catalog_edit_{_id}"),
            InlineKeyboardButton("Delete", callback_data=f"catalog_delete_{_id}")
        ])

    text = "\n".join(parts[:50])
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text)
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="Use the buttons below to edit or delete entries:", reply_markup=InlineKeyboardMarkup(buttons))


async def admin_catalog_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    try:
        _id = int(query.data.split("_")[-1])
    except Exception:
        await query.message.reply_text("Invalid id")
        return
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("DELETE FROM catalog WHERE id = ?", (_id,))
    conn.commit()
    conn.close()
    await query.message.reply_text(f"✅ Deleted catalog entry #{_id}.")
    # refresh list
    await admin_list_catalog_callback(update, context)


async def admin_catalog_edit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    try:
        _id = int(query.data.split("_")[-1])
    except Exception:
        await query.message.reply_text("Invalid id")
        return
    # store editing state and prompt
    context.user_data['editing_catalog_id'] = _id
    await query.message.reply_text(f"Reply with the new values for catalog #{_id} in format: New Name|New Price")


async def admin_receive_catalog_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # only handle when admin in edit mode
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    edit_id = context.user_data.get('editing_catalog_id')
    if not edit_id:
        return
    if not update.message or not update.message.text:
        if update.message:
            await update.message.reply_text("Format invalid. Please reply with text in format: New Name|New Price")
        return
    text = update.message.text.strip()
    parts = text.split("|")
    if len(parts) != 2:
        await update.message.reply_text("Format invalid. Use: New Name|New Price")
        return
    new_name = parts[0].strip()
    try:
        new_price = int(parts[1].strip())
    except Exception:
        await update.message.reply_text("Price must be a number.")
        return
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("UPDATE catalog SET name = ?, price = ? WHERE id = ?", (new_name, new_price, edit_id))
        if cur.rowcount == 0:
            await update.message.reply_text(f"No catalog entry with id {edit_id} found.")
        else:
            await update.message.reply_text(f"✅ Updated catalog id {edit_id} -> {new_name} (₹{new_price})")
        conn.commit()
        conn.close()
    except Exception as e:
        await update.message.reply_text(f"Failed to update catalog: {e}")
    finally:
        context.user_data.pop('editing_catalog_id', None)


async def location_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /location_add CityName")
        return
    name = " ".join(context.args).strip()
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )''')
        cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (name,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Location added: {name}")
    except Exception as e:
        await update.message.reply_text(f"Failed to add location: {e}")


async def gardener_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_CHAT_ID:
        return
    try:
        gid = int(query.data.split("_")[-1])
    except Exception:
        await query.message.reply_text("Invalid gardener id")
        return
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("UPDATE gardeners SET verified = 1 WHERE id = ?", (gid,))
    conn.commit()
    
    cur.execute("SELECT user_id, location FROM gardeners WHERE id = ? LIMIT 1", (gid,))
    row = cur.fetchone()
    if row:
        user_id, location = row
        # Check if the location is in the custom format (City, State)
        if location and ", " in location:
            parts = location.split(", ")
            if len(parts) == 2:
                custom_city, custom_state = parts
                try:
                    # 1. Insert State if not exists
                    cur.execute("INSERT OR IGNORE INTO states (name) VALUES (?)", (custom_state,))
                    conn.commit()
                    # 2. Get State ID
                    cur.execute("SELECT id FROM states WHERE name = ? LIMIT 1", (custom_state,))
                    srow = cur.fetchone()
                    if srow:
                        state_id = srow[0]
                        # 3. Insert City if not exists
                        cur.execute("INSERT OR IGNORE INTO cities (state_id, name) VALUES (?, ?)", (state_id, custom_city))
                        # 4. Insert into locations table if not exists (for legacy fallback compatibility)
                        cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (custom_city,))
                        conn.commit()
                        
                        # 5. Clean up gardener's location column to just custom_city
                        cur.execute("UPDATE gardeners SET location = ? WHERE id = ?", (custom_city, gid))
                        conn.commit()
                except Exception as e:
                    logging.error(f"Failed to auto-insert custom state/city: {e}")
                    
    conn.close()
    await query.message.reply_text(f"✅ Gardener #{gid} approved.")
    if row and row[0]:
        try:
            # notify gardener and send invite link
            invite_link = "https://t.me/+gc-6mkEvNfwxNmI1"
            await context.bot.send_message(chat_id=row[0], text="✅ You have been verified as a gardener. You can now use the 'Gardener Dashboard' in the main menu.")
            await context.bot.send_message(chat_id=row[0], text=f"Join our gardener group: {invite_link}")
        except Exception:
            pass


async def gardener_become_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user

    if is_blocked(user.id):
        await query.message.reply_text("🚫 You are currently blocked.")
        return
    # check if already registered
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("SELECT id, verified FROM gardeners WHERE user_id = ? LIMIT 1", (user.id,))
        row = cur.fetchone()
        conn.close()
    except Exception:
        row = None

    if row:
        gid, verified = row[0], int(row[1]) if row[1] is not None else 0
        if verified == 1:
            await query.message.reply_text("You are already a verified gardener. You can use 'Sell Stock' from the main menu.")
            return
        else:
            await query.message.reply_text("Your gardener application is already submitted and pending admin approval. You'll be notified once approved.")
            return

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Yes", callback_data="gardener_can_yes")],[InlineKeyboardButton("No", callback_data="gardener_can_no")]])
    await query.message.reply_text("Can you source products and help sell them?", reply_markup=kb)


async def gardener_can_yes_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data['expecting_gardener_alias'] = True
    await query.message.reply_text("Great — what display name/alias should we call you? (e.g. Raju, GreenShop)")


async def gardener_can_no_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("No problem — thanks for checking. If you change your mind, use Become a gardener from the menu.")


async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # handle shared contact or typed phone during gardener signup
    if not (context.user_data.get('expecting_gardener_phone') or context.user_data.get('expecting_gardener_contact')):
        return
    # prefer contact object if present
    phone = None
    try:
        contact = update.message.contact
        if contact:
            phone = contact.phone_number
    except Exception:
        contact = None
    if not phone:
        # accept typed phone number
        text = (update.message.text or "").strip()
        # basic sanitization
        if text:
            phone = text
    if not phone:
        await update.message.reply_text("Couldn't read a phone number. Please type your phone number (digits, with country code if needed).")
        return
    context.user_data['gardener_phone'] = phone
    context.user_data.pop('expecting_gardener_phone', None)
    context.user_data.pop('expecting_gardener_contact', None)
    context.user_data['expecting_gardener_alias'] = True
    await update.message.reply_text("Thanks — what alias should we call you? (e.g. Raju, GreenShop)")


def levenshtein_distance(s1, s2):
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
        
    return previous_row[-1]


async def gardener_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if is_blocked(user_id):
        return
    # gardener signup: alias -> items -> location -> persist

    # gardener signup: items -> location -> persist
    if context.user_data.get('expecting_gardener_alias'):
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text message for your alias:")
            return
        alias = update.message.text.strip()
        context.user_data['gardener_alias'] = alias
        context.user_data.pop('expecting_gardener_alias', None)
        context.user_data['expecting_gardener_items'] = True
        await update.message.reply_text("Thanks — what items can you source? List them separated by commas.")
        return

    if context.user_data.get('expecting_gardener_items'):
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text list of items you can source (comma separated):")
            return
        items = update.message.text.strip()
        context.user_data['gardener_items'] = items
        context.user_data.pop('expecting_gardener_items', None)
        # start guided state -> city selection for gardener location (do not accept free text)
        context.user_data['expecting_gardener_location'] = 'awaiting_state'
        # show states keyboard
        try:
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT id, name FROM states ORDER BY name")
            states = cur.fetchall()
            conn.close()
        except Exception:
            states = MAJOR_STATES
        if isinstance(states, list) and states and isinstance(states[0], tuple):
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(name, callback_data=f"gardener_state_id_{sid}")] for sid, name in states])
        else:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(s, callback_data=f"gardener_state_{s}")] for s in states])
        await update.message.reply_text('Select the state(s) you operate in — we will then ask for city:', reply_markup=kb)
        return

    # gardener custom location signup: state -> city
    if context.user_data.get('expecting_custom_state'):
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text message for the state name:")
            return
        state_input = update.message.text.strip()
        state_name = state_input.title()
        context.user_data['custom_state'] = state_name
        context.user_data.pop('expecting_custom_state', None)
        context.user_data['expecting_custom_city'] = True
        await update.message.reply_text(f"State set to *{state_name}*.\n\nPlease type the name of the City you want to add under this state:", parse_mode="Markdown")
        return

    if context.user_data.get('expecting_custom_city'):
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text message for the city name:")
            return
        city_input = update.message.text.strip()
        city_name = city_input.title()
        state_name = context.user_data.get('custom_state')
        
        # Check if the location is already there (exact or fuzzy matching)
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("SELECT c.name, s.name FROM cities c JOIN states s ON c.state_id = s.id")
        all_locs = cur.fetchall() # list of (city, state)
        
        duplicate_found = False
        matched_city = None
        matched_state = None
        
        for db_city, db_state in all_locs:
            if db_state.lower() == state_name.lower():
                # Exact or substring match in same state
                if db_city.lower() == city_name.lower() or db_city.lower() in city_name.lower() or city_name.lower() in db_city.lower():
                    duplicate_found = True
                    matched_city = db_city
                    matched_state = db_state
                    break
                # Levenshtein distance check (close match <= 2)
                if levenshtein_distance(db_city.lower(), city_name.lower()) <= 2:
                    duplicate_found = True
                    matched_city = db_city
                    matched_state = db_state
                    break
                    
        if duplicate_found:
            conn.close()
            await update.message.reply_text(
                f"⚠️ *Location Already Exists!*\n\n"
                f"The location *{matched_city}, {matched_state}* is already registered. "
                f"Please do not add a duplicate location. Select it from the city list instead.\n\n"
                f"Please type a different City name, or start gardener signup again with /start:",
                parse_mode="Markdown"
            )
            return
            
        # Store as "City, State" in context to write to gardeners table
        context.user_data['gardener_city'] = f"{city_name}, {state_name}"
        context.user_data['is_custom_location'] = True
        context.user_data.pop('expecting_custom_city', None)
        conn.close()
        
        # Move to UPI stage
        context.user_data['expecting_gardener_upi'] = True
        await update.message.reply_text(
            f"Location set to *{city_name}, {state_name}* (pending verification).\n\n"
            f"One last step! 📱\n\nPlease provide your **UPI ID** (e.g. name@upi). This will be used by customers to pay you directly for your products.",
            parse_mode="Markdown"
        )
        return

    if context.user_data.get('expecting_gardener_location') == 'text':
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text message for the location name:")
            return
        loc = update.message.text.strip()
        context.user_data['gardener_city'] = loc
        context.user_data.pop('expecting_gardener_location', None)
        context.user_data['expecting_gardener_upi'] = True
        await update.message.reply_text("One last step! 📱\n\nPlease provide your **UPI ID** (e.g. name@upi). This will be used by customers to pay you directly for your products.")
        return

    if context.user_data.get('expecting_gardener_upi'):
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text message containing your UPI ID (e.g. name@upi):")
            return
        upi_id = update.message.text.strip()
        user = update.effective_user
        alias = context.user_data.get('gardener_alias')
        items = context.user_data.get('gardener_items')
        city_name = context.user_data.get('gardener_city')
        try:
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute('''CREATE TABLE IF NOT EXISTS gardeners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                phone TEXT,
                items TEXT,
                location TEXT,
                verified INTEGER,
                upi_id TEXT
            )''')
            if not context.user_data.get('is_custom_location'):
                cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (city_name,))
            # Store Telegram username automatically in username column, and display name (alias) in phone column
            tg_username = user.username or user.first_name
            cur.execute("INSERT INTO gardeners (user_id, username, phone, items, location, verified, upi_id) VALUES (?, ?, ?, ?, ?, 0, ?)", 
                        (user.id, tg_username, alias, items, city_name, upi_id))
            conn.commit()
            conn.close()
            await update.message.reply_text("Thanks! 🌿 Your gardener request has been submitted for admin approval. You'll be notified once approved.")
            # notify admin
            try:
                loc_str = city_name
                if context.user_data.get('is_custom_location'):
                    loc_str += " (NEW LOCATION REQUEST)"
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🔔 New gardener signup: @{tg_username} (Name: {alias})\nItems: {items}\nLocation: {loc_str}\nUPI: {upi_id}\nApprove via dashboard.")
            except Exception:
                pass
        except Exception as e:
            await update.message.reply_text(f"Failed to save gardener info: {e}")
        finally:
            context.user_data.pop('expecting_gardener_upi', None)
            context.user_data.pop('gardener_phone', None)
            context.user_data.pop('gardener_alias', None)
            context.user_data.pop('gardener_items', None)
            context.user_data.pop('gardener_city', None)
            context.user_data.pop('is_custom_location', None)
            context.user_data.pop('custom_state', None)
        return

    # gardener profile updates
    edit_field = context.user_data.get('editing_gardener_field')
    if edit_field:
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text message.")
            return
        new_val = update.message.text.strip()
        user_id = update.effective_user.id
        field_map = {
            "name": "phone",
            "location": "location",
            "upi": "upi_id"
        }
        db_field = field_map.get(edit_field)
        if db_field:
            try:
                conn = sqlite3.connect('store.db')
                cur = conn.cursor()
                cur.execute(f"UPDATE gardeners SET {db_field} = ? WHERE user_id = ?", (new_val, user_id))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ Your {edit_field} has been updated to: {new_val}")
            except Exception as e:
                await update.message.reply_text(f"Error updating profile: {e}")
        context.user_data.pop('editing_gardener_field', None)
        return

    # gardener selling flow: simple guided messages
    sell_stage = context.user_data.get('sell_stage')
    if sell_stage:
        if not update.message or not update.message.text:
            if update.message:
                await update.message.reply_text("Please reply with a text message.")
            return
        if sell_stage == 'location':
            context.user_data['sell_location'] = update.message.text.strip()
            context.user_data['sell_stage'] = 'product'
            await update.message.reply_text('Enter product name:')
            return
        if sell_stage == 'product':
            context.user_data['sell_product'] = update.message.text.strip()
            context.user_data['sell_stage'] = 'quantity'
            await update.message.reply_text('Enter quantity (in grams):')
            return
        if sell_stage == 'quantity':
            try:
                qty = int(update.message.text.strip())
                if qty < 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text('Quantity must be a positive number of grams. Enter quantity (in grams):')
                return
            context.user_data['sell_quantity'] = qty
            context.user_data['sell_stage'] = 'price'
            await update.message.reply_text('Enter price (number):')
            return
        if sell_stage == 'price':
            try:
                price = int(update.message.text.strip())
            except Exception:
                await update.message.reply_text('Price must be a number. Enter price:')
                return
            loc = context.user_data.get('sell_location')
            name = context.user_data.get('sell_product')
            qty = context.user_data.get('sell_quantity', 1)
            user = update.effective_user
            # find gardener id
            try:
                conn = sqlite3.connect('store.db')
                cur = conn.cursor()
                cur.execute("SELECT id FROM gardeners WHERE user_id = ? LIMIT 1", (user.id,))
                row = cur.fetchone()
                gardener_id = row[0] if row else None
                # ensure locations table
                cur.execute('''CREATE TABLE IF NOT EXISTS locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE
                )''')
                cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (loc,))
                cur.execute("INSERT INTO catalog (city, name, price, gardener_id, quantity) VALUES (?, ?, ?, ?, ?)", (loc, name, price, gardener_id, qty))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ Product added: {name} ({qty}g, Price: ₹{price}) in {loc}. It will appear in product listings.")
                # notify admin
                try:
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"New product from gardener @{user.username or user.first_name}: {name} ({qty}g) in {loc} (₹{price})")
                except Exception:
                    pass
            except Exception as e:
                await update.message.reply_text(f"Failed to add product: {e}")
            finally:
                context.user_data.pop('sell_stage', None)
                context.user_data.pop('sell_location', None)
                context.user_data.pop('sell_product', None)
                context.user_data.pop('sell_quantity', None)
            return

    # otherwise, allow normal routing
    return


async def catalog_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    text = " ".join(context.args).strip()
    parts = text.split("|")
    if len(parts) != 3:
        await update.message.reply_text("Usage: /catalog_add City|Name|Price")
        return
    city, name, price = [p.strip() for p in parts]
    try:
        price_int = int(price)
    except Exception:
        await update.message.reply_text("Price must be a number.")
        return
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute('''CREATE TABLE IF NOT EXISTS catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            name TEXT,
            price INTEGER
        )''')
        cur.execute("INSERT INTO catalog (city, name, price) VALUES (?, ?, ?)", (city, name, price_int))
        conn.commit()
        # ensure location exists
        cur.execute('''CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )''')
        cur.execute("INSERT OR IGNORE INTO locations (name) VALUES (?)", (city,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Product added: {name} (₹{price_int}) in {city}")
    except Exception as e:
        await update.message.reply_text(f"Failed to add product: {e}")


async def catalog_update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    text = " ".join(context.args).strip()
    parts = text.split("|")
    if len(parts) != 3:
        await update.message.reply_text("Usage: /catalog_update id|New Name|New Price")
        return
    try:
        cid = int(parts[0].strip())
        new_name = parts[1].strip()
        new_price = int(parts[2].strip())
    except Exception:
        await update.message.reply_text("Invalid id or price format.")
        return
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("UPDATE catalog SET name = ?, price = ? WHERE id = ?", (new_name, new_price, cid))
        if cur.rowcount == 0:
            await update.message.reply_text(f"No catalog entry with id {cid} found.")
        else:
            await update.message.reply_text(f"✅ Updated catalog id {cid} -> {new_name} (₹{new_price})")
        conn.commit()
        conn.close()
    except Exception as e:
        await update.message.reply_text(f"Failed to update catalog: {e}")


async def catalog_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /catalog_delete id")
        return
    try:
        cid = int(context.args[0])
    except Exception:
        await update.message.reply_text("id must be a number")
        return
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("DELETE FROM catalog WHERE id = ?", (cid,))
        if cur.rowcount == 0:
            await update.message.reply_text(f"No catalog entry with id {cid} found.")
        else:
            await update.message.reply_text(f"✅ Deleted catalog entry id {cid}.")
        conn.commit()
        conn.close()
    except Exception as e:
        await update.message.reply_text(f"Failed to delete catalog entry: {e}")


async def location_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    if not context.args:
        await update.message.reply_text("Usage: /location_delete CityName")
        return
    name = " ".join(context.args).strip()
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("DELETE FROM locations WHERE name = ?", (name,))
        if cur.rowcount == 0:
            await update.message.reply_text(f"No location named {name} found.")
        else:
            await update.message.reply_text(f"✅ Deleted location {name}.")
        conn.commit()
        conn.close()
    except Exception as e:
        await update.message.reply_text(f"Failed to delete location: {e}")

async def city_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # parse city selection. supports both `city_<name>` and `city_id_<id>`
    data = query.data
    city = None
    if data.startswith("city_id_"):
        try:
            cid = int(data.split("_")[-1])
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT name FROM cities WHERE id = ? LIMIT 1", (cid,))
            row = cur.fetchone()
            conn.close()
            if row:
                city = row[0]
        except Exception:
            city = None
    else:
        # parse city after prefix `city_` (supports legacy multi-word cities)
        city = query.data[len("city_"):]
    context.user_data['city'] = city
    # load catalog from DB so bot sees latest changes
    catalog = load_catalog_from_db()
    # pre-fetch gardener names for this city's items to avoid N queries
    gardener_names = {}
    item_list = catalog.get(city, [])
    gids = list(set(item['gardener_id'] for item in item_list if item.get('gardener_id')))
    if gids:
        try:
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            placeholders = ",".join(["?"] * len(gids))
            cur.execute(f"SELECT id, username FROM gardeners WHERE id IN ({placeholders})", gids)
            gardener_names = {row[0]: row[1] for row in cur.fetchall()}
            conn.close()
        except Exception:
            pass

    keyboard = []
    for idx, item in enumerate(item_list):
        qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
        btn_text = f"{item['name']}{qty_str} (₹{item['price']})"
        gid = item.get('gardener_id')
        if gid and gid in gardener_names:
            btn_text += f" - by {gardener_names[gid]}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"prod_{idx}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"📍 Stock available in **{city}**:\nSelect a plant to purchase:", reply_markup=reply_markup, parse_mode="Markdown")
    return SELECT_PRODUCT

async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    prod_idx = int(query.data.split("_")[1])
    city = context.user_data.get('city')
    if not city:
        await query.message.reply_text("Session lost. Please start again with /start.")
        return ConversationHandler.END

    # read catalog fresh from DB
    catalog = load_catalog_from_db()
    product = catalog.get(city, [])[prod_idx]

    # Initialize cart if not present
    if 'cart' not in context.user_data:
        context.user_data['cart'] = []
    
    cart = context.user_data['cart']
    if cart:
        # Check if gardener matches
        existing_gardener_id = cart[0].get('gardener_id')
        new_gardener_id = product.get('gardener_id')
        if existing_gardener_id != new_gardener_id:
            # Different gardener! Show warning
            message_text = (
                f"⚠️ *Different Seller Detected!*\n\n"
                f"Your cart already contains items from another gardener. You can only order items from one gardener per transaction.\n\n"
                f"Would you like to clear your current cart and add *{product['name']}*, or keep your current cart?"
            )
            keyboard = [
                [
                    InlineKeyboardButton("🗑️ Clear Cart & Add", callback_data=f"clearadd_{prod_idx}"),
                ],
                [
                    InlineKeyboardButton("🔙 Keep Current Cart", callback_data="show_cart")
                ]
            ]
            await query.message.delete()
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=message_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="Markdown"
            )
            return SELECT_PRODUCT

    # Add item to cart
    context.user_data['cart'].append({
        'id': product['id'],
        'name': product['name'],
        'price': product['price'],
        'gardener_id': product.get('gardener_id'),
        'quantity': product.get('quantity', 1),
        'cart_quantity': 1
    })

    # Render current cart contents
    cart = context.user_data['cart']
    total_price = sum(item['price'] * item.get('cart_quantity', 1) for item in cart)
    
    cart_lines = []
    for idx, item in enumerate(cart, 1):
        qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
        cart_qty = item.get('cart_quantity', 1)
        item_total = item['price'] * cart_qty
        cart_lines.append(f"{idx}. *{item['name']}*{qty_str} - {cart_qty}x (₹{item['price']}) = ₹{item_total}")
    
    cart_text = "\n".join(cart_lines)
    
    message_text = (
        f"🛒 *Cart Updated!*\n\n"
        f"Added *{product['name']}* (₹{product['price']}) to your cart.\n\n"
        f"📋 *Current Cart:*\n"
        f"{cart_text}\n\n"
        f"💰 *Total Cart Value:* ₹{total_price}\n\n"
        f"Adjust quantities below or proceed to checkout:"
    )

    keyboard = []
    for idx, item in enumerate(cart):
        c_qty = item.get('cart_quantity', 1)
        keyboard.append([
            InlineKeyboardButton(f"➖ {item['name']}", callback_data=f"cartdec_{idx}"),
            InlineKeyboardButton(f"{c_qty} unit(s)", callback_data="cartinfo"),
            InlineKeyboardButton(f"➕", callback_data=f"cartinc_{idx}")
        ])
        
    keyboard.append([
        InlineKeyboardButton("➕ Add More Plants", callback_data=f"city_{city}"),
    ])
    keyboard.append([
        InlineKeyboardButton("🛍️ Proceed to Checkout", callback_data="checkout_start")
    ])
    keyboard.append([
        InlineKeyboardButton("🗑️ Clear Cart", callback_data="clear_cart"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="view_menu")
    ])

    await query.message.delete()
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return SELECT_PRODUCT


async def clear_cart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer("Cart cleared.")
    context.user_data.pop('cart', None)
    return await view_menu_callback(update, context)


async def cart_increment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    cart = context.user_data.get('cart', [])
    if idx < len(cart):
        cart[idx]['cart_quantity'] = cart[idx].get('cart_quantity', 1) + 1
    return await show_cart_callback(update, context)


async def cart_decrement_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split("_")[1])
    cart = context.user_data.get('cart', [])
    if idx < len(cart):
        qty = cart[idx].get('cart_quantity', 1) - 1
        if qty <= 0:
            cart.pop(idx)
        else:
            cart[idx]['cart_quantity'] = qty
            
    if not cart:
        context.user_data.pop('cart', None)
        await query.message.reply_text("Your cart is now empty.")
        return await view_menu_callback(update, context)
        
    return await show_cart_callback(update, context)


async def clear_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    prod_idx = int(query.data.split("_")[1])
    # Clear the cart first
    context.user_data['cart'] = []
    # Set query data to prod_{prod_idx} so product_selected can process it
    query.data = f"prod_{prod_idx}"
    return await product_selected(update, context)


async def show_cart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    cart = context.user_data.get('cart', [])
    if not cart:
        await query.message.reply_text("Your cart is empty.")
        return await view_menu_callback(update, context)
        
    city = context.user_data.get('city')
    total_price = sum(item['price'] * item.get('cart_quantity', 1) for item in cart)
    
    cart_lines = []
    for idx, item in enumerate(cart, 1):
        qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
        cart_qty = item.get('cart_quantity', 1)
        item_total = item['price'] * cart_qty
        cart_lines.append(f"{idx}. *{item['name']}*{qty_str} - {cart_qty}x (₹{item['price']}) = ₹{item_total}")
    
    cart_text = "\n".join(cart_lines)
    
    message_text = (
        f"🛒 *Cart Summary*\n\n"
        f"📋 *Current Cart:*\n"
        f"{cart_text}\n\n"
        f"💰 *Total Cart Value:* ₹{total_price}\n\n"
        f"Adjust quantities below or proceed to checkout:"
    )

    keyboard = []
    for idx, item in enumerate(cart):
        c_qty = item.get('cart_quantity', 1)
        keyboard.append([
            InlineKeyboardButton(f"➖ {item['name']}", callback_data=f"cartdec_{idx}"),
            InlineKeyboardButton(f"{c_qty} unit(s)", callback_data="cartinfo"),
            InlineKeyboardButton(f"➕", callback_data=f"cartinc_{idx}")
        ])
        
    keyboard.append([
        InlineKeyboardButton("➕ Add More Plants", callback_data=f"city_{city}"),
    ])
    keyboard.append([
        InlineKeyboardButton("🛍️ Proceed to Checkout", callback_data="checkout_start")
    ])
    keyboard.append([
        InlineKeyboardButton("🗑️ Clear Cart", callback_data="clear_cart"),
        InlineKeyboardButton("🔙 Main Menu", callback_data="view_menu")
    ])

    try:
        await query.message.delete()
    except Exception:
        pass
        
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text=message_text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return SELECT_PRODUCT


async def checkout_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    cart = context.user_data.get('cart', [])
    if not cart:
        await query.message.reply_text("Your cart is empty! Please add some plants first.")
        return ConversationHandler.END

    total_price = sum(item['price'] * item.get('cart_quantity', 1) for item in cart)
    discount = context.user_data.get('discount_amount', 0)
    total_due = total_price - discount
    if total_due < 0:
        total_due = 0
        
    discount_str = f"💎 *Discount Applied:* -₹{discount}\n" if discount > 0 else ""
    
    message_text = (
        f"🛍️ *Checkout Summary*\n\n"
        f"Total Items: {sum(item.get('cart_quantity', 1) for item in cart)}\n"
        f"Subtotal: *₹{total_price}*\n"
        f"{discount_str}"
        f"Total Price: *₹{total_due}*\n\n"
        f"Please choose your preferred delivery method:\n\n"
        f"1. 👤 *Contact Seller Directly*: Proceed with checkout, and get direct messaging link(s) to the seller(s) at the end.\n"
        f"2. 📦 *Anonymous Drop-off (+₹99)*: Drop-off at a convenient nearby spot with a drop pin & photo."
    )
    
    keyboard = [
        [InlineKeyboardButton("👤 Contact Seller Directly", callback_data="delivery_direct")],
        [InlineKeyboardButton("📦 Anonymous Drop-off (+₹99)", callback_data="delivery_anonymous")],
        [InlineKeyboardButton("🔙 Back to Cart", callback_data=f"city_{context.user_data.get('city')}")]
    ]
    
    try:
        await query.message.edit_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    return SELECT_DELIVERY_METHOD


async def delivery_direct_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['delivery_method'] = 'direct'
    return await prompt_delivery_slot(update, context)


async def delivery_anonymous_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['delivery_method'] = 'anonymous'
    return await prompt_delivery_slot(update, context)


async def prompt_delivery_slot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    
    message_text = (
        f"📅 *Select Delivery Time Slot*\n\n"
        f"Please select your preferred time slot for the delivery drop-off:"
    )
    
    keyboard = [
        [InlineKeyboardButton("🌅 Morning (9 AM - 12 PM)", callback_data="slot_morning")],
        [InlineKeyboardButton("☀️ Afternoon (12 PM - 4 PM)", callback_data="slot_afternoon")],
        [InlineKeyboardButton("🌇 Evening (4 PM - 8 PM)", callback_data="slot_evening")],
        [InlineKeyboardButton("🌙 Night (8 PM - 11 PM)", callback_data="slot_night")]
    ]
    
    try:
        await query.message.edit_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        
    return SELECT_DELIVERY_METHOD


async def delivery_slot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    slot_map = {
        "slot_morning": "🌅 Morning (9 AM - 12 PM)",
        "slot_afternoon": "☀️ Afternoon (12 PM - 4 PM)",
        "slot_evening": "🌇 Evening (4 PM - 8 PM)",
        "slot_night": "🌙 Night (8 PM - 11 PM)"
    }
    context.user_data['delivery_slot'] = slot_map.get(data, "Standard Slot")
    return await proceed_to_payment_flow(update, context)


async def proceed_to_payment_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    cart = context.user_data.get('cart', [])
    delivery_method = context.user_data.get('delivery_method')
    delivery_slot = context.user_data.get('delivery_slot', 'Standard Slot')
    
    subtotal = sum(item['price'] * item.get('cart_quantity', 1) for item in cart)
    delivery_fee = 99 if delivery_method == 'anonymous' else 0
    discount = context.user_data.get('discount_amount', 0)
    
    total_due = subtotal + delivery_fee - discount
    if total_due < 0:
        total_due = 0
        
    context.user_data['total_due'] = total_due

    unique_gardener_ids = list(set(item['gardener_id'] for item in cart if item.get('gardener_id')))
    current_upi = UPI_ID
    gardener_source = None
    
    if len(unique_gardener_ids) == 1:
        gardener_source = unique_gardener_ids[0]
        try:
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT upi_id FROM gardeners WHERE id = ? LIMIT 1", (gardener_source,))
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                current_upi = row[0]
        except Exception:
            pass
            
    context.user_data['gardener_source'] = gardener_source

    encoded_name = urllib.parse.quote(MERCHANT_NAME)
    encoded_note = urllib.parse.quote(f"Order for {len(cart)} items")
    upi_url = f"upi://pay?pa={current_upi}&pn={encoded_name}&am={total_due}&cu=INR&tn={encoded_note}"
    
    qr_code_api = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(upi_url)}"
    
    items_desc = []
    for item in cart:
        qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
        c_qty = item.get('cart_quantity', 1)
        items_desc.append(f"• {item['name']}{qty_str} - {c_qty}x (₹{item['price']})")
    items_text = "\n".join(items_desc)

    delivery_label = "📦 Anonymous Drop-off (+₹99)" if delivery_method == 'anonymous' else "👤 Contact Seller Directly"
    discount_line = f"💎 *Discount Applied:* -₹{discount}\n" if discount > 0 else ""

    instruction_text = (
        f"🛒 *Your Order Details:*\n"
        f"{items_text}\n\n"
        f"🚚 *Delivery Method:* {delivery_label}\n"
        f"⏰ *Delivery Slot:* {delivery_slot}\n"
        f"{discount_line}"
        f"💰 *Total Amount Due:* ₹{total_due}\n\n"
        f"📱 You can pay directly by scanning the QR code image below or sending money to our UPI ID.\n\n"
        f"⚠️ *After completing the payment*, find the **12-digit UTR / UPI Ref No.** in your payment app (Google Pay, PhonePe, Paytm) and reply directly to this message with it."
    )

    await query.message.delete()
    
    await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=qr_code_api,
        caption=instruction_text,
        parse_mode="Markdown"
    )
    
    try:
        context.user_data['expecting_utr'] = True
    except Exception:
        pass
    return PROCESS_PAYMENT


async def contact_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    try:
        admin_username = os.environ.get("ADMIN_USERNAME")
        if admin_username:
            url = f"https://t.me/{admin_username}"
        else:
            url = f"tg://user?id={ADMIN_CHAT_ID}"
    except Exception:
        url = None

    buttons = []
    if url:
        buttons.append([InlineKeyboardButton("💬 Send Direct Message", url=url)])
    buttons.append([InlineKeyboardButton("✉️ Start Live Chat Support", callback_data="start_support_ticket")])
    buttons.append([InlineKeyboardButton("Back to Menu", callback_data="view_menu")])
    reply_markup = InlineKeyboardMarkup(buttons)
    try:
        await query.message.edit_text("Need help? Choose a support option below:", reply_markup=reply_markup)
    except Exception:
        await query.message.reply_text("Need help? Choose a support option below.", reply_markup=reply_markup)
    return ConversationHandler.END


async def order_ticket_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split("_")[-1])
    
    text = (
        f"🎫 *Raise Support Ticket for Order #{order_id}*\n\n"
        f"Please select the topic/reason why you are raising this ticket:"
    )
    
    buttons = [
        [InlineKeyboardButton("💳 Payment Verification Issue", callback_data=f"raise_ticket_{order_id}_payment")],
        [InlineKeyboardButton("⏳ Delivery Delay", callback_data=f"raise_ticket_{order_id}_delay")],
        [InlineKeyboardButton("🥀 Incorrect/Damaged Item", callback_data=f"raise_ticket_{order_id}_damaged")],
        [InlineKeyboardButton("❓ Other Query", callback_data=f"raise_ticket_{order_id}_other")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"track_order_{order_id}")]
    ]
    
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def order_raise_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    # query.data format: raise_ticket_{order_id}_{topic}
    parts = query.data.split("_")
    order_id = int(parts[2])
    topic_key = parts[3]
    
    user = query.from_user
    user_id = user.id
    username = user.username or user.first_name
    
    topic_titles = {
        'payment': "💳 Payment Verification Issue",
        'delay': "⏳ Delivery Delay",
        'damaged': "🥀 Incorrect/Damaged Item",
        'other': "❓ Other Query"
    }
    topic_title = topic_titles.get(topic_key, "General Issue")
    
    # Fetch order details to attach to ticket metadata
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT product, price, utr_no FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    conn.close()
    
    product = "Unknown Product"
    price = 0
    utr = "N/A"
    if row:
        product, price, utr = row
        
    # Send ticket notification to the Admin
    admin_text = (
        f"🎫 *New Order Support Ticket Raised*\n\n"
        f"👤 *Customer:* @{username} (ID: `{user_id}`)\n"
        f"📦 *Order:* #{order_id} - *{product}* (₹{price})\n"
        f"🏷 *Topic:* {topic_title}\n"
        f"🔢 *UTR:* `{utr}`\n\n"
        f"👉 *Reply directly* to this message to chat with the customer."
    )
    
    try:
        admin_alert_msg = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_text,
            parse_mode="Markdown"
        )
        
        # Log to DB
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO support_tickets (user_id, username, admin_message_id, user_message_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, admin_alert_msg.message_id, query.message.message_id, datetime.datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        
        # Enter support chat mode
        context.user_data.pop('expecting_address', None)
        context.user_data.pop('expecting_utr', None)
        context.user_data.pop('expecting_quantity', None)
        context.user_data.pop('expecting_product_name', None)
        context.user_data.pop('expecting_product_price', None)
        context.user_data.pop('expecting_city_name', None)
        context.user_data.pop('expecting_review_order_id', None)
        context.user_data.pop('expecting_rating_order_id', None)
        
        context.user_data['expecting_support_msg'] = True
        
        buttons = [[InlineKeyboardButton("❌ Cancel Live Chat", callback_data="cancel_support_ticket")]]
        
        user_text = (
            f"💬 *Support Ticket Raised (Order #{order_id})*\n\n"
            f"Topic: *{topic_title}*\n\n"
            f"Please type your details/message below and send it. The admin has been notified and will reply directly in this chat.\n\n"
            f"Click the button below at any time to end the chat."
        )
        await query.message.edit_text(user_text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Error raising support ticket for order: {e}")
        await query.message.reply_text("⚠️ Failed to initiate support ticket. Please contact the admin directly or try again later.")


async def start_support_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    
    # Reset other states
    context.user_data.pop('expecting_address', None)
    context.user_data.pop('expecting_utr', None)
    context.user_data.pop('expecting_quantity', None)
    context.user_data.pop('expecting_product_name', None)
    context.user_data.pop('expecting_product_price', None)
    context.user_data.pop('expecting_city_name', None)
    context.user_data.pop('expecting_review_order_id', None)
    context.user_data.pop('expecting_rating_order_id', None)
    
    context.user_data['expecting_support_msg'] = True
    
    buttons = [[InlineKeyboardButton("❌ Cancel Live Chat", callback_data="cancel_support_ticket")]]
    reply_markup = InlineKeyboardMarkup(buttons)
    
    text = (
        "💬 *Live Support Chat Started*\n\n"
        "Please type your message or support request below and send it. "
        "The admin will receive your message and reply directly in this chat.\n\n"
        "Click the button below at any time to cancel/end the chat."
    )
    
    try:
        await query.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    return ConversationHandler.END


async def cancel_support_ticket_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data.pop('expecting_support_msg', None)
    
    # Go back to menu
    return await view_menu_callback(update, context)


async def modus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    modus_text = (
        "Seede-manali-se\nYour Local  Plug🔌\nGet safe and top quality items Anonymously\n"
        "The new safe way to get you'r shit without the bullshit\n\n"
        "How It Works ??\n"
        "1. select your city\n"
        "2. Select products \n"
        "3. pay the amount (all deatails wiull be auto deleted within 24 hours )\n"
        "4. you send us an address thats's convinent to you \n"
        "5. we drop the product in discreet packaging at your location and send you exact 📍 and a photo \n"
        "6. that's it you enjoy yourself \n\nlet your buddiess know also \nthank you"
    )
    try:
        await query.message.edit_text(modus_text)
    except Exception:
        await query.message.reply_text(modus_text)
    # re-show main menu after modus
    keyboard = [
        [InlineKeyboardButton("Contact Admin ✉️", callback_data="contact_admin")],
        [InlineKeyboardButton("Modus Operandi 📜", callback_data="modus")],
        [InlineKeyboardButton("View Products 🛍️", callback_data="view_products")]
    ]
    await query.message.reply_text("Back to menu:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


async def view_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # show states driven from DB (id-based callbacks)
    keyboard = []
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("SELECT id, name FROM states ORDER BY name")
        rows = cur.fetchall()
        conn.close()
        for sid, name in rows:
            keyboard.append([InlineKeyboardButton(name, callback_data=f"state_id_{sid}")])
    except Exception:
        for st in MAJOR_STATES:
            keyboard.append([InlineKeyboardButton(st, callback_data=f"state_{st}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.message.edit_text("Please select your delivery location to view available stock:", reply_markup=reply_markup)
    except Exception:
        await query.message.reply_text("Please select your delivery location to view available stock:", reply_markup=reply_markup)
    return SELECT_CITY


async def view_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    keyboard = [
        [InlineKeyboardButton("🛍️ View Products", callback_data="view_products")],
        [InlineKeyboardButton("📜 My Orders", callback_data="user_orders")],
        [InlineKeyboardButton("🎁 Refer & Earn", callback_data="refer_earn")],
        [InlineKeyboardButton("✉️ Contact Admin", callback_data="contact_admin")],
        [InlineKeyboardButton("📜 Modus Operandi", callback_data="modus")]
    ]
    # Check if admin
    user_id = update.effective_user.id if update.effective_user else (query.from_user.id if query else None)
    if user_id == ADMIN_CHAT_ID:
        keyboard.append([InlineKeyboardButton("🔧 Admin Dashboard", callback_data="admin_dashboard")])
    
    # Check if gardener
    if user_id:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("SELECT verified FROM gardeners WHERE user_id = ? LIMIT 1", (user_id,))
        grow = cur.fetchone()
        conn.close()
        if grow:
            if grow[0] == 1:
                keyboard.append([InlineKeyboardButton("👨‍🌾 Gardener Dashboard", callback_data="gardener_dashboard")])
        else:
            keyboard.append([InlineKeyboardButton("👨‍🌾 Become a Gardener", callback_data="become_gardener")])

    text = "Welcome to the Local Plant Shop! 🌿\nChoose an option below:"
    if query:
        try:
            await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        if update.message:
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


async def state_selected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    
    state_id = None
    state_name = "Selected State"
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    
    if data.startswith("state_id_"):
        try:
            state_id = int(data[len("state_id_"):])
            cur.execute("SELECT name FROM states WHERE id = ? LIMIT 1", (state_id,))
            row = cur.fetchone()
            if row:
                state_name = row[0]
        except Exception:
            pass
    elif data.startswith("state_"):
        state_name = data[len("state_"):]
        try:
            cur.execute("SELECT id FROM states WHERE name = ? LIMIT 1", (state_name,))
            row = cur.fetchone()
            if row:
                state_id = row[0]
        except Exception:
            pass
            
    cities = []
    if state_id is not None:
        try:
            cur.execute("SELECT id, name FROM cities WHERE state_id = ? ORDER BY name", (state_id,))
            cities = cur.fetchall()
            
            # Seeding fallback: if no cities are registered for this state, insert major cities from constant
            if not cities and state_name in MAJOR_STATE_CITIES:
                for c_name in MAJOR_STATE_CITIES[state_name]:
                    cur.execute("INSERT OR IGNORE INTO cities (state_id, name) VALUES (?, ?)", (state_id, c_name))
                conn.commit()
                # Re-fetch
                cur.execute("SELECT id, name FROM cities WHERE state_id = ? ORDER BY name", (state_id,))
                cities = cur.fetchall()
        except Exception as e:
            logging.error(f"Error fetching/seeding cities: {e}")
            
    conn.close()
    
    # Render keyboard
    keyboard = []
    if cities:
        keyboard = [[InlineKeyboardButton(name, callback_data=f"city_id_{cid}")] for cid, name in cities]
    
    if not keyboard:
        keyboard.append([InlineKeyboardButton("🔙 Back to States", callback_data="view_products")])
        msg_text = f"📍 No cities currently registered under *{state_name}*."
    else:
        keyboard.append([InlineKeyboardButton("🔙 Back to States", callback_data="view_products")])
        keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data="view_menu")])
        msg_text = f"Selected state: *{state_name}*.\n\nChoose your city:"
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    try:
        await query.message.edit_text(msg_text, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception:
        await query.message.reply_text(msg_text, reply_markup=reply_markup, parse_mode="Markdown")
        
    return SELECT_CITY


async def state_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    state = query.data[len("state_search_"):]
    logging.info(f"state_search set for user={update.effective_user.id if update.effective_user else 'unknown'} state={state}")
    # set flag so next text message is treated as a search query
    context.user_data['state_search'] = state
    await query.message.reply_text(f"Type part of the city name to search within {state}:")
    return ConversationHandler.END


async def gardener_state_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data
    state_id = None
    try:
        if data.startswith("gardener_state_id_"):
            state_id = int(data.split("_")[-1])
        elif data.startswith("gardener_state_"):
            # legacy: lookup by name
            state_name = data[len("gardener_state_"):]
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT id FROM states WHERE name = ? LIMIT 1", (state_name,))
            r = cur.fetchone()
            conn.close()
            state_id = r[0] if r else None
    except Exception:
        state_id = None

    # determine state display name
    state_name = None
    try:
        if state_id:
            conn2 = sqlite3.connect('store.db')
            cur2 = conn2.cursor()
            cur2.execute("SELECT name FROM states WHERE id = ? LIMIT 1", (state_id,))
            rr = cur2.fetchone()
            conn2.close()
            state_name = rr[0] if rr else None

    except Exception:
        state_name = None

    # fetch cities for gardener flow
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        if state_id:
            cur.execute("SELECT id, name FROM cities WHERE state_id = ? ORDER BY name", (state_id,))
        else:
            cur.execute("SELECT id, name FROM cities ORDER BY name LIMIT 50")
        cities = cur.fetchall()
        conn.close()
    except Exception:
        cities = []

    # use id-based callback_data to avoid invalid/too-long callback payloads
    keyboard = [[InlineKeyboardButton(name, callback_data=f"gardener_city_id_{cid}")] for cid, name in cities]
    keyboard.append([InlineKeyboardButton("➕ Add new location", callback_data="gardener_add_custom_loc")])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="view_menu")])
    try:
        await query.message.edit_text(f"Selected state: {state_name or state_id}. Please pick your city:", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception:
        await query.message.reply_text(f"Selected state: {state_name or state_id}. Please pick your city:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END


async def gardener_city_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    # expect id-based callback: gardener_city_id_<id>
    data = query.data
    city_name = None
    try:
        if data.startswith("gardener_city_id_"):
            cid = int(data.split("_")[-1])
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT name FROM cities WHERE id = ? LIMIT 1", (cid,))
            r = cur.fetchone()
            conn.close()
            city_name = r[0] if r else None
    except Exception:
        city_name = None

    if not city_name:
        await query.message.reply_text("Invalid city selection.")
        return ConversationHandler.END

    context.user_data['gardener_city'] = city_name
    context.user_data['expecting_gardener_upi'] = True
    await query.message.reply_text("One last step! 📱\n\nPlease provide your **UPI ID** (e.g. name@upi). This will be used by customers to pay you directly for your products.")
    return ConversationHandler.END


async def gardener_add_custom_loc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data['expecting_custom_state'] = True
    await query.message.reply_text("Please type the name of the State you operate in:")
    return ConversationHandler.END


async def gardener_sell_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query: await query.answer()
    user = (query.from_user if query else update.effective_user)
    # verify gardener status first
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("SELECT id, location, verified FROM gardeners WHERE user_id = ? LIMIT 1", (user.id,))
        row = cur.fetchone()
        conn.close()
    except Exception:
        row = None

    if not row or int(row[2]) != 1:
        msg = "Only verified gardeners can add products."
        if query: await query.message.reply_text(msg)
        else: await update.message.reply_text(msg)
        return

    if row[1]:
        # prefill location and jump to product entry
        context.user_data['sell_location'] = row[1]
        context.user_data['sell_stage'] = 'product'
        msg = f"You're selling from {row[1]}.\n\nEnter product name:"
        if query: await query.message.reply_text(msg)
        else: await update.message.reply_text(msg)
        return

    # if no location known, ask for location first
    context.user_data['sell_stage'] = 'location'
    msg = 'Enter the city/location where you will sell from:'
    if query: await query.message.reply_text(msg)
    else: await update.message.reply_text(msg)
    return


async def gardener_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    if is_blocked(user_id):
        await query.message.reply_text("🚫 You are currently blocked.")
        return
    
    # check if verified gardener
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id, verified FROM gardeners WHERE user_id = ? LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row or int(row[1]) != 1:
        await query.message.reply_text("Unauthorized.")
        return

    keyboard = [
        [InlineKeyboardButton("📦 My Products", callback_data="gardener_my_products")],
        [InlineKeyboardButton("📜 My Orders", callback_data="gardener_my_orders")],
        [InlineKeyboardButton("📊 Sales Stats", callback_data="gardener_stats")],
        [InlineKeyboardButton("👤 Profile", callback_data="gardener_profile")],
        [InlineKeyboardButton("➕ Add New Product", callback_data="gardener_sell")],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="view_menu")]
    ]
    await query.message.edit_text("👨‍🌾 *Gardener Dashboard*\nManage your listings and orders below:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def gardener_my_products_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    # find gardener internal id
    cur.execute("SELECT id FROM gardeners WHERE user_id = ? LIMIT 1", (user_id,))
    grow = cur.fetchone()
    if not grow:
        conn.close()
        await query.message.reply_text("Gardener record not found.")
        return
    gardener_id = grow[0]
    
    try:
        cur.execute("SELECT id, name, price, city, quantity FROM catalog WHERE gardener_id = ?", (gardener_id,))
        products = cur.fetchall()
    except Exception:
        cur.execute("SELECT id, name, price, city FROM catalog WHERE gardener_id = ?", (gardener_id,))
        products = [r + (1,) for r in cur.fetchall()]
    conn.close()
    
    if not products:
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="gardener_dashboard")]]
        await query.message.edit_text("You have no active products listed.", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    lines = ["Your active listings:"]
    keyboard = []
    for pid, name, price, city, qty in products:
        qty_val = qty if qty is not None else 1
        lines.append(f"• {name} ({city}) - ₹{price} ({qty_val}g)")
        keyboard.append([InlineKeyboardButton(f"🗑 Delete {name}", callback_data=f"gardener_del_prod_{pid}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="gardener_dashboard")])
    await query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def gardener_delete_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    pid = query.data.split("_")[-1]
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    # auth check: does this product belong to this user?
    cur.execute("SELECT gardener_id FROM catalog WHERE id = ?", (pid,))
    row = cur.fetchone()
    if row:
        gardener_id = row[0]
        cur.execute("SELECT user_id FROM gardeners WHERE id = ?", (gardener_id,))
        grow = cur.fetchone()
        if grow and grow[0] == query.from_user.id:
            cur.execute("DELETE FROM catalog WHERE id = ?", (pid,))
            conn.commit()
            await query.message.reply_text(f"✅ Product deleted successfully.")
        else:
            await query.message.reply_text("Unauthorized.")
    conn.close()
    # refresh list
    await gardener_my_products_callback(update, context)


async def gardener_profile_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT username, phone, location, upi_id FROM gardeners WHERE user_id = ? LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        await query.message.reply_text("Gardener record not found.")
        return
    
    tg_uname, name, loc, upi = row
    text = (
        f"👤 *Your Profile*\n\n"
        f"📛 *Name:* {name}\n"
        f"✈️ *Telegram:* @{tg_uname or 'Not set'}\n"
        f"📍 *Location:* {loc}\n"
        f"📱 *UPI ID:* {upi or 'Not set'}\n\n"
        "Click a button below to update your information:"
    )
    
    keyboard = [
        [InlineKeyboardButton("Edit Name", callback_data="gardener_edit_name")],
        [InlineKeyboardButton("Edit Location", callback_data="gardener_edit_location")],
        [InlineKeyboardButton("Edit UPI ID", callback_data="gardener_edit_upi")],
        [InlineKeyboardButton("🔙 Back", callback_data="gardener_dashboard")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def gardener_edit_field_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    field = query.data.replace("gardener_edit_", "")
    context.user_data['editing_gardener_field'] = field
    
    prompts = {
        "name": "Enter your new display name:",
        "location": "Enter your new location (City/Area):",
        "upi": "Enter your new UPI ID (for receiving payments):"
    }
    await query.message.reply_text(prompts.get(field, "Enter new value:"))


async def gardener_my_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id FROM gardeners WHERE user_id = ? LIMIT 1", (user_id,))
    grow = cur.fetchone()
    if not grow:
        conn.close()
        return
    gardener_id = grow[0]
    
    cur.execute("SELECT id, username, product, status, city FROM orders WHERE gardener_id = ? ORDER BY id DESC", (gardener_id,))
    orders = cur.fetchall()
    conn.close()
    
    if not orders:
        kb = [[InlineKeyboardButton("🔙 Back", callback_data="gardener_dashboard")]]
        await query.message.edit_text("No orders found for your products yet.", reply_markup=InlineKeyboardMarkup(kb))
        return

    lines = ["Orders for your products:"]
    for oid, usern, prod, status, city in orders:
        lines.append(f"📦 #{oid}: {prod} (@{usern}) • {status} • {city}")
    
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="gardener_dashboard")]]
    await query.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(keyboard))


async def user_orders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id, product, status, utr_no FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 10", (user_id,))
    orders = cur.fetchall()
    conn.close()
    
    if not orders:
        await query.message.edit_text("You haven't placed any orders yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="view_menu")]]))
        return
    
    text = "📜 *Your Recent Orders*\n\n"
    kb = []
    for oid, prod, status, utr in orders:
        status_emoji = "⏳" if status == "PENDING" else "✅" if status == "DELIVERED" else "📦" if status == "SHIPPED" else "❌"
        text += f"#{oid}: *{prod}* - {status_emoji} {status}\n"
        kb.append([InlineKeyboardButton(f"🔍 Track Order #{oid}", callback_data=f"track_order_{oid}")])
        
    kb.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="view_menu")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def gardener_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id, total_sales, avg_rating FROM gardeners WHERE user_id = ?", (user_id,))
    grow = cur.fetchone()
    if not grow:
        conn.close()
        await query.message.reply_text("Gardener record not found.")
        return
    gid, sales, rating = grow
    rating_val = rating if rating is not None else 0.0
    sales_val = sales if sales is not None else 0
    
    cur.execute("SELECT COUNT(1), SUM(price) FROM orders WHERE gardener_id = ? AND status = 'DELIVERED'", (gid,))
    delivered = cur.fetchone()
    order_count = delivered[0] or 0
    revenue = delivered[1] or 0
    conn.close()
    
    text = (
        f"📊 *Sales Analytics*\n\n"
        f"💰 *Total Revenue:* ₹{revenue}\n"
        f"📦 *Completed Orders:* {order_count}\n"
        f"⭐ *Average Rating:* {rating_val:.1f}\n"
        f"📈 *Total Sales Count:* {sales_val}\n"
    )
    kb = [[InlineKeyboardButton("🔙 Back", callback_data="gardener_dashboard")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def refer_earn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    bot_dn = (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_dn}?start=ref_{user_id}"
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    # ensure user exists
    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    cur.execute("SELECT referral_count, points FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    count = row[0] if row else 0
    points = row[1] if row else 0
    
    text = (
        f"🎁 *Refer & Earn*\n\n"
        f"Invite your friends and earn points for every purchase they make!\n\n"
        f"🔗 *Your Referral Link:* \n`{ref_link}`\n\n"
        f"👥 *Total Referrals:* {count}\n"
        f"💎 *Earned Points:* {points}\n\n"
        "Your friends must use your link to start the bot for you to get credit."
    )
    kb = [
        [InlineKeyboardButton("💎 Points Store", callback_data="points_store")],
        [InlineKeyboardButton("🔙 Back", callback_data="view_menu")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def leaderboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT username, total_sales, avg_rating FROM gardeners WHERE verified = 1 ORDER BY avg_rating DESC, total_sales DESC LIMIT 10")
    rows = cur.fetchall()
    conn.close()
    
    if not rows:
        await query.message.edit_text("No verified gardeners registered yet.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="view_menu")]]))
        return
        
    text = "🏆 *Gardener Leaderboard*\n\n"
    for rank, (uname, sales, rating) in enumerate(rows, 1):
        stars = "⭐" * int(round(rating)) if rating > 0 else "No ratings"
        text += f"{rank}. *@{uname}*\n   Rating: {rating:.1f} ({stars})\n   Completed Sales: {sales}\n\n"
        
    kb = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="view_menu")]]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def points_store_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id = ? LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    points = row[0] if row else 0
    
    text = (
        f"💎 *Referral Points Store* 💎\n\n"
        f"Use your points to get discount codes applied automatically on your next checkout!\n\n"
        f"Your current balance: *{points} Points*\n\n"
        f"🛒 *Available Rewards:*\n"
        f"1. ₹50 checkout discount ➔ Costs *100 Points*\n"
        f"2. ₹100 checkout discount ➔ Costs *180 Points*\n"
        f"3. ₹200 checkout discount ➔ Costs *300 Points*\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("🎫 Redeem ₹50 Off (100 pts)", callback_data="redeem_50")],
        [InlineKeyboardButton("🎫 Redeem ₹100 Off (180 pts)", callback_data="redeem_100")],
        [InlineKeyboardButton("🎫 Redeem ₹200 Off (300 pts)", callback_data="redeem_200")],
        [InlineKeyboardButton("🔙 Back to Referrals", callback_data="refer_earn")]
    ]
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")


async def redeem_points_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    
    amount = int(data.split("_")[-1])
    cost_map = {50: 100, 100: 180, 200: 300}
    cost = cost_map.get(amount, 999999)
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT points FROM users WHERE user_id = ? LIMIT 1", (user_id,))
    row = cur.fetchone()
    points = row[0] if row else 0
    
    if points < cost:
        conn.close()
        await query.answer("❌ Insufficient points balance!", show_alert=True)
        return
        
    # Deduct points safely
    cur.execute("UPDATE users SET points = points - ? WHERE user_id = ? AND points >= ?", (cost, user_id, cost))
    conn.commit()
    success = cur.rowcount > 0
    conn.close()
    
    if not success:
        await query.answer("❌ Transaction failed or insufficient points balance!", show_alert=True)
        return await points_store_callback(update, context)
        
    # Apply discount to session
    context.user_data['discount_amount'] = context.user_data.get('discount_amount', 0) + amount
    
    await query.answer("🎉 Reward redeemed successfully!", show_alert=True)
    # Reload points store
    return await points_store_callback(update, context)


async def track_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    order_id = int(query.data.split("_")[-1])
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT product, price, status, utr_no, address, admin_photo_file_id, confirmed_by_user, rating, review FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        await query.message.edit_text("Order not found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="user_orders")]]))
        return
        
    product, price, status, utr, address, photo, confirmed, rating, review = row
    
    # Build visual progress tracker
    step1 = "✅"
    step2 = "✅" if (photo or status == 'DELIVERED' or confirmed) else "⏳"
    step3 = "✅" if (photo or status == 'DELIVERED' or confirmed) else "⏳"
    step4 = "✅" if confirmed else "⏳"
    
    status_text = (
        f"🔍 *Order #{order_id} Tracking Panel*\n\n"
        f"🌿 *Item:* {product}\n"
        f"💰 *Price:* ₹{price}\n"
        f"🔢 *UTR:* `{utr}`\n"
        f"🏠 *Delivery Address:* {address}\n\n"
        f"*Delivery Status Steps:*\n"
        f"{step1} *1. Payment Recorded*\n"
        f"{step2} *2. Sourced & Packed*\n"
        f"{step3} *3. Dropped at Location* (Location & photo sent)\n"
        f"{step4} *4. Delivery Confirmed* (Finalized)\n\n"
    )
    
    if photo and not confirmed:
        status_text += "📦 *Your item has been dropped off!* Please check the coordinates and photo sent in this chat, then click the confirm button to close the order."
    elif confirmed:
        status_text += f"🎉 *Order Completed!* Thank you for your purchase.\n"
        if rating:
            status_text += f"   Rating: {'⭐' * rating}\n"
        if review:
            status_text += f"   Review: _{review}_\n"
            
    kb = []
    if photo and not confirmed:
        kb.append([InlineKeyboardButton("✅ Confirm Delivery Received", callback_data=f"confirm_delivery_{order_id}")])
    kb.append([InlineKeyboardButton("🎫 Raise Ticket for Order", callback_data=f"order_ticket_select_{order_id}")])
    kb.append([InlineKeyboardButton("🔙 Back to Orders", callback_data="user_orders")])
    
    await query.message.edit_text(status_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")


async def confirm_delivery_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Thank you for confirming!")
    order_id = int(query.data.split("_")[-1])
    user_id = query.from_user.id

    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT confirmed_by_user, gardener_id, product FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await query.message.edit_text("Order not found.")
        return
    
    confirmed, gardener_id, product = row
    if confirmed:
        await query.message.edit_text("✅ You have already confirmed this delivery. Thank you!")
        conn.close()
        return

    # Update safely to prevent double confirmation and duplicate points awarding
    cur.execute("UPDATE orders SET confirmed_by_user = 1 WHERE id = ? AND confirmed_by_user = 0", (order_id,))
    conn.commit()
    if cur.rowcount == 0:
        await query.message.edit_text("✅ You have already confirmed this delivery. Thank you!")
        conn.close()
        return

    # Award points to referrer (50 points)
    try:
        cur.execute("SELECT user_id FROM orders WHERE id = ? LIMIT 1", (order_id,))
        orow = cur.fetchone()
        if orow:
            buyer_id = orow[0]
            cur.execute("SELECT referred_by FROM users WHERE user_id = ? LIMIT 1", (buyer_id,))
            ref_row = cur.fetchone()
            if ref_row and ref_row[0]:
                referrer_id = ref_row[0]
                cur.execute("UPDATE users SET points = points + 50 WHERE user_id = ?", (referrer_id,))
                conn.commit()
                try:
                    await context.bot.send_message(
                        chat_id=referrer_id,
                        text=f"🎊 *You earned 50 points!* Someone you referred just completed a purchase."
                    )
                except Exception:
                    pass
    except Exception as e:
        logging.error(f"Error awarding referral points: {e}")
        
    conn.close()

    # Notify admin
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"✅ Customer confirmed delivery of Order #{order_id} ({product}).",
        )
    except Exception: pass

    # If gardener order, ask for rating
    if gardener_id:
        rating_kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("⭐", callback_data=f"rate_1_{order_id}"),
            InlineKeyboardButton("⭐⭐", callback_data=f"rate_2_{order_id}"),
            InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_3_{order_id}"),
            InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_4_{order_id}"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_5_{order_id}"),
        ]])
        await query.message.edit_text(
            "🎉 *Delivery confirmed!* Thank you!\n\nHow would you rate this gardener?",
            reply_markup=rating_kb,
            parse_mode="Markdown"
        )
    else:
        await query.message.edit_text("🎉 *Delivery confirmed!* Thank you for your purchase!", parse_mode="Markdown")


async def rate_gardener_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("Rating submitted!")
    parts = query.data.split("_")  # rate_N_orderid
    stars = int(parts[1])
    order_id = int(parts[2])

    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT gardener_id, rating FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        await query.message.edit_text("Order not found.")
        return
    
    gardener_id, existing_rating = row
    if existing_rating:
        await query.message.edit_text("You have already rated this order. Thank you!")
        conn.close()
        return

    # Save rating on order safely
    cur.execute("UPDATE orders SET rating = ? WHERE id = ? AND rating IS NULL", (stars, order_id))
    conn.commit()
    if cur.rowcount == 0:
        await query.message.edit_text("You have already rated this order. Thank you!")
        conn.close()
        return
        
    # Recalculate gardener avg rating and total_sales
    cur.execute("SELECT AVG(rating), COUNT(rating) FROM orders WHERE gardener_id = ? AND rating IS NOT NULL", (gardener_id,))
    result = cur.fetchone()
    new_avg = result[0] or 0
    total_sales = result[1] or 0
    cur.execute("UPDATE gardeners SET avg_rating = ?, total_sales = ? WHERE id = ?", (round(new_avg, 2), total_sales, gardener_id))
    conn.commit()
    conn.close()

    # Ask for review feedback
    context.user_data['expecting_review_order_id'] = order_id
    
    stars_text = "⭐" * stars
    await query.message.edit_text(
        f"{stars_text} *Thanks for rating!*\n\n"
        f"Would you like to write a quick text review/feedback for this gardener? "
        f"Reply directly to this message with your review (or type 'cancel' to skip):",
        parse_mode="Markdown"
    )


async def handle_user_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    order_id = context.user_data.pop('expecting_review_order_id', None)
    if not order_id:
        return
    if not update.message or not update.message.text:
        if update.message:
            await update.message.reply_text("Review skipped. Thank you!")
        return
    review_text = update.message.text.strip()
    if review_text.lower() == 'cancel':
        await update.message.reply_text("Review skipped. Thank you!")
        return
        
    try:
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("UPDATE orders SET review = ? WHERE id = ?", (review_text, order_id))
        conn.commit()
        conn.close()
        await update.message.reply_text("✅ Thank you for your review/feedback!")
    except Exception as e:
        logging.error(f"Error saving review: {e}")
        await update.message.reply_text("Thank you for your feedback!")


async def state_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    state = query.data[len("state_type_"):]
    context.user_data['state_type'] = state
    await query.message.reply_text(f"Please type your city name in {state}:")
    return ConversationHandler.END


async def state_city_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This handler intercepts text when user is searching/typing a city for a previously selected state
    if not update.message or not update.message.text:
        return ConversationHandler.END

    if context.user_data.get('state_search'):
        state = context.user_data.pop('state_search')
        q = update.message.text.strip().lower()
        logging.info(f"state_city_text_handler: user={update.effective_user.id if update.effective_user else 'unknown'} state={state} query={q}")
        # search catalog and locations for matching cities
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        # search locations table first
        cur.execute("SELECT name FROM locations WHERE lower(name) LIKE ? ORDER BY name LIMIT 20", (f"%{q}%",))
        locs = [r[0] for r in cur.fetchall()]
        # also include cities present in catalog
        cur.execute("SELECT DISTINCT city FROM catalog WHERE lower(city) LIKE ? ORDER BY city LIMIT 20", (f"%{q}%",))
        cat_cities = [r[0] for r in cur.fetchall()]
        conn.close()
        candidates = sorted(list(dict.fromkeys(locs + cat_cities)))
        logging.info(f"search candidates: {candidates}")
        if not candidates:
            await update.message.reply_text(f"No cities found matching '{q}' — try typing a different substring or type your city exactly.")
            return ConversationHandler.END
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(c, callback_data=f"city_{c}")] for c in candidates[:20]])
        await update.message.reply_text("Select your city from results:", reply_markup=kb)
        return ConversationHandler.END

    if context.user_data.get('state_type'):
        state = context.user_data.pop('state_type')
        city = update.message.text.strip()
        # proceed to show products for this city
        await send_products_for_city(update, context, city)
        return ConversationHandler.END

    return ConversationHandler.END


async def send_products_for_city(update_or_chat, context: ContextTypes.DEFAULT_TYPE, city: str) -> None:
    # update context.user_data['city'] and show products
    # update_or_chat may be Update or chat id; handle both
    try:
        if isinstance(update_or_chat, Update):
            chat_id = update_or_chat.effective_chat.id
            ud = update_or_chat
        else:
            chat_id = int(update_or_chat)
            ud = None
    except Exception:
        chat_id = None
        ud = None

    catalog = load_catalog_from_db()
    items = catalog.get(city, [])
    if not items:
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=f"No products found for {city}. Try a different city or go back to the menu.")
        return
    keyboard = []
    for idx, item in enumerate(items):
        qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
        keyboard.append([InlineKeyboardButton(f"{item['name']}{qty_str} (₹{item['price']})", callback_data=f"prod_{idx}")])
    if ud and ud.callback_query:
        try:
            await ud.callback_query.message.edit_text(f"📍 Stock available in **{city}**:\nSelect a plant to purchase:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
        except Exception:
            await context.bot.send_message(chat_id=ud.effective_chat.id, text=f"📍 Stock available in {city}:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        if chat_id:
            await context.bot.send_message(chat_id=chat_id, text=f"📍 Stock available in {city}:", reply_markup=InlineKeyboardMarkup(keyboard))
    # set the city in user_data if update provided
    if ud:
        ud.user_data['city'] = city
    return

async def order_expiry_background_loop(application: Application) -> None:
    """Background task running indefinitely to check and expire orders."""
    while True:
        try:
            await asyncio.sleep(60)
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            now_str = datetime.datetime.now().isoformat()
            cur.execute("SELECT id, user_id FROM orders WHERE status = 'PENDING' AND expires_at IS NOT NULL AND expires_at < ?", (now_str,))
            expired_orders = cur.fetchall()
            for oid, uid in expired_orders:
                cur.execute("UPDATE orders SET status = 'EXPIRED' WHERE id = ?", (oid,))
                conn.commit()
                try:
                    await application.bot.send_message(
                        chat_id=uid,
                        text=f"⏰ *Order #{oid} has expired.*\n\nYour payment was not verified within 30 minutes. Please place a new order. Contact admin if you believe this is an error.",
                        parse_mode="Markdown"
                    )
                    await application.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=f"⚠️ Order #{oid} auto-expired (unverified payment)."
                    )
                except Exception as e:
                    logging.error(f"Failed to send expiry notice for Order #{oid}: {e}")
            conn.close()
        except Exception as e:
            logging.error(f"Error in order_expiry_background_loop: {e}")

async def post_init(application: Application) -> None:
    # Start background loop (disabled as per request)
    # asyncio.create_task(order_expiry_background_loop(application))
    pass

async def expire_order(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job callback: auto-cancel an order if not confirmed within 30 minutes."""
    data = context.job.data
    order_id = data.get('order_id')
    user_id = data.get('user_id')
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT status FROM orders WHERE id = ?", (order_id,))
    row = cur.fetchone()
    if row and row[0] == 'PENDING':
        cur.execute("UPDATE orders SET status = 'EXPIRED' WHERE id = ?", (order_id,))
        conn.commit()
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⏰ *Order #{order_id} has expired.*\n\nYour payment was not verified within 30 minutes. Please place a new order with a valid UTR. Contact admin if you believe this is an error.",
                parse_mode="Markdown"
            )
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"⚠️ Order #{order_id} auto-expired (unverified payment)."
            )
        except Exception as e:
            logging.error(f"Error sending expiry notice: {e}")
    conn.close()


async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        if update.message:
            await update.message.reply_text("⚠️ Please send a text message containing your 12-digit UTR/UPI Ref number.")
        return PROCESS_PAYMENT
    utr_no = update.message.text.strip()
    try:
        chat_id = update.effective_chat.id if update.effective_chat else 'unknown'
        user_id = update.effective_user.id if update.effective_user else 'unknown'
        logging.info(f"Received UTR message from chat={chat_id} user={user_id}: {utr_no}")
    except Exception:
        logging.info("Received UTR message (could not extract chat/user)")
    # Only process if the user was expected to send UTR
    if not context.user_data.get('expecting_utr'):
        logging.info("UTR message received but not expected; ignoring.")
        return ConversationHandler.END
    # ensure cart exists and has items
    cart = context.user_data.get('cart', [])
    if not cart:
        await update.message.reply_text("Your cart is empty. Please start again with /start.")
        context.user_data.pop('expecting_utr', None)
        return ConversationHandler.END
    if not utr_no.isdigit() or len(utr_no) != 12:
        await update.message.reply_text("⚠️ Invalid format. A standard UPI Ref/UTR number must be exactly 12 numerical digits. Please recheck your app receipt and try typing it again:")
        return PROCESS_PAYMENT

    # 🚨 UTR Duplicate Detection
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute("SELECT id FROM orders WHERE (utr_no = ? OR utr_no LIKE ? || '_%') AND status != 'EXPIRED'", (utr_no, utr_no))
    dupe = cur.fetchone()
    conn.close()
    if dupe:
        await update.message.reply_text(
            f"🚫 *Duplicate UTR detected!*\n\nThis UTR number has already been used for Order #{dupe[0]}. "
            f"Please use a unique UTR from your current payment.\n\nIf you believe this is an error, contact admin.",
            parse_mode="Markdown"
        )
        return PROCESS_PAYMENT

    context.user_data['utr_no'] = utr_no
    # flip flags: stop expecting UTR, now expect address
    context.user_data.pop('expecting_utr', None)
    context.user_data['expecting_address'] = True
    await update.message.reply_text("✅ UTR Number recorded! Final Step: Please reply with your delivery address where you want this delivered.")
    return ConversationHandler.END

async def get_address_and_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Only accept address if expected
    if not context.user_data.get('expecting_address'):
        logging.info("Address message received but not expected; ignoring.")
        return ConversationHandler.END
    if not update.message or not update.message.text:
        if update.message:
            await update.message.reply_text("⚠️ Please send a text message containing your delivery address.")
        return GET_ADDRESS
    address = update.message.text.strip()
    # ensure we have required previous data
    cart = context.user_data.get('cart', [])
    if not context.user_data.get('utr_no') or not cart:
        await update.message.reply_text("No pending order found. Please start again with /start.")
        context.user_data.pop('expecting_address', None)
        return ConversationHandler.END
    user = update.message.from_user
    city = context.user_data.get('city')
    utr_no = context.user_data['utr_no']
    delivery_method = context.user_data.get('delivery_method')
    delivery_slot = context.user_data.get('delivery_slot', 'Standard Slot')
    
    delivery_label = "📦 Anonymous Drop-off (+₹99)" if delivery_method == 'anonymous' else "👤 Contact Seller Directly"
    address_with_delivery = f"{address}\n(Delivery: {delivery_label}, Slot: {delivery_slot})"
    
    expires_at = None

    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    
    order_ids = []
    
    # 1. Insert cart items (separate row for each unit ordered)
    for index, item in enumerate(cart):
        qty = item.get('cart_quantity', 1)
        for u in range(qty):
            item_suffix = f"{index}_{u}" if u > 0 else f"{index}"
            item_utr = utr_no if (index == 0 and u == 0) else f"{utr_no}_{item_suffix}"
            cursor.execute(
                "INSERT INTO orders (user_id, username, city, product, price, utr_no, address, gardener_id, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user.id, user.username or user.first_name, city, item['name'], item['price'], item_utr, address_with_delivery, item.get('gardener_id'), expires_at)
            )
            order_ids.append(cursor.lastrowid)
        
    # 2. Insert anonymous drop-off fee if selected
    if delivery_method == 'anonymous':
        fee_utr = f"{utr_no}_fee"
        cursor.execute(
            "INSERT INTO orders (user_id, username, city, product, price, utr_no, address, gardener_id, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user.id, user.username or user.first_name, city, "Anonymous Drop-off Fee", 99, fee_utr, address_with_delivery, None, expires_at)
        )
        order_ids.append(cursor.lastrowid)
        
    conn.commit()
    conn.close()
    
    subtotal = sum(item['price'] * item.get('cart_quantity', 1) for item in cart)
    delivery_fee = 99 if delivery_method == 'anonymous' else 0
    discount = context.user_data.get('discount_amount', 0)
    total_due = subtotal + delivery_fee - discount
    if total_due < 0:
        total_due = 0
    
    # Success message for customer
    user_items_desc = []
    inserted_idx = 0
    for item in cart:
        qty = item.get('cart_quantity', 1)
        qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
        item_order_ids = [str(order_ids[inserted_idx + u]) for u in range(qty)]
        inserted_idx += qty
        user_items_desc.append(f"• *{item['name']}*{qty_str} - {qty}x (₹{item['price']}) (Orders #{', '.join(item_order_ids)})")
        
    user_items_text = "\n".join(user_items_desc)
    
    success_msg = (
        f"🎉 *Order Placed Successfully!*\n\n"
        f"📋 *Items:* \n{user_items_text}\n"
    )
    if delivery_method == 'anonymous':
        success_msg += f"• *Anonymous Drop-off Fee* - ₹99 (Order #{order_ids[-1]})\n"
    if discount > 0:
        success_msg += f"💎 *Discount Applied:* -₹{discount}\n"
        
    success_msg += (
        f"\n💰 *Total Paid:* ₹{total_due}\n"
        f"🔢 *UTR:* `{utr_no}`\n\n"
        f"We will drop off the products near your delivery location and send you the exact location pin and photo."
    )
    
    # Construct gardener contact buttons if "Contact Seller Directly" selected
    contact_buttons = []
    if delivery_method == 'direct':
        unique_gids = list(set(item.get('gardener_id') for item in cart if item.get('gardener_id')))
        if unique_gids:
            try:
                conn = sqlite3.connect('store.db')
                cur = conn.cursor()
                placeholders = ",".join(["?"] * len(unique_gids))
                cur.execute(f"SELECT id, username, user_id FROM gardeners WHERE id IN ({placeholders})", unique_gids)
                g_rows = cur.fetchall()
                conn.close()
                
                for g_id, g_username, g_user_id in g_rows:
                    if g_username:
                        contact_buttons.append([InlineKeyboardButton(f"💬 Contact Seller @{g_username}", url=f"https://t.me/{g_username}")])
                    elif g_user_id:
                        contact_buttons.append([InlineKeyboardButton("💬 Contact Seller", url=f"tg://user?id={g_user_id}")])
            except Exception as e:
                logging.error(f"Error fetching gardener contact info: {e}")
                
        if not contact_buttons:
            admin_username = os.environ.get("ADMIN_USERNAME")
            if admin_username:
                contact_buttons.append([InlineKeyboardButton("💬 Contact Admin", url=f"https://t.me/{admin_username}")])
            else:
                contact_buttons.append([InlineKeyboardButton("💬 Contact Admin", url=f"tg://user?id={ADMIN_CHAT_ID}")])
                
        success_msg += "\n\n📞 *You chose Direct Contact.* Please click the button(s) below to contact the seller(s) directly and coordinate delivery."
        
    reply_markup = InlineKeyboardMarkup(contact_buttons) if contact_buttons else None
    
    await update.message.reply_text(
        success_msg,
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )
    
    # Construct consolidated notification for Admin
    admin_items_desc = []
    inserted_idx = 0
    for item in cart:
        qty = item.get('cart_quantity', 1)
        qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
        item_order_ids = [str(order_ids[inserted_idx + u]) for u in range(qty)]
        inserted_idx += qty
        admin_items_desc.append(f"• *{item['name']}*{qty_str} - {qty}x (₹{item['price']}) [Orders: {', '.join(item_order_ids)}]")
        
    if delivery_method == 'anonymous':
        fee_order_id = order_ids[-1]
        admin_items_desc.append(f"• *Anonymous Drop-off Fee* (₹99) - Order #{fee_order_id} [To close: `/deliver {fee_order_id}`]")
        
    admin_text = (
        f"🚨 *NEW UPI ORDER GROUP* 🚨\n\n"
        f"👤 *Customer:* @{user.username or user.first_name}\n"
        f"📍 *City:* {city}\n"
        f"🚚 *Delivery Method:* {delivery_label}\n"
        f"⏰ *Delivery Slot:* {delivery_slot}\n"
        f"🔢 *UPI UTR:* `{utr_no}`\n"
        f"🏠 *Address:* {address}\n\n"
        f"📋 *Items Ordered:*\n" + "\n".join(admin_items_desc) + "\n\n"
        f"💰 *Total Paid:* ₹{total_due}"
    )
    
    try:
        await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
    except Exception as e:
        logging.error(f"Failed to send admin notification: {e}")
        
    # Group ordered items by gardener and notify them
    gardener_items = {}
    inserted_idx = 0
    for item in cart:
        qty = item.get('cart_quantity', 1)
        gid = item.get('gardener_id')
        if gid:
            if gid not in gardener_items:
                gardener_items[gid] = []
            for u in range(qty):
                gardener_items[gid].append((order_ids[inserted_idx + u], item))
        inserted_idx += qty
            
    for gid, items_list in gardener_items.items():
        try:
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT user_id, username FROM gardeners WHERE id = ? LIMIT 1", (gid,))
            grow = cur.fetchone()
            conn.close()
            if grow and grow[0]:
                gardener_chat_id = grow[0]
                
                g_items_desc = []
                for o_id, item in items_list:
                    qty_str = f" ({item['quantity']}g)" if item.get('quantity') is not None else ""
                    g_items_desc.append(f"• *{item['name']}*{qty_str} (₹{item['price']}) - Order #{o_id} [To close: `/deliver {o_id}`]")
                
                gardener_text = (
                    f"🚨 *NEW UPI ORDER (Your Items)* 🚨\n\n"
                    f"👤 *Customer:* @{user.username or user.first_name}\n"
                    f"📍 *City:* {city}\n"
                    f"🚚 *Delivery Method:* {delivery_label}\n"
                    f"⏰ *Delivery Slot:* {delivery_slot}\n"
                    f"🔢 *UPI UTR:* `{utr_no}`\n"
                    f"🏠 *Address:* {address}\n\n"
                    f"📋 *Your Items:*\n" + "\n".join(g_items_desc)
                )
                await context.bot.send_message(chat_id=gardener_chat_id, text=gardener_text)
        except Exception as e:
            logging.error(f"Failed to notify gardener ID {gid}: {e}")
            
    # clear per-user flags
    context.user_data.pop('expecting_address', None)
    context.user_data.pop('utr_no', None)
    context.user_data.pop('product_name', None)
    context.user_data.pop('price', None)
    context.user_data.pop('city', None)
    context.user_data.pop('gardener_source', None)
    context.user_data.pop('cart', None)
    context.user_data.pop('delivery_method', None)
    context.user_data.pop('delivery_slot', None)
    context.user_data.pop('total_due', None)
    context.user_data.pop('discount_amount', None)
    
    return ConversationHandler.END


async def message_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # route free-form text messages based on per-user flags
    if context.user_data.get('expecting_utr'):
        return await process_payment(update, context)
    if context.user_data.get('expecting_address'):
        return await get_address_and_finalize(update, context)
    return ConversationHandler.END


async def handle_support_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    user_id = user.id
    username = user.username or user.first_name
    
    # Check if user typed cancel/exit
    text_val = update.message.text.strip().lower() if update.message.text else ""
    if text_val in ('cancel', 'exit', '/cancel'):
        context.user_data.pop('expecting_support_msg', None)
        await update.message.reply_text("❌ Live support chat ended.")
        await view_menu_callback(update, context)
        return
        
    try:
        # Forward the user's message to the Admin
        forwarded_msg = await context.bot.forward_message(
            chat_id=ADMIN_CHAT_ID,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        
        # Send a context metadata message to the Admin
        info_text = (
            f"📨 *Support Message from:* @{username} (ID: `{user_id}`)\n"
            f"👉 *Reply directly* to the forwarded message above to answer."
        )
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=info_text,
            parse_mode="Markdown"
        )
        
        # Log to DB
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO support_tickets (user_id, username, admin_message_id, user_message_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, forwarded_msg.message_id, update.message.message_id, datetime.datetime.now().isoformat())
        )
        conn.commit()
        conn.close()
        
        await update.message.reply_text(
            "✉️ *Message forwarded to Live Support.*\n"
            "Please wait, the admin will reply directly in this chat.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error handling support message: {e}")
        await update.message.reply_text("⚠️ Failed to send message to support. Please try again later.")


async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or update.message.chat_id != ADMIN_CHAT_ID:
        return
    if not update.message.reply_to_message:
        return
        
    reply_to = update.message.reply_to_message
    
    conn = sqlite3.connect('store.db')
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, status FROM support_tickets WHERE admin_message_id = ? LIMIT 1",
        (reply_to.message_id,)
    )
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return
        
    user_id, status = row
    
    # Check if admin closed the ticket
    text_val = update.message.text.strip().lower() if update.message.text else ""
    if text_val in ('/close', 'close'):
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        cur.execute("UPDATE support_tickets SET status = 'CLOSED' WHERE admin_message_id = ?", (reply_to.message_id,))
        conn.commit()
        conn.close()
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="🔒 *Live support chat has been closed by the admin.*"
            )
            # clear flag in user application user_data context
            if user_id in context.application.user_data:
                context.application.user_data[user_id].pop('expecting_support_msg', None)
        except Exception:
            pass
            
        await update.message.reply_text(f"✅ Ticket closed for user {user_id}.")
        return

    # Forward reply back to user via copy_message
    try:
        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=ADMIN_CHAT_ID,
            message_id=update.message.message_id
        )
        await update.message.reply_text("✅ Reply forwarded to user.")
    except Exception as e:
        logging.error(f"Error forwarding reply to user {user_id}: {e}")
        await update.message.reply_text("⚠️ Failed to send reply to user.")


async def unified_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id if update.effective_user else None
    if not user_id:
        return ConversationHandler.END

    # 0. Support Ticket System Routing
    if context.user_data.get('expecting_support_msg'):
        await handle_support_message(update, context)
        return ConversationHandler.END

    # 0a. User Order Review routing
    if context.user_data.get('expecting_review_order_id'):
        await handle_user_review(update, context)
        return ConversationHandler.END

    # 1. Admin Broadcast
    if user_id == ADMIN_CHAT_ID and context.user_data.get('awaiting_admin_broadcast'):
        await admin_text_router(update, context)
        return ConversationHandler.END

    # 2. Admin Receive Catalog Edit
    if user_id == ADMIN_CHAT_ID and context.user_data.get('editing_catalog_id'):
        await admin_receive_catalog_edit(update, context)
        return ConversationHandler.END

    # 3. Gardener Registration & Profile & Sell Flow
    gardener_flags = [
        'expecting_gardener_phone', 'expecting_gardener_alias',
        'expecting_gardener_items', 'expecting_gardener_location',
        'expecting_gardener_upi', 'editing_gardener_field', 'sell_stage',
        'expecting_custom_state', 'expecting_custom_city'
    ]
    if any(context.user_data.get(flag) for flag in gardener_flags):
        await gardener_text_router(update, context)
        return ConversationHandler.END

    # 4. State/City Search & Type flows
    if context.user_data.get('state_search') or context.user_data.get('state_type'):
        await state_city_text_handler(update, context)
        return ConversationHandler.END

    # 5. User Order Flows (UTR & Address)
    if context.user_data.get('expecting_utr') or context.user_data.get('expecting_address'):
        return await message_router(update, context)

    return ConversationHandler.END

# --- ADMIN ROUTING ---
async def admin_backup_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    if user_id != ADMIN_CHAT_ID:
        await update.message.reply_text("Unauthorized.")
        return
        
    db_file = os.path.join(DB_DIR, 'store.db')
    if not os.path.exists(db_file):
        db_file = 'store.db'
        if not os.path.exists(db_file):
            await update.message.reply_text("❌ Database file not found!")
            return
            
    try:
        await update.message.reply_text("⏳ Generating database backup...")
        with open(db_file, 'rb') as f:
            await context.bot.send_document(
                chat_id=ADMIN_CHAT_ID,
                document=f,
                filename=f"backup_store_{datetime.date.today().isoformat()}.db",
                caption="📦 Here is your database backup file."
            )
    except Exception as e:
        logging.error(f"Error in backup command: {e}")
        await update.message.reply_text(f"❌ Failed to send backup file: {e}")


async def admin_deliver_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Allow admin or verified gardener for their own products
    user_id = update.effective_user.id
    if not context.args: return ConversationHandler.END
    order_id = context.args[0]
    
    is_authorized = False
    if user_id == ADMIN_CHAT_ID:
        is_authorized = True
    else:
        try:
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT gardener_id FROM orders WHERE id = ?", (order_id,))
            row = cur.fetchone()
            if row and row[0]:
                gardener_id = row[0]
                cur.execute("SELECT user_id, verified FROM gardeners WHERE id = ?", (gardener_id,))
                gr = cur.fetchone()
                if gr and gr[0] == user_id and gr[1] == 1:
                    is_authorized = True
            conn.close()
        except Exception:
            pass
            
    if not is_authorized:
        await update.message.reply_text("You are not authorized to deliver this order.")
        return ConversationHandler.END
        
    context.user_data['admin_order_id'] = order_id
    await update.message.reply_text(f"📸 Please upload the delivery proof photo for Order #{order_id}:")
    return ADMIN_AWAITING_PHOTO


async def admin_process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data.get('admin_order_id')
    if not order_id:
        await update.message.reply_text("No order selected for delivery.")
        return ConversationHandler.END
        
    sender_id = update.effective_user.id
    
    # same auth check as command
    is_authorized = False
    if sender_id == ADMIN_CHAT_ID:
        is_authorized = True
    else:
        try:
            conn = sqlite3.connect('store.db')
            cur = conn.cursor()
            cur.execute("SELECT gardener_id FROM orders WHERE id = ?", (order_id,))
            row = cur.fetchone()
            if row and row[0]:
                gardener_id = row[0]
                cur.execute("SELECT user_id, verified FROM gardeners WHERE id = ?", (gardener_id,))
                gr = cur.fetchone()
                if gr and gr[0] == sender_id and gr[1] == 1:
                    is_authorized = True
            conn.close()
        except Exception:
            pass
    
    if not is_authorized:
        await update.message.reply_text("Unauthorized.")
        return ConversationHandler.END

    photo_file_id = update.message.photo[-1].file_id
    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, product, address, gardener_id FROM orders WHERE id = ?", (order_id,))
    result = cursor.fetchone()
    if result:
        user_id, product, address, gardener_id = result
        is_anonymous = "Anonymous Drop-off" in (address or "")
        if not is_anonymous:
            # Direct Contact delivery: immediately set status = 'DELIVERED'
            cursor.execute("UPDATE orders SET admin_photo_file_id = ?, awaiting_admin_location = 0, status = 'DELIVERED' WHERE id = ?", (photo_file_id, order_id))
            conn.commit()
            
            sender_label = "Admin" if sender_id == ADMIN_CHAT_ID else "Gardener"
            try:
                await send_user_photo(context, user_id, photo_file_id, caption=f"🌿 Your order for *{product}* has been delivered/sent!", sender=sender_label)
                
                # Escrow: Ask user to confirm delivery
                buttons = [
                    [InlineKeyboardButton("✅ Confirm Delivery Received", callback_data=f"confirm_delivery_{order_id}")]
                ]
                if gardener_id:
                    gardener_username = None
                    gardener_chat_id = None
                    cursor.execute("SELECT username, user_id FROM gardeners WHERE id = ?", (gardener_id,))
                    grow = cursor.fetchone()
                    if grow:
                        gardener_username = grow[0]
                        gardener_chat_id = grow[1]
                    
                    if gardener_username:
                        buttons.append([InlineKeyboardButton(f"❌ Not received? Contact {gardener_username}", url=f"https://t.me/{gardener_username}")])
                    elif gardener_chat_id:
                        buttons.append([InlineKeyboardButton("❌ Not received? Contact Gardener", url=f"tg://user?id={gardener_chat_id}")])
                else:
                    admin_username = os.environ.get("ADMIN_USERNAME")
                    if admin_username:
                        buttons.append([InlineKeyboardButton("❌ Not received? Contact Admin", url=f"https://t.me/{admin_username}")])
                    else:
                        buttons.append([InlineKeyboardButton("❌ Not received? Contact Admin", url=f"tg://user?id={ADMIN_CHAT_ID}")])
                        
                confirm_kb = InlineKeyboardMarkup(buttons)
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"📦 *Order #{order_id} has been delivered!*\n\nPlease confirm if you have received your item.",
                    reply_markup=confirm_kb,
                    parse_mode="Markdown"
                )
                await update.message.reply_text(f"Photo and delivery confirmation request sent to user for Order #{order_id}.")
                
                # notify both admin and actual sender
                notif_text = f"✅ Delivered Order #{order_id}; photo forwarded to user."
                await context.bot.send_message(chat_id=sender_id, text=notif_text)
                if sender_id != ADMIN_CHAT_ID:
                    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🔔 Gardener delivered Order #{order_id}.")
                context.user_data.pop('admin_order_id', None)
            except Exception as e:
                logging.error(f"Error in sending direct contact delivery photo/message to user: {e}")
        else:
            # Anonymous Drop-off delivery: store photo and request location
            cursor.execute("UPDATE orders SET admin_photo_file_id = ?, awaiting_admin_location = 1 WHERE id = ?", (photo_file_id, order_id))
            conn.commit()
            # ask for location
            kb = ReplyKeyboardMarkup([[KeyboardButton("Share Drop-off Location", request_location=True)]], one_time_keyboard=True, resize_keyboard=True)
            try:
                await update.message.reply_text(f"Photo received for Order #{order_id}. Please now send the drop-off location (press the button):", reply_markup=kb)
            except Exception:
                await update.message.reply_text(f"Photo received for Order #{order_id}. Please now send the drop-off location.")
    conn.close()
    return ConversationHandler.END

# --- FLET DASHBOARD UI ---
def main_dashboard(page: ft.Page):
    page.title = "The Green Oasis - Admin Dashboard"
    page.theme_mode = ft.ThemeMode.DARK
    page.padding = 30
    
    title_row = ft.Row([ft.Text("🌿 Plant Shop Order Dashboard", style=ft.TextThemeStyle.HEADLINE_MEDIUM, color=ft.Colors.GREEN_400)], alignment=ft.MainAxisAlignment.CENTER)
    
    orders_table = ft.DataTable(
        columns=[
            ft.DataColumn(ft.Text("ID")),
            ft.DataColumn(ft.Text("Customer")),
            ft.DataColumn(ft.Text("City")),
            ft.DataColumn(ft.Text("Product")),
            ft.DataColumn(ft.Text("UTR Number")),
            ft.DataColumn(ft.Text("Delivery Address")),
            ft.DataColumn(ft.Text("Status")),
        ],
        rows=[]
    )

    def refresh_data(e=None):
        orders_table.rows.clear()
        conn = sqlite3.connect('store.db')
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, city, product, utr_no, address, status FROM orders ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        
        def approve_order(e, order_id):
            try:
                conn2 = sqlite3.connect('store.db')
                cur2 = conn2.cursor()
                cur2.execute("UPDATE orders SET status = 'DELIVERED' WHERE id = ?", (order_id,))
                conn2.commit()
                conn2.close()
                # Send order status update notification to customer
                try:
                    conn_temp = sqlite3.connect('store.db')
                    cur_temp = conn_temp.cursor()
                    cur_temp.execute("SELECT user_id, product FROM orders WHERE id = ?", (order_id,))
                    t_row = cur_temp.fetchone()
                    conn_temp.close()
                    if t_row:
                        uid, prod = t_row
                        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
                        payload = {
                            "chat_id": uid,
                            "text": f"📦 *Order Status Update*\n\nYour order *#{order_id}* for *{prod}* has been marked as *DELIVERED*! Thank you.",
                            "parse_mode": "Markdown"
                        }
                        requests.post(url, json=payload, timeout=5)
                except Exception as notify_err:
                    print('DEBUG: approve_order notify failed', notify_err)
            except Exception as ex:
                print('DEBUG: approve_order failed', ex)
            try:
                refresh_data()
            except:
                pass

        # upload proof dialog state
        proof_dialog = None
        pending_proof_order = {"id": None}

        def send_photo_via_http(user_id, file_path, caption):
            try:
                url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
                with open(file_path, "rb") as f:
                    files = {"photo": f}
                    data = {"chat_id": user_id, "caption": caption, "parse_mode": "Markdown"}
                    resp = requests.post(url, files=files, data=data, timeout=15)
                return resp.ok, resp.text
            except Exception as e:
                return False, str(e)

        def open_proof_dialog(order_id):
            pending_proof_order["id"] = order_id
            nonlocal proof_dialog
            file_field = ft.TextField(label="Local image file path or URL")
            status_text = ft.Text("")

            def submit_proof(ev):
                path = file_field.value.strip()
                if not path:
                    status_text.value = "Please provide a file path."
                    page.update()
                    return
                # fetch user id and product
                conn3 = sqlite3.connect('store.db')
                cur3 = conn3.cursor()
                cur3.execute("SELECT user_id, product FROM orders WHERE id = ?", (order_id,))
                res = cur3.fetchone()
                conn3.close()
                if not res:
                    status_text.value = "Order not found."
                    page.update()
                    return
                user_id, product = res
                caption = f"🌿 Your order for *{product}* has been delivered. Proof attached."

                ok, info = send_photo_via_http(user_id, path, caption)
                if ok:
                    # mark delivered
                    try:
                        conn4 = sqlite3.connect('store.db')
                        cur4 = conn4.cursor()
                        cur4.execute("UPDATE orders SET status = 'DELIVERED' WHERE id = ?", (order_id,))
                        conn4.commit()
                        conn4.close()
                    except Exception as ex:
                        print('DEBUG: mark delivered failed', ex)
                    status_text.value = "Proof sent to user."
                    proof_dialog.open = False
                    try:
                        refresh_data()
                    except:
                        pass
                else:
                    status_text.value = f"Failed to send: {info}"
                page.update()

            proof_dialog = ft.AlertDialog(
                title=ft.Text(f"Upload delivery proof for Order #{order_id}"),
                content=ft.Column([file_field, status_text], tight=True),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda e: setattr(proof_dialog, 'open', False)),
                    ft.ElevatedButton("Upload & Send", on_click=submit_proof, bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE)
                ]
            )
            page.overlay.append(proof_dialog)
            proof_dialog.open = True
            page.update()

        for r in rows:
            status_color = ft.Colors.GREEN if r[6] == 'DELIVERED' else ft.Colors.AMBER
            address_text = r[5] or ""
            orders_table.rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(r[0]))),
                        ft.DataCell(ft.Text(f"@{r[1]}")),
                        ft.DataCell(ft.Text(r[2])),
                        ft.DataCell(ft.Text(r[3])),
                        ft.DataCell(ft.Text(r[4], color=ft.Colors.BLUE_200, weight=ft.FontWeight.BOLD)),
                        ft.DataCell(ft.Text(address_text, max_lines=3)),
                        ft.DataCell(ft.Row([
                            ft.Text(r[6], color=status_color, weight=ft.FontWeight.BOLD),
                            ft.ElevatedButton("Approve", on_click=lambda e, oid=r[0]: approve_order(e, oid), bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE),
                            ft.ElevatedButton("Upload Proof", on_click=lambda e, oid=r[0]: open_proof_dialog(oid), bgcolor=ft.Colors.BLUE_700, color=ft.Colors.WHITE)
                        ]))
                    ]
                )
            )
        try:
            page.update()
        except:
            pass

    refresh_btn = ft.ElevatedButton("🔄 Refresh Orders Table", on_click=refresh_data, icon=ft.Icons.REFRESH, bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE)
    
    page.add(
        title_row,
        ft.Divider(height=20, color=ft.Colors.ON_SURFACE_VARIANT),
        ft.Row([refresh_btn], alignment=ft.MainAxisAlignment.END),
        ft.ListView([orders_table], expand=True, spacing=10)
    )
    
    refresh_data()

def run_flet_dashboard():
    # Launches Flet application inside this tracking context thread
    ft.app(target=main_dashboard)

if __name__ == '__main__':
    init_db()
    # 1. Optionally spin up Dashboard UI window loop into an isolated background worker thread
    # In production/container environments set DISABLE_DASHBOARD=1 to avoid launching Flet GUI.
    disable_dashboard = os.environ.get("DISABLE_DASHBOARD", "0") in ("1", "true", "True")
    if not disable_dashboard:
        threading.Thread(target=run_flet_dashboard, daemon=True).start()
    
    # 2. Run the heavy structural Telegram bot loop directly inside the main thread execution context
    print("Bot is up and running...")
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, block_check_handler), group=-1)
    
    user_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_CITY: [CallbackQueryHandler(city_selected, pattern="^city_")],
            SELECT_PRODUCT: [
                CallbackQueryHandler(product_selected, pattern="^prod_"),
                CallbackQueryHandler(checkout_start_callback, pattern="^checkout_start$"),
                CallbackQueryHandler(clear_cart_callback, pattern="^clear_cart$"),
                CallbackQueryHandler(view_menu_callback, pattern="^view_menu$"),
                CallbackQueryHandler(clear_add_callback, pattern="^clearadd_"),
                CallbackQueryHandler(show_cart_callback, pattern="^show_cart$"),
                CallbackQueryHandler(cart_increment_callback, pattern="^cartinc_"),
                CallbackQueryHandler(cart_decrement_callback, pattern="^cartdec_")
            ],
            SELECT_DELIVERY_METHOD: [
                CallbackQueryHandler(delivery_direct_callback, pattern="^delivery_direct$"),
                CallbackQueryHandler(delivery_anonymous_callback, pattern="^delivery_anonymous$"),
                CallbackQueryHandler(delivery_slot_callback, pattern="^slot_"),
                CallbackQueryHandler(city_selected, pattern="^city_")
            ],
            PROCESS_PAYMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_payment)],
            GET_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address_and_finalize)]
        },
        fallbacks=[]
    )
    admin_conv = ConversationHandler(
        entry_points=[CommandHandler("deliver", admin_deliver_command)],
        states={ADMIN_AWAITING_PHOTO: [MessageHandler(filters.PHOTO, admin_process_photo)]},
        fallbacks=[]
    )
    app.add_handler(CallbackQueryHandler(contact_admin_callback, pattern="^contact_admin$"), group=1)
    app.add_handler(CallbackQueryHandler(start_support_ticket_callback, pattern="^start_support_ticket$"), group=1)
    app.add_handler(CallbackQueryHandler(cancel_support_ticket_callback, pattern="^cancel_support_ticket$"), group=1)
    app.add_handler(CallbackQueryHandler(modus_callback, pattern="^modus$"), group=1)
    app.add_handler(CallbackQueryHandler(view_products_callback, pattern="^view_products$"), group=1)
    app.add_handler(CallbackQueryHandler(disclaimer_agree_callback, pattern="^disclaimer_agree$"), group=1)
    app.add_handler(CallbackQueryHandler(disclaimer_reject_callback, pattern="^disclaimer_reject$"), group=1)
    app.add_handler(CallbackQueryHandler(view_menu_callback, pattern="^view_menu$"), group=1)
    app.add_handler(CallbackQueryHandler(checkout_start_callback, pattern="^checkout_start$"), group=1)
    app.add_handler(CallbackQueryHandler(clear_cart_callback, pattern="^clear_cart$"), group=1)
    app.add_handler(CallbackQueryHandler(delivery_direct_callback, pattern="^delivery_direct$"), group=1)
    app.add_handler(CallbackQueryHandler(delivery_anonymous_callback, pattern="^delivery_anonymous$"), group=1)
    app.add_handler(CallbackQueryHandler(clear_add_callback, pattern="^clearadd_"), group=1)
    app.add_handler(CallbackQueryHandler(show_cart_callback, pattern="^show_cart$"), group=1)
    app.add_handler(CallbackQueryHandler(cart_increment_callback, pattern="^cartinc_"), group=1)
    app.add_handler(CallbackQueryHandler(cart_decrement_callback, pattern="^cartdec_"), group=1)
    app.add_handler(CallbackQueryHandler(delivery_slot_callback, pattern="^slot_"), group=1)
    app.add_handler(CallbackQueryHandler(leaderboard_callback, pattern="^leaderboard$"), group=1)
    app.add_handler(CallbackQueryHandler(points_store_callback, pattern="^points_store$"), group=1)
    app.add_handler(CallbackQueryHandler(redeem_points_callback, pattern=r"^redeem_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(track_order_callback, pattern=r"^track_order_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(user_orders_callback, pattern="^user_orders$"), group=1)
    app.add_handler(CallbackQueryHandler(refer_earn_callback, pattern="^refer_earn$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_stats_callback, pattern="^gardener_stats$"), group=1)
    app.add_handler(CallbackQueryHandler(city_selected, pattern="^city_"), group=1)
    app.add_handler(CallbackQueryHandler(state_selected_callback, pattern="^state_"), group=1)
    app.add_handler(CallbackQueryHandler(state_search_callback, pattern="^state_search_"), group=1)
    app.add_handler(CallbackQueryHandler(state_type_callback, pattern="^state_type_"), group=1)
    app.add_handler(CallbackQueryHandler(product_selected, pattern="^prod_"), group=1)
    # admin dashboard and sub-action callbacks
    app.add_handler(CallbackQueryHandler(admin_dashboard_callback, pattern="^admin_dashboard$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_broadcast_callback, pattern="^admin_broadcast_"), group=1)
    app.add_handler(CallbackQueryHandler(admin_manage_blocked_callback, pattern="^admin_manage_blocked$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_toggle_block_callback, pattern="^admin_toggle_block_"), group=1)
    app.add_handler(CallbackQueryHandler(admin_view_orders_callback, pattern="^admin_view_orders$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_manage_order_callback, pattern=r"^admin_manage_order_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_cancel_order_callback, pattern=r"^admin_cancel_order_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(order_ticket_select_callback, pattern=r"^order_ticket_select_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(order_raise_ticket_callback, pattern=r"^raise_ticket_\d+_\w+$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_view_catalog_callback, pattern="^admin_view_catalog$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_manage_locations_callback, pattern="^admin_manage_locations$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_delete_loc_callback, pattern=r"^admin_delete_loc_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_revenue_callback, pattern="^admin_revenue_cb$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_add_location_callback, pattern="^admin_add_location$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_edit_products_callback, pattern="^admin_edit_products$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_gardeners_callback, pattern="^admin_gardeners$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_approve_callback, pattern=r"^gardener_approve_\d+$"), group=1)
    # gardener dashboard
    app.add_handler(CallbackQueryHandler(gardener_dashboard_callback, pattern="^gardener_dashboard$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_my_products_callback, pattern="^gardener_my_products$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_my_orders_callback, pattern="^gardener_my_orders$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_delete_product_callback, pattern=r"^gardener_del_prod_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_profile_callback, pattern="^gardener_profile$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_edit_field_callback, pattern=r"^gardener_edit_\w+$"), group=1)
    # gardener flows
    app.add_handler(CallbackQueryHandler(gardener_become_callback, pattern="^become_gardener$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_can_yes_callback, pattern="^gardener_can_yes$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_can_no_callback, pattern="^gardener_can_no$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_state_callback, pattern="^gardener_state_"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_city_callback, pattern="^gardener_city_id_"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_add_custom_loc_callback, pattern="^gardener_add_custom_loc$"), group=1)
    app.add_handler(CallbackQueryHandler(gardener_sell_callback, pattern="^gardener_sell$"), group=1)
    # admin command handlers for chat-based management
    app.add_handler(CommandHandler("location_add", location_add_command), group=1)
    app.add_handler(CommandHandler("catalog_add", catalog_add_command), group=1)
    app.add_handler(CommandHandler("catalog_update", catalog_update_command), group=1)
    app.add_handler(CommandHandler("catalog_delete", catalog_delete_command), group=1)
    # admin inline catalog actions
    app.add_handler(CallbackQueryHandler(admin_catalog_delete_callback, pattern=r"^catalog_delete_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(admin_catalog_edit_callback, pattern=r"^catalog_edit_\d+$"), group=1)
    # admin text handler for receiving edited catalog values (higher priority)
    # gardener/contact text handlers should be high priority
    app.add_handler(MessageHandler(filters.CONTACT, handle_contact), group=0)
    # admin live support reply handler
    app.add_handler(MessageHandler(filters.Chat(ADMIN_CHAT_ID) & filters.REPLY, handle_admin_reply), group=0)
    # unified text router handles all non-command text inputs securely without handler collision
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unified_text_router), group=0)
    # location delete command
    app.add_handler(CommandHandler("location_delete", location_delete_command), group=1)
    app.add_handler(CommandHandler("backup_db", admin_backup_db_command), group=1)
    app.add_handler(user_conv)
    app.add_handler(admin_conv)
    # handle incoming location messages from users
    async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        loc = update.message.location
        if not loc:
            return
        lat = loc.latitude
        lon = loc.longitude
        sender_id = update.message.from_user.id
        conn = sqlite3.connect('store.db')
        cur = conn.cursor()
        
        # Check if admin OR verified gardener sent location
        is_handler = False
        admin_order_id = context.user_data.get('admin_order_id')
        
        if sender_id == ADMIN_CHAT_ID:
            is_handler = True
        else:
            # check if it's the gardener for the specified or most recent awaiting order
            try:
                if admin_order_id:
                    cur.execute("SELECT id, gardener_id FROM orders WHERE id = ?", (admin_order_id,))
                    row = cur.fetchone()
                else:
                    cur.execute("SELECT id, gardener_id FROM orders WHERE awaiting_admin_location = 1 ORDER BY id DESC LIMIT 1")
                    row = cur.fetchone()
                
                if row and row[1]:
                    oid, gid = row
                    cur.execute("SELECT user_id, verified FROM gardeners WHERE id = ?", (gid,))
                    gr = cur.fetchone()
                    if gr and gr[0] == sender_id and gr[1] == 1:
                        is_handler = True
                        if not admin_order_id:
                            admin_order_id = oid
            except Exception as e:
                logging.error(f"Error checking location handler authorization: {e}")
                pass

        if is_handler:
            if not admin_order_id:
                # try find awaiting_admin_location flag
                try:
                    cur.execute("SELECT id FROM orders WHERE awaiting_admin_location = 1 ORDER BY id DESC LIMIT 1")
                    row = cur.fetchone()
                    admin_order_id = row[0] if row else None
                except Exception:
                    pass
            
            if admin_order_id:
                # Verify that this handler is indeed authorized to deliver this SPECIFIC order_id
                is_authorized_for_order = False
                if sender_id == ADMIN_CHAT_ID:
                    is_authorized_for_order = True
                else:
                    try:
                        cur.execute("SELECT gardener_id FROM orders WHERE id = ?", (admin_order_id,))
                        row_auth = cur.fetchone()
                        if row_auth and row_auth[0]:
                            cur.execute("SELECT user_id, verified FROM gardeners WHERE id = ?", (row_auth[0],))
                            gr_auth = cur.fetchone()
                            if gr_auth and gr_auth[0] == sender_id and gr_auth[1] == 1:
                                is_authorized_for_order = True
                    except Exception:
                        pass
                
                if is_authorized_for_order:
                    # store location and forward photo+location to user
                    cur.execute("SELECT user_id, product, admin_photo_file_id, gardener_id FROM orders WHERE id = ?", (admin_order_id,))
                    r = cur.fetchone()
                    if r:
                        user_id, product, admin_photo, gardener_id = r
                        cur.execute("UPDATE orders SET admin_lat = ?, admin_lon = ?, awaiting_admin_location = 0, status = 'DELIVERED' WHERE id = ?", (lat, lon, admin_order_id))
                        conn.commit()
                        try:
                            # send photo then location to user
                            sender_label = "Admin" if sender_id == ADMIN_CHAT_ID else "Gardener"
                            if admin_photo:
                                await send_user_photo(context, user_id, admin_photo, caption=f"🌿 Your order for *{product}* has been dropped off safely!", sender=sender_label)
                            await context.bot.send_location(chat_id=user_id, latitude=lat, longitude=lon)
                            
                            # Escrow: Ask user to confirm delivery
                            buttons = [
                                [InlineKeyboardButton("✅ Confirm Delivery Received", callback_data=f"confirm_delivery_{admin_order_id}")]
                            ]
                            if gardener_id:
                                gardener_username = None
                                gardener_chat_id = None
                                cur.execute("SELECT username, user_id FROM gardeners WHERE id = ?", (gardener_id,))
                                grow = cur.fetchone()
                                if grow:
                                    gardener_username = grow[0]
                                    gardener_chat_id = grow[1]
                                
                                if gardener_username:
                                    buttons.append([InlineKeyboardButton(f"❌ Not received? Contact {gardener_username}", url=f"https://t.me/{gardener_username}")])
                                elif gardener_chat_id:
                                    buttons.append([InlineKeyboardButton("❌ Not received? Contact Gardener", url=f"tg://user?id={gardener_chat_id}")])
                            else:
                                admin_username = os.environ.get("ADMIN_USERNAME")
                                if admin_username:
                                    buttons.append([InlineKeyboardButton("❌ Not received? Contact Admin", url=f"https://t.me/{admin_username}")])
                                else:
                                    buttons.append([InlineKeyboardButton("❌ Not received? Contact Admin", url=f"tg://user?id={ADMIN_CHAT_ID}")])
                                    
                            confirm_kb = InlineKeyboardMarkup(buttons)
                            await context.bot.send_message(
                                chat_id=user_id,
                                text=f"📦 *Order #{admin_order_id} has been delivered!*\n\nThe drop-off location is shown above. Please confirm if you have received your item.",
                                reply_markup=confirm_kb,
                                parse_mode="Markdown"
                            )
                            await update.message.reply_text(f"Location forwarded to user for Order #{admin_order_id}", reply_markup=ReplyKeyboardRemove())
                            context.user_data.pop('admin_order_id', None)
                            
                            # notify both admin and actual sender
                            notif_text = f"✅ Delivered Order #{admin_order_id}; location forwarded to user."
                            await context.bot.send_message(chat_id=sender_id, text=notif_text)
                            if sender_id != ADMIN_CHAT_ID:
                                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🔔 Gardener delivered Order #{admin_order_id}.")
                        except Exception as e:
                            logging.error(f"Error sending drop-off location info to user: {e}")
                            pass
            conn.close()
            return

        # otherwise treat as user sharing their location when requested
        user_id = sender_id
        cur.execute("SELECT id FROM orders WHERE user_id = ? AND location_requested = 1 ORDER BY id DESC LIMIT 1", (user_id,))
        row = cur.fetchone()
        if row:
            oid = row[0]
            cur.execute("UPDATE orders SET lat = ?, lon = ?, location_requested = 0 WHERE id = ?", (lat, lon, oid))
            conn.commit()
            try:
                await update.message.reply_text(f"Thanks — location recorded for Order #{oid}.", reply_markup=ReplyKeyboardRemove())
            except Exception:
                pass
            try:
                await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"📍 Location received for Order #{oid} (user {user_id}): {lat},{lon}")
            except Exception:
                pass
        conn.close()
    app.add_handler(MessageHandler(filters.LOCATION, handle_location), group=1)
    app.add_handler(CallbackQueryHandler(confirm_delivery_callback, pattern=r"^confirm_delivery_\d+$"), group=1)
    app.add_handler(CallbackQueryHandler(rate_gardener_callback, pattern=r"^rate_[1-5]_\d+$"), group=1)
    
    app.run_polling()