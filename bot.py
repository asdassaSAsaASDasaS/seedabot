import os
import sqlite3
import logging
import urllib.parse
import threading
import asyncio
import ssl
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters
)
import flet as ft
import requests

# global application object (will be assigned in __main__)
app = None

# --- MAC OS SSL FIX ---
ssl._create_default_https_context = ssl._create_unverified_context

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8736431220:AAHiQIe9CfYRRWVJlVUecOFqL0dh1TD8KFk")
try:
    ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", "1222599704"))
except Exception:
    ADMIN_CHAT_ID = 1222599704
UPI_ID = os.environ.get("UPI_ID", "7259398790-4@ybl")
MERCHANT_NAME = "The Green Oasis" 

# --- STAGES FOR CONVERSATION HANDLERS ---
SELECT_CITY, SELECT_PRODUCT, PROCESS_PAYMENT, GET_ADDRESS = range(4)
ADMIN_AWAITING_PHOTO = range(1)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            city TEXT,
            product TEXT,
            utr_no TEXT,
            address TEXT,
            status TEXT DEFAULT 'PENDING'
        )
    ''')
    # ensure catalog table exists so dashboard and bot can share product data
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            city TEXT,
            name TEXT,
            price INTEGER
        )
    ''')
    conn.commit()
    conn.close()

# --- LOCAL CATALOG ---
def load_catalog_from_db():
    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    cursor.execute("SELECT city, name, price FROM catalog ORDER BY id")
    rows = cursor.fetchall()
    conn.close()

    catalog = {}
    for city, name, price in rows:
        catalog.setdefault(city, []).append({"name": name, "price": price})

    # fallback to hard-coded sample if DB empty
    if not catalog:
        catalog = {
            "Bangalore": [
                {"name": "Monstera Deliciosa", "price": 1200},
                {"name": "Snake Plant (Laurentii)", "price": 450},
            ],
            "Mangalore": [
                {"name": "Fiddle Leaf Fig", "price": 1500},
                {"name": "Peace Lily", "price": 350},
            ]
        }

    return catalog

# --- USER TELEGRAM FLOW ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    init_db()
    keyboard = [
        [InlineKeyboardButton("Bangalore 🌆", callback_data="city_Bangalore")],
        [InlineKeyboardButton("Mangalore 🌊", callback_data="city_Mangalore")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome to the Local Plant Shop! 🌿\nPlease select your delivery location to view available stock:",
        reply_markup=reply_markup
    )
    return SELECT_CITY

async def city_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    city = query.data.split("_")[1]
    context.user_data['city'] = city
    # load catalog from DB so bot sees latest changes
    catalog = load_catalog_from_db()
    keyboard = []
    for idx, item in enumerate(catalog.get(city, [])):
        keyboard.append([InlineKeyboardButton(f"{item['name']} (₹{item['price']})", callback_data=f"prod_{idx}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"📍 Stock available in **{city}**:\nSelect a plant to purchase:", reply_markup=reply_markup, parse_mode="Markdown")
    return SELECT_PRODUCT

async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    prod_idx = int(query.data.split("_")[1])
    city = context.user_data['city']
    # read catalog fresh from DB
    catalog = load_catalog_from_db()
    product = catalog.get(city, [])[prod_idx]
    
    context.user_data['product_name'] = product['name']
    context.user_data['price'] = product['price']
    
    encoded_name = urllib.parse.quote(MERCHANT_NAME)
    encoded_note = urllib.parse.quote(f"Order for {product['name']}")
    upi_url = f"upi://pay?pa={UPI_ID}&pn={encoded_name}&am={product['price']}&cu=INR&tn={encoded_note}"
    
    qr_code_api = f"https://api.qrserver.com/v1/create-qr-code/?size=300x300&data={urllib.parse.quote(upi_url)}"
    
    instruction_text = (
        f"🛒 *You selected:* {product['name']}\n"
        f"💰 *Amount Due:* ₹{product['price']}\n\n"
        f"📌 *Our UPI ID:* `{UPI_ID}`\n\n"
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
    return PROCESS_PAYMENT

async def process_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    utr_no = update.message.text.strip()
    if not utr_no.isdigit() or len(utr_no) != 12:
        await update.message.reply_text("⚠️ Invalid format. A standard UPI Ref/UTR number must be exactly 12 numerical digits. Please recheck your app receipt and try typing it again:")
        return PROCESS_PAYMENT

    context.user_data['utr_no'] = utr_no
    await update.message.reply_text("✅ UTR Number recorded! Final Step: Please reply with your delivery address where you want this delivered and your mobile number.")
    return GET_ADDRESS

async def get_address_and_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    address = update.message.text
    user = update.message.from_user
    city = context.user_data['city']
    product_name = context.user_data['product_name']
    price = context.user_data['price']
    utr_no = context.user_data['utr_no']
    
    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO orders (user_id, username, city, product, utr_no, address) VALUES (?, ?, ?, ?, ?, ?)",
        (user.id, user.username or user.first_name, city, product_name, utr_no, address)
    )
    order_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    await update.message.reply_text(f"🎉 Order #{order_id} placed successfully!\n\nWe will keep the product near your delivery location and send you a photo.")
    
    admin_text = f"🚨 *NEW UPI ORDER #{order_id}* 🚨\n\n👤 *Customer:* @{user.username}\n📍 *City:* {city}\n🌿 *Plant:* {product_name}\n💰 *Expected Amount:* ₹{price}\n🔢 *UPI UTR:* `{utr_no}`\n🏠 *Address:* {address}\n\nTo close: `/deliver {order_id}`"
    await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text, parse_mode="Markdown")
    return ConversationHandler.END

# --- ADMIN ROUTING ---
async def admin_deliver_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.chat_id != ADMIN_CHAT_ID: return ConversationHandler.END
    if not context.args: return ConversationHandler.END
    context.user_data['admin_order_id'] = context.args[0]
    await update.message.reply_text(f"📸 Please upload the delivery proof photo for Order #{context.args[0]}:")
    return ADMIN_AWAITING_PHOTO

async def admin_process_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    order_id = context.user_data['admin_order_id']
    photo_file_id = update.message.photo[-1].file_id
    conn = sqlite3.connect('store.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, product FROM orders WHERE id = ?", (order_id,))
    result = cursor.fetchone()
    if result:
        user_id, product = result
        cursor.execute("UPDATE orders SET status = 'DELIVERED' WHERE id = ?", (order_id,))
        conn.commit()
        await context.bot.send_photo(chat_id=user_id, photo=photo_file_id, caption=f"🌿 Your order for *{product}* has been dropped off safely!")
        await update.message.reply_text("✅ Photo sent to user.")
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
    app = Application.builder().token(BOT_TOKEN).build()
    
    user_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            SELECT_CITY: [CallbackQueryHandler(city_selected, pattern="^city_")],
            SELECT_PRODUCT: [CallbackQueryHandler(product_selected, pattern="^prod_")],
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
    app.add_handler(user_conv)
    app.add_handler(admin_conv)
    
    app.run_polling()