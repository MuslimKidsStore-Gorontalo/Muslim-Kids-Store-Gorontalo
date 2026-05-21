"""
POS Toko Kelontong AI — Telegram Bot
Point of Sale lengkap untuk toko kelontong
Powered by Google Gemini API (GRATIS)
"""

import os, json, csv, io, sqlite3
from datetime import datetime, timedelta
import google.generativeai as genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes,
)

# ── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
DB_PATH        = "pos_toko.db"

genai.configure(api_key=GEMINI_API_KEY)
gemini = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    generation_config={"temperature": 0.1, "max_output_tokens": 600},
)

# ConversationHandler states
(
    JUAL_ITEM, JUAL_PILIH_BAYAR, JUAL_BAYAR_TUNAI, JUAL_KONFIRM_TRANSFER,
    PRODUK_NAMA, PRODUK_HARGA_JUAL, PRODUK_HARGA_BELI, PRODUK_STOK, PRODUK_SATUAN,
    STOK_MASUK_PRODUK, STOK_MASUK_QTY,
    EDIT_FIELD, EDIT_VALUE,
    SETTING_QRIS, SETTING_REKENING,
) = range(15)

# ── Pembayaran Config (simpan di file json per user) ──────────────────────────
import pathlib

def get_payment_config(owner_id):
    path = pathlib.Path(f"payment_{owner_id}.json")
    if path.exists():
        return json.loads(path.read_text())
    return {"qris_name": "", "qris_number": "", "bank_name": "", "bank_number": "", "bank_holder": ""}

def save_payment_config(owner_id, config):
    path = pathlib.Path(f"payment_{owner_id}.json")
    path.write_text(json.dumps(config, ensure_ascii=False))

CAT_EMOJI = {
    "Minuman":"🥤","Makanan":"🍜","Snack":"🍿","Rokok":"🚬",
    "Sembako":"🌾","Kebersihan":"🧹","Perawatan":"🧴",
    "Bumbu":"🧂","Frozen":"🧊","Lainnya":"📦",
}
CATEGORIES = list(CAT_EMOJI.keys())


# ── Database ─────────────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id    INTEGER NOT NULL,
            name        TEXT    NOT NULL,
            price_sell  INTEGER NOT NULL DEFAULT 0,
            price_buy   INTEGER NOT NULL DEFAULT 0,
            stock       INTEGER NOT NULL DEFAULT 0,
            min_stock   INTEGER NOT NULL DEFAULT 5,
            unit        TEXT    NOT NULL DEFAULT 'pcs',
            category    TEXT    NOT NULL DEFAULT 'Lainnya',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sales (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id        INTEGER NOT NULL,
            total           INTEGER NOT NULL,
            payment         INTEGER NOT NULL DEFAULT 0,
            change          INTEGER NOT NULL DEFAULT 0,
            payment_method  TEXT    NOT NULL DEFAULT 'tunai',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sale_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sale_id     INTEGER NOT NULL,
            product_id  INTEGER,
            product_name TEXT   NOT NULL,
            price       INTEGER NOT NULL,
            qty         INTEGER NOT NULL,
            subtotal    INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stock_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id    INTEGER NOT NULL,
            product_id  INTEGER NOT NULL,
            qty_change  INTEGER NOT NULL,
            type        TEXT    NOT NULL,
            note        TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id    INTEGER NOT NULL,
            amount      INTEGER NOT NULL,
            category    TEXT    NOT NULL DEFAULT 'Operasional',
            description TEXT    NOT NULL,
            date        TEXT    NOT NULL,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

# Products
def db_add_product(owner, name, price_sell, price_buy, stock, min_stock, unit, category):
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT INTO products (owner_id,name,price_sell,price_buy,stock,min_stock,unit,category) VALUES (?,?,?,?,?,?,?,?)",
            (owner, name, price_sell, price_buy, stock, min_stock, unit, category)
        )
        return cur.lastrowid

def db_search_products(owner, keyword):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT * FROM products WHERE owner_id=? AND name LIKE ? ORDER BY name",
            (owner, f"%{keyword}%")
        ).fetchall()

def db_get_product(owner, pid):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT * FROM products WHERE owner_id=? AND id=?", (owner, pid)
        ).fetchone()

def db_get_all_products(owner):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT * FROM products WHERE owner_id=? ORDER BY category, name", (owner,)
        ).fetchall()

def db_update_stock(owner, pid, qty_change, type_, note=""):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE products SET stock=stock+? WHERE owner_id=? AND id=?", (qty_change, owner, pid))
        c.execute(
            "INSERT INTO stock_log (owner_id,product_id,qty_change,type,note) VALUES (?,?,?,?,?)",
            (owner, pid, qty_change, type_, note)
        )

def db_update_product(pid, field, value):
    allowed = ["name","price_sell","price_buy","stock","min_stock","unit","category"]
    if field not in allowed: return
    with sqlite3.connect(DB_PATH) as c:
        c.execute(f"UPDATE products SET {field}=? WHERE id=?", (value, pid))

def db_low_stock(owner):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT * FROM products WHERE owner_id=? AND stock<=min_stock ORDER BY stock", (owner,)
        ).fetchall()

# Sales
def db_create_sale(owner, items, total, payment, payment_method="tunai"):
    change = payment - total if payment_method == "tunai" else 0
    with sqlite3.connect(DB_PATH) as c:
        cur = c.execute(
            "INSERT INTO sales (owner_id,total,payment,change,payment_method) VALUES (?,?,?,?,?)",
            (owner, total, payment, change, payment_method)
        )
        sale_id = cur.lastrowid
        for item in items:
            c.execute(
                "INSERT INTO sale_items (sale_id,product_id,product_name,price,qty,subtotal) VALUES (?,?,?,?,?,?)",
                (sale_id, item["pid"], item["name"], item["price"], item["qty"], item["subtotal"])
            )
            if item.get("pid"):
                c.execute("UPDATE products SET stock=stock-? WHERE id=?", (item["qty"], item["pid"]))
                c.execute(
                    "INSERT INTO stock_log (owner_id,product_id,qty_change,type,note) VALUES (?,?,?,?,?)",
                    (owner, item["pid"], -item["qty"], "sale", f"Sale #{sale_id}")
                )
        return sale_id, change

def db_get_sales(owner, since=None):
    with sqlite3.connect(DB_PATH) as c:
        if since:
            return c.execute(
                "SELECT * FROM sales WHERE owner_id=? AND created_at>=? ORDER BY created_at DESC",
                (owner, since)
            ).fetchall()
        return c.execute(
            "SELECT * FROM sales WHERE owner_id=? ORDER BY created_at DESC", (owner,)
        ).fetchall()

def db_get_sale_items(sale_id):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT * FROM sale_items WHERE sale_id=?", (sale_id,)
        ).fetchall()

def db_get_monthly_sales(owner):
    month = datetime.now().strftime("%Y-%m")
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT * FROM sales WHERE owner_id=? AND created_at LIKE ? ORDER BY created_at DESC",
            (owner, f"{month}%")
        ).fetchall()

# Expenses
def db_add_expense(owner, amount, category, description):
    with sqlite3.connect(DB_PATH) as c:
        c.execute(
            "INSERT INTO expenses (owner_id,amount,category,description,date) VALUES (?,?,?,?,?)",
            (owner, amount, category, description, datetime.now().strftime("%Y-%m-%d"))
        )

def db_get_monthly_expenses(owner):
    month = datetime.now().strftime("%Y-%m")
    with sqlite3.connect(DB_PATH) as c:
        return c.execute(
            "SELECT * FROM expenses WHERE owner_id=? AND date LIKE ? ORDER BY created_at DESC",
            (owner, f"{month}%")
        ).fetchall()


# ── Helpers ──────────────────────────────────────────────────────────────────
def fmt_rp(v):
    if v >= 1_000_000: return f"Rp {v/1e6:.1f}jt"
    if v >= 1_000:     return f"Rp {v/1e3:.0f}rb"
    return f"Rp {int(v):,}"

def fmt_full(v): return f"Rp {int(v):,}"
def now_str():   return datetime.now().strftime("%d/%m/%Y %H:%M")
def today():     return datetime.now().strftime("%Y-%m-%d")

def make_receipt(sale_id, items, total, payment, change, method="tunai"):
    method_label = {"tunai":"💵 Tunai", "qris":"📱 QRIS", "transfer":"🏦 Transfer"}.get(method, method)
    lines = [
        "🧾 *STRUK PEMBAYARAN*",
        f"No: #{sale_id:04d} | {now_str()}",
        "─" * 28,
    ]
    for it in items:
        lines.append(f"{it['name'][:20]}")
        lines.append(f"  {it['qty']} × {fmt_full(it['price'])} = {fmt_full(it['subtotal'])}")
    lines += [
        "─" * 28,
        f"💰 *TOTAL         : {fmt_full(total)}*",
        f"{method_label}",
    ]
    if method == "tunai":
        lines += [
            f"💵 Bayar         : {fmt_full(payment)}",
            f"💚 Kembalian     : *{fmt_full(change)}*",
        ]
    else:
        lines.append(f"✅ *LUNAS*")
    lines += ["─" * 28, "Terima kasih! 🙏"]
    return "\n".join(lines)

def cart_summary(cart):
    if not cart: return "_Keranjang kosong_"
    lines = ["🛒 *Keranjang:*"]
    total = 0
    for i, item in enumerate(cart, 1):
        sub = item["qty"] * item["price"]
        total += sub
        lines.append(f"{i}. {item['name']} × {item['qty']} = {fmt_full(sub)}")
    lines.append(f"\n💰 *Total: {fmt_full(total)}*")
    return "\n".join(lines)


# ── AI Helpers ────────────────────────────────────────────────────────────────
def ai_parse_item(text, products):
    """Parse teks jual menjadi item transaksi dengan fallback manual."""

    # ── Fallback manual: coba cocokkan langsung tanpa AI ─────────────────
    # Format: "nama produk angka" atau "angka nama produk"
    import re
    text_lower = text.lower().strip()

    # Coba cocokkan dengan produk yang ada
    for p in products:
        nama = p[2].lower()
        # Cek apakah nama produk ada di teks
        if nama in text_lower or any(w in text_lower for w in nama.split()):
            # Ambil angka dari teks
            angka = re.findall(r'\d+', text)
            qty = int(angka[0]) if angka else 1
            return {
                "found": True,
                "items": [{"pid": p[0], "name": p[2], "price": p[3], "qty": qty}],
                "message": "OK"
            }

    # ── Kalau manual gagal, coba via Gemini AI ────────────────────────────
    product_list = "\n".join([
        f"id:{p[0]}|nama:{p[2]}|harga:{p[3]}|stok:{p[5]}|satuan:{p[7]}"
        for p in products
    ])
    prompt = f"""Kamu kasir toko. Parse perintah penjualan berikut.

Produk tersedia:
{product_list}

Perintah: "{text}"

Aturan:
- Cocokkan nama produk secara fleksibel (tidak harus persis sama)
- Jika ada angka setelah nama produk, itu adalah qty
- Jika tidak ada angka, qty = 1

Kembalikan HANYA JSON (tanpa markdown, tanpa penjelasan):
{{"found":true,"items":[{{"pid":ID_PRODUK,"name":"NAMA","price":HARGA,"qty":JUMLAH}}],"message":"ok"}}

Jika tidak ada produk yang cocok:
{{"found":false,"items":[],"message":"Produk tidak ditemukan"}}"""

    try:
        resp = gemini.generate_content(prompt)
        raw  = resp.text.strip()
        # Bersihkan markdown jika ada
        raw = re.sub(r'```json|```', '', raw).strip()
        # Ambil JSON dari teks
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        return json.loads(raw)
    except Exception:
        return {"found": False, "items": [], "message": "Gagal memproses"}

def ai_parse_expense(text):
    """Parse teks pengeluaran."""
    prompt = f"""Parse pengeluaran toko berikut. Kembalikan HANYA JSON:
{{"success":true/false,"amount":integer,"category":"Belanja Stok/Operasional/Gaji/Listrik/Lainnya","description":"singkat maks 40 karakter","reply":"konfirmasi singkat"}}
Konversi: rb=x1000, jt=x1000000. Teks: "{text}" """
    resp = gemini.generate_content(prompt)
    raw  = resp.text.replace("```json","").replace("```","").strip()
    return json.loads(raw)


# ── /start ────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"🏪 Selamat datang *{name}*!\n\n"
        "*Muslim Kids Store Gorontalo* siap membantu.\n\n"
        "📌 *Menu Utama:*\n"
        "🛒 /jual — Catat penjualan\n"
        "📦 /produk — Kelola produk\n"
        "📊 /laporan — Laporan & omzet\n"
        "📉 /stok — Cek & update stok\n"
        "💸 /pengeluaran — Catat pengeluaran\n"
        "💳 /setting_pembayaran — Atur QRIS & Rekening\n"
        "📥 /export — Export data CSV\n"
        "⚠️ /stok_tipis — Alert stok menipis\n"
        "❓ /bantuan — Panduan lengkap",
        parse_mode="Markdown"
    )


# ── /bantuan ──────────────────────────────────────────────────────────────────
async def cmd_bantuan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Panduan POS Toko Kelontong*\n\n"
        "*🛒 Penjualan:*\n"
        "Ketik `/jual` lalu masukkan item:\n"
        "`indomie goreng 3`\n"
        "`aqua botol 2, teh botol 1`\n"
        "Ketik `selesai` → masukkan nominal bayar\n\n"
        "*📦 Produk:*\n"
        "`/produk` — lihat semua produk\n"
        "`/tambah` — tambah produk baru\n"
        "`/cari indomie` — cari produk\n\n"
        "*📉 Stok:*\n"
        "`/stok` — lihat semua stok\n"
        "`/stok_masuk` — catat barang masuk\n"
        "`/stok_tipis` — produk hampir habis\n\n"
        "*💸 Pengeluaran:*\n"
        "Ketik `/pengeluaran` lalu:\n"
        "`beli rokok gudang garam 500rb`\n"
        "`bayar listrik 200rb`\n\n"
        "*📊 Laporan:*\n"
        "`/laporan` — omzet, laba, penjualan",
        parse_mode="Markdown"
    )


# ── /jual (ConversationHandler) ───────────────────────────────────────────────
async def cmd_jual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["cart"] = []
    await update.message.reply_text(
        "🛒 *Mode Penjualan*\n\n"
        "Masukkan nama produk & jumlah:\n"
        "`indomie goreng 3`\n"
        "`aqua 600ml 2`\n\n"
        "Ketik *selesai* jika sudah, atau /batal untuk membatalkan.",
        parse_mode="Markdown"
    )
    return JUAL_ITEM

async def jual_item(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text    = update.message.text.strip()
    owner   = update.effective_user.id
    cart    = ctx.user_data.get("cart", [])

    if text.lower() in ["batal", "/batal"]:
        ctx.user_data["cart"] = []
        await update.message.reply_text("❌ Penjualan dibatalkan.")
        return ConversationHandler.END

    if text.lower() == "selesai":
        if not cart:
            await update.message.reply_text("⚠️ Keranjang masih kosong! Masukkan produk dulu.")
            return JUAL_ITEM
        total = sum(i["qty"] * i["price"] for i in cart)
        ctx.user_data["jual_total"] = total
        keyboard = [
            [InlineKeyboardButton("💵 Tunai",    callback_data="pay_tunai")],
            [InlineKeyboardButton("📱 QRIS",     callback_data="pay_qris")],
            [InlineKeyboardButton("🏦 Transfer", callback_data="pay_transfer")],
        ]
        await update.message.reply_text(
            f"{cart_summary(cart)}\n\n💳 *Pilih metode pembayaran:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return JUAL_PILIH_BAYAR

    # Cek produk dulu — jika kosong langsung kasih tahu
    products = db_get_all_products(owner)
    if not products:
        await update.message.reply_text(
            "⚠️ *Belum ada produk di database!*\n\n"
            "Tambah produk dulu dengan /tambah sebelum bisa berjualan.\n\n"
            "Atau ketik /batal untuk keluar dari mode penjualan.",
            parse_mode="Markdown"
        )
        return JUAL_ITEM

    # Parse item via AI
    try:
        parsed = ai_parse_item(text, products)
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Gagal memproses item. Coba ketik ulang.\nContoh: `indomie goreng 3`",
            parse_mode="Markdown"
        )
        return JUAL_ITEM

    if not parsed.get("found"):
        await update.message.reply_text(
            f"🤔 {parsed.get('message','Tidak dikenali.')}\n\nCoba ketik: `nama produk jumlah`\nContoh: `indomie 3` atau `aqua 2`"
        )
        return JUAL_ITEM

    added = []
    for item in parsed.get("items", []):
        if item.get("price", 0) == 0 and item.get("pid") is None:
            await update.message.reply_text(
                f"⚠️ Produk *{item['name']}* tidak ditemukan.\n"
                f"Cek nama produk dengan /produk atau tambah dulu dengan /tambah.",
                parse_mode="Markdown"
            )
            continue
        # Cek stok
        if item.get("pid"):
            prod = db_get_product(owner, item["pid"])
            if prod and prod[5] < item["qty"]:
                await update.message.reply_text(
                    f"⚠️ Stok *{item['name']}* hanya {prod[5]} {prod[7]}!",
                    parse_mode="Markdown"
                )
                continue
        cart.append({
            "pid": item.get("pid"), "name": item["name"],
            "price": item["price"], "qty": item["qty"],
            "subtotal": item["price"] * item["qty"],
        })
        added.append(f"✅ {item['name']} × {item['qty']} = {fmt_full(item['price'] * item['qty'])}")

    ctx.user_data["cart"] = cart
    if added:
        total = sum(i["qty"] * i["price"] for i in cart)
        await update.message.reply_text(
            "\n".join(added) + f"\n\n{cart_summary(cart)}\n\n"
            "Tambah item lagi atau ketik *selesai*.",
            parse_mode="Markdown"
        )
    return JUAL_ITEM

async def jual_bayar_tunai(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip().replace(".","").replace(",","")
    owner = update.effective_user.id
    cart  = ctx.user_data.get("cart", [])
    total = ctx.user_data.get("jual_total", 0)

    try:
        payment = int(text)
    except ValueError:
        await update.message.reply_text("⚠️ Masukkan angka saja. Contoh: `50000`")
        return JUAL_BAYAR_TUNAI

    if payment < total:
        await update.message.reply_text(
            f"⚠️ Kurang! Total *{fmt_full(total)}*, bayar *{fmt_full(payment)}*.\n"
            "Masukkan ulang nominal yang cukup:",
            parse_mode="Markdown"
        )
        return JUAL_BAYAR_TUNAI

    sale_id, change = db_create_sale(owner, cart, total, payment, "tunai")
    ctx.user_data["cart"] = []
    receipt = make_receipt(sale_id, cart, total, payment, change, "tunai")
    await update.message.reply_text(receipt, parse_mode="Markdown")
    return ConversationHandler.END

async def jual_konfirm_transfer(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler konfirmasi setelah transfer — user ketik 'sudah' atau nominal."""
    text  = update.message.text.strip().lower()
    owner = update.effective_user.id
    cart  = ctx.user_data.get("cart", [])
    total = ctx.user_data.get("jual_total", 0)
    method = ctx.user_data.get("pay_method", "transfer")

    if text in ["sudah", "lunas", "ok", "oke", "ya", "done", "✅"]:
        sale_id, _ = db_create_sale(owner, cart, total, total, method)
        ctx.user_data["cart"] = []
        receipt = make_receipt(sale_id, cart, total, total, 0, method)
        await update.message.reply_text(
            f"✅ *Pembayaran dikonfirmasi!*\n\n{receipt}",
            parse_mode="Markdown"
        )
        return ConversationHandler.END
    else:
        await update.message.reply_text(
            "Ketik *sudah* setelah pembayaran diterima, atau /batal untuk membatalkan.",
            parse_mode="Markdown"
        )
        return JUAL_KONFIRM_TRANSFER

async def jual_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["cart"] = []
    await update.message.reply_text("❌ Penjualan dibatalkan.")
    return ConversationHandler.END


# ── /tambah produk (ConversationHandler) ─────────────────────────────────────
async def cmd_tambah(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_prod"] = {}
    await update.message.reply_text(
        "📦 *Tambah Produk Baru*\n\nMasukkan nama produk:",
        parse_mode="Markdown"
    )
    return PRODUK_NAMA

async def produk_nama(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_prod"]["name"] = update.message.text.strip()
    await update.message.reply_text(f"💰 Harga *jual* (contoh: 3500):", parse_mode="Markdown")
    return PRODUK_HARGA_JUAL

async def produk_harga_jual(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text.strip().replace(".","").replace(",",""))
        ctx.user_data["new_prod"]["price_sell"] = val
        await update.message.reply_text(f"💸 Harga *beli/modal* (contoh: 2800):", parse_mode="Markdown")
        return PRODUK_HARGA_BELI
    except:
        await update.message.reply_text("⚠️ Masukkan angka saja.")
        return PRODUK_HARGA_JUAL

async def produk_harga_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text.strip().replace(".","").replace(",",""))
        ctx.user_data["new_prod"]["price_buy"] = val
        await update.message.reply_text("📊 Stok awal (contoh: 50):")
        return PRODUK_STOK
    except:
        await update.message.reply_text("⚠️ Masukkan angka saja.")
        return PRODUK_HARGA_BELI

async def produk_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text.strip())
        ctx.user_data["new_prod"]["stock"] = val
        await update.message.reply_text("📏 Satuan produk (contoh: pcs / botol / bungkus / kg):")
        return PRODUK_SATUAN
    except:
        await update.message.reply_text("⚠️ Masukkan angka saja.")
        return PRODUK_STOK

async def produk_satuan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["new_prod"]["unit"] = update.message.text.strip()
    # Pilih kategori
    cats = [CATEGORIES[i:i+3] for i in range(0, len(CATEGORIES), 3)]
    keyboard = [[InlineKeyboardButton(f"{CAT_EMOJI[c]} {c}", callback_data=f"cat_{c}") for c in row] for row in cats]
    await update.message.reply_text(
        "🗂️ Pilih kategori:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return PRODUK_SATUAN  # tunggu callback

async def produk_kategori_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    category = query.data.replace("cat_","")
    owner    = query.from_user.id
    prod     = ctx.user_data.get("new_prod", {})

    pid = db_add_product(
        owner, prod["name"], prod["price_sell"], prod["price_buy"],
        prod["stock"], 5, prod["unit"], category
    )
    margin = prod["price_sell"] - prod["price_buy"]
    await query.edit_message_text(
        f"✅ *Produk berhasil ditambahkan!*\n\n"
        f"📦 {prod['name']} (ID: #{pid})\n"
        f"🏷️ Kategori: {category} {CAT_EMOJI.get(category,'')}\n"
        f"💰 Jual: {fmt_full(prod['price_sell'])}\n"
        f"💸 Beli: {fmt_full(prod['price_buy'])}\n"
        f"📈 Margin: {fmt_full(margin)} ({round(margin/prod['price_sell']*100) if prod['price_sell'] else 0}%)\n"
        f"📊 Stok: {prod['stock']} {prod['unit']}",
        parse_mode="Markdown"
    )
    ctx.user_data["new_prod"] = {}
    return ConversationHandler.END


async def cmd_setting_pembayaran(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner  = update.effective_user.id
    config = get_payment_config(owner)
    qris_info = f"📱 *QRIS:* {config['qris_name']} — {config['qris_number']}" if config['qris_number'] else "📱 *QRIS:* Belum diset"
    bank_info = (f"🏦 *Transfer:* {config['bank_name']} a/n {config['bank_holder']}\n   No: `{config['bank_number']}`"
                 if config['bank_number'] else "🏦 *Transfer:* Belum diset")
    keyboard = [
        [InlineKeyboardButton("📱 Atur QRIS",     callback_data="set_qris")],
        [InlineKeyboardButton("🏦 Atur Rekening",  callback_data="set_rekening")],
    ]
    await update.message.reply_text(
        f"💳 *Setting Pembayaran*\n\n{qris_info}\n{bank_info}\n\n"
        "Pilih yang ingin diatur:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def setting_qris_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 *Atur Info QRIS*\n\nKetik format:\n`NamaDompet NomorHP`\n\nContoh:\n`GoPay 081234567890`",
        parse_mode="Markdown"
    )
    return SETTING_QRIS

async def setting_qris_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner  = update.effective_user.id
    parts  = update.message.text.strip().split(None, 1)
    config = get_payment_config(owner)
    if len(parts) >= 2:
        config["qris_name"]   = parts[0]
        config["qris_number"] = parts[1]
    else:
        config["qris_name"]   = parts[0]
        config["qris_number"] = ""
    save_payment_config(owner, config)
    await update.message.reply_text(
        f"✅ Info QRIS disimpan!\n📱 *{config['qris_name']}* — {config['qris_number']}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

async def setting_rekening_input(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏦 *Atur Rekening Transfer*\n\nKetik format:\n`NamaBank NomorRek NamaPemilik`\n\nContoh:\n`BRI 123456789012 Siti Rahma`",
        parse_mode="Markdown"
    )
    return SETTING_REKENING

async def setting_rekening_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner  = update.effective_user.id
    parts  = update.message.text.strip().split(None, 2)
    config = get_payment_config(owner)
    if len(parts) >= 3:
        config["bank_name"]   = parts[0]
        config["bank_number"] = parts[1]
        config["bank_holder"] = parts[2]
        save_payment_config(owner, config)
        await update.message.reply_text(
            f"✅ Rekening disimpan!\n🏦 *{config['bank_name']}* — `{config['bank_number']}`\na/n *{config['bank_holder']}*",
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("⚠️ Format salah. Contoh: `BRI 123456789012 Siti Rahma`", parse_mode="Markdown")
    return ConversationHandler.END


# ── /produk ───────────────────────────────────────────────────────────────────
async def cmd_produk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner    = update.effective_user.id
    products = db_get_all_products(owner)
    if not products:
        await update.message.reply_text(
            "📦 Belum ada produk.\nGunakan /tambah untuk menambah produk."
        )
        return

    # Kelompokkan per kategori
    by_cat = {}
    for p in products:
        cat = p[8]
        by_cat.setdefault(cat, []).append(p)

    lines = [f"📦 *Daftar Produk ({len(products)} item)*\n"]
    for cat, prods in sorted(by_cat.items()):
        lines.append(f"\n{CAT_EMOJI.get(cat,'📦')} *{cat}*")
        for p in prods:
            # p: id,owner,name,price_sell,price_buy,stock,min_stock,unit,category
            stok_icon = "🔴" if p[5] <= p[6] else ("🟡" if p[5] <= p[6]*2 else "🟢")
            lines.append(f"  {stok_icon} #{p[0]} {p[2]} — {fmt_rp(p[3])} | Stok: {p[5]} {p[7]}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_cari(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner   = update.effective_user.id
    keyword = " ".join(ctx.args) if ctx.args else ""
    if not keyword:
        await update.message.reply_text("Contoh: `/cari indomie`", parse_mode="Markdown")
        return
    products = db_search_products(owner, keyword)
    if not products:
        await update.message.reply_text(f"❌ Produk '{keyword}' tidak ditemukan.")
        return
    lines = [f"🔍 *Hasil pencarian '{keyword}':*\n"]
    for p in products:
        margin = p[3] - p[4]
        lines.append(
            f"📦 *#{p[0]} {p[2]}*\n"
            f"   Jual: {fmt_full(p[3])} | Beli: {fmt_full(p[4])} | Margin: {fmt_full(margin)}\n"
            f"   Stok: {p[5]} {p[7]} | Kategori: {p[8]}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /stok ─────────────────────────────────────────────────────────────────────
async def cmd_stok(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner    = update.effective_user.id
    products = db_get_all_products(owner)
    if not products:
        await update.message.reply_text("📦 Belum ada produk.")
        return
    lines = ["📉 *Status Stok Semua Produk*\n"]
    for p in products:
        icon = "🔴" if p[5] <= p[6] else ("🟡" if p[5] <= p[6]*2 else "🟢")
        lines.append(f"{icon} #{p[0]} {p[2]}: *{p[5]}* {p[7]} (min: {p[6]})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_stok_tipis(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner = update.effective_user.id
    prods = db_low_stock(owner)
    if not prods:
        await update.message.reply_text("✅ Semua stok aman!")
        return
    lines = [f"⚠️ *Stok Menipis ({len(prods)} produk)*\n"]
    for p in prods:
        lines.append(f"🔴 {p[2]}: *{p[5]}* {p[7]} (min: {p[6]})")
    lines.append("\nGunakan /stok_masuk untuk update stok.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /stok_masuk (ConversationHandler) ────────────────────────────────────────
async def cmd_stok_masuk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner    = update.effective_user.id
    products = db_get_all_products(owner)
    if not products:
        await update.message.reply_text("📦 Belum ada produk. Tambah dulu dengan /tambah")
        return STOK_MASUK_PRODUK

    lines = ["📥 *Stok Masuk*\n\nKetik ID atau nama produk:\n"]
    for p in products[:20]:
        lines.append(f"#{p[0]} {p[2]} (stok: {p[5]})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    return STOK_MASUK_PRODUK

async def stok_masuk_produk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner = update.effective_user.id
    text  = update.message.text.strip()
    prod  = None

    if text.startswith("#") or text.isdigit():
        pid  = int(text.replace("#",""))
        prod = db_get_product(owner, pid)
    else:
        results = db_search_products(owner, text)
        if results: prod = results[0]

    if not prod:
        await update.message.reply_text("❌ Produk tidak ditemukan. Coba lagi.")
        return STOK_MASUK_PRODUK

    ctx.user_data["stok_masuk_prod"] = prod
    await update.message.reply_text(
        f"📦 *{prod[2]}*\nStok saat ini: *{prod[5]} {prod[7]}*\n\nMasukkan jumlah yang masuk:",
        parse_mode="Markdown"
    )
    return STOK_MASUK_QTY

async def stok_masuk_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    owner = update.effective_user.id
    prod  = ctx.user_data.get("stok_masuk_prod")
    try:
        qty = int(update.message.text.strip())
        if qty <= 0: raise ValueError
    except:
        await update.message.reply_text("⚠️ Masukkan angka positif.")
        return STOK_MASUK_QTY

    db_update_stock(owner, prod[0], qty, "in", "Stok masuk manual")
    new_stock = prod[5] + qty
    await update.message.reply_text(
        f"✅ Stok *{prod[2]}* diupdate!\n"
        f"Sebelum: {prod[5]} → Sesudah: *{new_stock}* {prod[7]}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


# ── /pengeluaran ──────────────────────────────────────────────────────────────
async def cmd_pengeluaran(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💸 *Catat Pengeluaran Toko*\n\n"
        "Ketik pengeluaran dengan bebas:\n"
        "• `beli rokok gudang garam 500rb`\n"
        "• `bayar listrik 200 ribu`\n"
        "• `gaji karyawan 2jt`\n"
        "• `beli plastik kresek 50rb`",
        parse_mode="Markdown"
    )

async def handle_pengeluaran(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handle pengeluaran jika user dalam mode pengeluaran — dipanggil dari message handler."""
    pass  # Dihandle di handle_message


# ── /laporan ──────────────────────────────────────────────────────────────────
async def cmd_laporan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📅 Hari Ini",      callback_data="lap_daily"),
         InlineKeyboardButton("📆 7 Hari",         callback_data="lap_weekly")],
        [InlineKeyboardButton("🗓️ Bulan Ini",     callback_data="lap_monthly"),
         InlineKeyboardButton("🏆 Produk Terlaris", callback_data="lap_terlaris")],
    ]
    await update.message.reply_text(
        "📊 Pilih laporan:", reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_laporan(update: Update, period: str, owner: int):
    now  = datetime.now()
    is_cb = hasattr(update, "callback_query") and update.callback_query
    send  = update.callback_query.edit_message_text if is_cb else update.message.reply_text

    if period == "terlaris":
        with sqlite3.connect(DB_PATH) as c:
            rows = c.execute("""
                SELECT si.product_name, SUM(si.qty) as total_qty, SUM(si.subtotal) as total_rev
                FROM sale_items si
                JOIN sales s ON si.sale_id=s.id
                WHERE s.owner_id=?
                GROUP BY si.product_name ORDER BY total_qty DESC LIMIT 10
            """, (owner,)).fetchall()
        if not rows:
            await send("📭 Belum ada data penjualan.", parse_mode="Markdown")
            return
        lines = ["🏆 *Produk Terlaris*\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {r[0]} — {r[1]} terjual ({fmt_rp(r[2])})")
        await send("\n".join(lines), parse_mode="Markdown")
        return

    if period == "daily":
        since = now.strftime("%Y-%m-%d")
        label = "Hari Ini"
    elif period == "weekly":
        since = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        label = "7 Hari Terakhir"
    else:
        since = now.strftime("%Y-%m-01")
        label = now.strftime("%B %Y")

    sales = db_get_sales(owner, since=since)
    expenses = db_get_monthly_expenses(owner) if period == "monthly" else []

    if not sales:
        await send(f"📭 Tidak ada penjualan untuk *{label}*.", parse_mode="Markdown")
        return

    # Hitung omzet & laba kotor
    total_omzet = sum(s[2] for s in sales)
    total_bayar = sum(s[3] for s in sales)

    # Hitung HPP dari sale_items
    hpp = 0
    with sqlite3.connect(DB_PATH) as c:
        for s in sales:
            items = c.execute("SELECT product_id, qty FROM sale_items WHERE sale_id=?", (s[0],)).fetchall()
            for item in items:
                if item[0]:
                    prod = c.execute("SELECT price_buy FROM products WHERE id=?", (item[0],)).fetchone()
                    if prod: hpp += prod[0] * item[1]

    laba_kotor = total_omzet - hpp
    total_exp  = sum(e[2] for e in expenses) if expenses else 0
    laba_bersih = laba_kotor - total_exp

    lines = [
        f"📊 *Laporan {label}*\n",
        f"🛒 Jumlah Transaksi : *{len(sales)}*",
        f"💰 Total Omzet      : *{fmt_full(total_omzet)}*",
        f"📦 HPP (Modal)      : *{fmt_full(hpp)}*",
        f"📈 Laba Kotor       : *{fmt_full(laba_kotor)}*",
    ]
    if period == "monthly" and total_exp:
        lines += [
            f"💸 Total Pengeluaran: *{fmt_full(total_exp)}*",
            f"✅ Laba Bersih      : *{fmt_full(laba_bersih)}*",
        ]
    lines.append(f"\n🕐 *5 Transaksi Terakhir:*")
    for s in sales[:5]:
        dt = datetime.strptime(s[6], "%Y-%m-%d %H:%M:%S").strftime("%d/%m %H:%M") if len(s[6]) > 10 else s[6]
        lines.append(f"#{s[0]:04d} {dt} — {fmt_rp(s[2])}")

    await send("\n".join(lines), parse_mode="Markdown")


# ── /export ───────────────────────────────────────────────────────────────────
async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🛒 Penjualan", callback_data="exp_sales"),
         InlineKeyboardButton("📦 Produk",    callback_data="exp_products")],
        [InlineKeyboardButton("💸 Pengeluaran", callback_data="exp_expenses")],
    ]
    await update.message.reply_text("📥 Export data apa?", reply_markup=InlineKeyboardMarkup(keyboard))


# ── General Message Handler ───────────────────────────────────────────────────
async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text  = update.message.text.strip()
    owner = update.effective_user.id

    # ── Mode setting pembayaran (dari callback set_qris / set_rekening) ───
    setting_mode = ctx.user_data.get("setting_mode")
    if setting_mode == "qris":
        parts  = text.split(None, 1)
        config = get_payment_config(owner)
        config["qris_name"]   = parts[0] if parts else ""
        config["qris_number"] = parts[1] if len(parts) > 1 else ""
        save_payment_config(owner, config)
        ctx.user_data.pop("setting_mode", None)
        await update.message.reply_text(
            f"✅ QRIS disimpan!\n📱 *{config['qris_name']}* — `{config['qris_number']}`",
            parse_mode="Markdown"
        )
        return

    if setting_mode == "rekening":
        parts  = text.split(None, 2)
        config = get_payment_config(owner)
        if len(parts) >= 3:
            config["bank_name"], config["bank_number"], config["bank_holder"] = parts
            save_payment_config(owner, config)
            ctx.user_data.pop("setting_mode", None)
            await update.message.reply_text(
                f"✅ Rekening disimpan!\n🏦 *{config['bank_name']}* `{config['bank_number']}` a/n *{config['bank_holder']}*",
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("⚠️ Format salah. Contoh: `BRI 123456789012 Siti Rahma`", parse_mode="Markdown")
        return

    # ── Mode tunggu konfirmasi QRIS/Transfer ──────────────────────────────
    awaiting = ctx.user_data.get("awaiting")
    if awaiting == "tunai":
        # Proses bayar tunai
        cart  = ctx.user_data.get("cart", [])
        total = ctx.user_data.get("jual_total", 0)
        clean = text.replace(".","").replace(",","")
        try:
            payment = int(clean)
        except ValueError:
            await update.message.reply_text("⚠️ Masukkan angka saja. Contoh: `50000`")
            return
        if payment < total:
            await update.message.reply_text(
                f"⚠️ Kurang! Total *{fmt_full(total)}*, bayar *{fmt_full(payment)}*.",
                parse_mode="Markdown"
            )
            return
        sale_id, change = db_create_sale(owner, cart, total, payment, "tunai")
        ctx.user_data["cart"] = []
        ctx.user_data.pop("awaiting", None)
        receipt = make_receipt(sale_id, cart, total, payment, change, "tunai")
        await update.message.reply_text(receipt, parse_mode="Markdown")
        return

    if awaiting == "nontunai":
        method = ctx.user_data.get("pay_method", "transfer")
        if text.lower() in ["sudah","lunas","ok","oke","ya","done","✅"]:
            cart    = ctx.user_data.get("cart", [])
            total   = ctx.user_data.get("jual_total", 0)
            sale_id, _ = db_create_sale(owner, cart, total, total, method)
            ctx.user_data["cart"] = []
            ctx.user_data.pop("awaiting", None)
            receipt = make_receipt(sale_id, cart, total, total, 0, method)
            await update.message.reply_text(
                f"✅ *Pembayaran dikonfirmasi!*\n\n{receipt}", parse_mode="Markdown"
            )
        else:
            method_label = "QRIS" if method == "qris" else "Transfer"
            await update.message.reply_text(
                f"Ketik *sudah* setelah pembayaran {method_label} diterima.",
                parse_mode="Markdown"
            )
        return

    # ── Mode normal: parse sebagai pengeluaran ────────────────────────────
    await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        parsed = ai_parse_expense(text)
    except Exception:
        await update.message.reply_text(
            "🤔 Tidak dipahami.\n\n"
            "Untuk *penjualan* → /jual\n"
            "Untuk *pengeluaran* → ketik langsung contoh: `beli gula 50rb`\n"
            "Untuk *tambah produk* → /tambah",
            parse_mode="Markdown"
        )
        return

    if not parsed.get("success"):
        await update.message.reply_text(
            "🤔 Tidak dipahami sebagai pengeluaran.\n\n"
            "Contoh: `beli rokok 500rb`, `bayar listrik 200rb`\n"
            "Untuk penjualan gunakan /jual",
            parse_mode="Markdown"
        )
        return

    db_add_expense(owner, parsed["amount"], parsed["category"], parsed["description"])
    expenses  = db_get_monthly_expenses(owner)
    total_exp = sum(e[2] for e in expenses)

    await update.message.reply_text(
        f"{parsed.get('reply','✅ Pengeluaran dicatat!')}\n\n"
        f"💸 *{parsed['description']}*\n"
        f"💰 {fmt_full(parsed['amount'])}\n"
        f"📂 {parsed['category']}\n"
        f"📅 {today()}\n\n"
        f"─────────────────\n"
        f"📊 Total pengeluaran bulan ini: *{fmt_full(total_exp)}*",
        parse_mode="Markdown"
    )


# ── Callback Router ───────────────────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    owner = query.from_user.id
    data  = query.data

    if data.startswith("lap_"):
        await show_laporan(update, data.replace("lap_",""), owner)

    elif data.startswith("cat_"):
        await produk_kategori_callback(update, ctx)

    elif data.startswith("pay_"):
        method = data.replace("pay_","")
        cart   = ctx.user_data.get("cart", [])
        total  = ctx.user_data.get("jual_total", 0)
        ctx.user_data["pay_method"] = method

        if method == "tunai":
            await query.edit_message_text(
                f"{cart_summary(cart)}\n\n"
                f"💵 *Pembayaran Tunai*\nMasukkan nominal uang yang dibayar:",
                parse_mode="Markdown"
            )
            # Set state ke JUAL_BAYAR_TUNAI via context
            ctx.user_data["awaiting"] = "tunai"

        elif method == "qris":
            config = get_payment_config(owner)
            if not config["qris_number"]:
                await query.edit_message_text(
                    "⚠️ Info QRIS belum diset!\nGunakan /setting_pembayaran untuk mengatur QRIS dulu.",
                    parse_mode="Markdown"
                )
                return
            await query.edit_message_text(
                f"📱 *Pembayaran via QRIS*\n\n"
                f"💰 Total: *{fmt_full(total)}*\n\n"
                f"Dompet  : *{config['qris_name']}*\n"
                f"No. HP  : `{config['qris_number']}`\n\n"
                f"Minta pembeli scan QR / transfer ke nomor di atas.\n"
                f"Ketik *sudah* setelah pembayaran diterima.",
                parse_mode="Markdown"
            )
            ctx.user_data["awaiting"] = "nontunai"

        elif method == "transfer":
            config = get_payment_config(owner)
            if not config["bank_number"]:
                await query.edit_message_text(
                    "⚠️ Rekening belum diset!\nGunakan /setting_pembayaran untuk mengatur rekening dulu.",
                    parse_mode="Markdown"
                )
                return
            await query.edit_message_text(
                f"🏦 *Pembayaran via Transfer*\n\n"
                f"💰 Total: *{fmt_full(total)}*\n\n"
                f"Bank    : *{config['bank_name']}*\n"
                f"No. Rek : `{config['bank_number']}`\n"
                f"a/n     : *{config['bank_holder']}*\n\n"
                f"Minta pembeli transfer ke rekening di atas.\n"
                f"Ketik *sudah* setelah pembayaran diterima.",
                parse_mode="Markdown"
            )
            ctx.user_data["awaiting"] = "nontunai"

    elif data == "set_qris":
        await query.edit_message_text(
            "📱 *Atur Info QRIS*\n\nKetik format:\n`NamaDompet NomorHP`\n\nContoh:\n`GoPay 081234567890`",
            parse_mode="Markdown"
        )
        ctx.user_data["setting_mode"] = "qris"

    elif data == "set_rekening":
        await query.edit_message_text(
            "🏦 *Atur Rekening Transfer*\n\nKetik format:\n`NamaBank NomorRek NamaPemilik`\n\nContoh:\n`BRI 123456789012 Siti Rahma`",
            parse_mode="Markdown"
        )
        ctx.user_data["setting_mode"] = "rekening"

    elif data.startswith("exp_"):
        kind = data.replace("exp_","")
        output = io.StringIO()
        writer = csv.writer(output)

        if kind == "sales":
            writer.writerow(["ID","Tanggal","Total","Bayar","Kembalian","Metode"])
            for s in db_get_sales(owner):
                writer.writerow([s[0], s[6], s[2], s[3], s[4], s[5] if len(s)>5 else "tunai"])
            filename = f"penjualan_{today()}.csv"
            caption  = "🛒 Data Penjualan"

        elif kind == "products":
            writer.writerow(["ID","Nama","Harga Jual","Harga Beli","Stok","Min Stok","Satuan","Kategori"])
            for p in db_get_all_products(owner):
                writer.writerow([p[0], p[2], p[3], p[4], p[5], p[6], p[7], p[8]])
            filename = f"produk_{today()}.csv"
            caption  = "📦 Data Produk"

        elif kind == "expenses":
            writer.writerow(["ID","Tanggal","Jumlah","Kategori","Deskripsi"])
            for e in db_get_monthly_expenses(owner):
                writer.writerow([e[0], e[5], e[2], e[3], e[4]])
            filename = f"pengeluaran_{today()}.csv"
            caption  = "💸 Data Pengeluaran"

        output.seek(0)
        await query.message.reply_document(
            document=output.getvalue().encode("utf-8-sig"),
            filename=filename,
            caption=f"📥 {caption} — {now_str()}"
        )


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    init_db()
    print("✅ Database POS siap")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # ConversationHandler: Jual
    jual_conv = ConversationHandler(
        entry_points=[CommandHandler("jual", cmd_jual)],
        states={
            JUAL_ITEM:            [MessageHandler(filters.TEXT & ~filters.COMMAND, jual_item)],
            JUAL_PILIH_BAYAR:     [MessageHandler(filters.TEXT & ~filters.COMMAND, jual_item)],
            JUAL_BAYAR_TUNAI:     [MessageHandler(filters.TEXT & ~filters.COMMAND, jual_bayar_tunai)],
            JUAL_KONFIRM_TRANSFER:[MessageHandler(filters.TEXT & ~filters.COMMAND, jual_konfirm_transfer)],
        },
        fallbacks=[
            CommandHandler("batal", jual_cancel),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
    )

    # ConversationHandler: Setting Pembayaran
    setting_conv = ConversationHandler(
        entry_points=[CommandHandler("setting_pembayaran", cmd_setting_pembayaran)],
        states={
            SETTING_QRIS:     [MessageHandler(filters.TEXT & ~filters.COMMAND, setting_qris_save)],
            SETTING_REKENING: [MessageHandler(filters.TEXT & ~filters.COMMAND, setting_rekening_save)],
        },
        fallbacks=[
            CommandHandler("batal", jual_cancel),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
    )

    # ConversationHandler: Tambah Produk
    tambah_conv = ConversationHandler(
        entry_points=[CommandHandler("tambah", cmd_tambah)],
        states={
            PRODUK_NAMA:       [MessageHandler(filters.TEXT & ~filters.COMMAND, produk_nama)],
            PRODUK_HARGA_JUAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, produk_harga_jual)],
            PRODUK_HARGA_BELI: [MessageHandler(filters.TEXT & ~filters.COMMAND, produk_harga_beli)],
            PRODUK_STOK:       [MessageHandler(filters.TEXT & ~filters.COMMAND, produk_stok)],
            PRODUK_SATUAN:     [
                MessageHandler(filters.TEXT & ~filters.COMMAND, produk_satuan),
                CallbackQueryHandler(produk_kategori_callback, pattern="^cat_"),
            ],
        },
        fallbacks=[
            CommandHandler("batal", jual_cancel),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
    )

    # ConversationHandler: Stok Masuk
    stok_conv = ConversationHandler(
        entry_points=[CommandHandler("stok_masuk", cmd_stok_masuk)],
        states={
            STOK_MASUK_PRODUK: [MessageHandler(filters.TEXT & ~filters.COMMAND, stok_masuk_produk)],
            STOK_MASUK_QTY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, stok_masuk_qty)],
        },
        fallbacks=[
            CommandHandler("batal", jual_cancel),
            CommandHandler("start", cmd_start),
        ],
        allow_reentry=True,
    )

    app.add_handler(jual_conv)
    app.add_handler(tambah_conv)
    app.add_handler(stok_conv)
    app.add_handler(setting_conv)

    app.add_handler(CommandHandler("start",               cmd_start))
    app.add_handler(CommandHandler("bantuan",             cmd_bantuan))
    app.add_handler(CommandHandler("produk",              cmd_produk))
    app.add_handler(CommandHandler("cari",                cmd_cari))
    app.add_handler(CommandHandler("stok",                cmd_stok))
    app.add_handler(CommandHandler("stok_tipis",          cmd_stok_tipis))
    app.add_handler(CommandHandler("laporan",             cmd_laporan))
    app.add_handler(CommandHandler("pengeluaran",         cmd_pengeluaran))
    app.add_handler(CommandHandler("setting_pembayaran",  cmd_setting_pembayaran))
    app.add_handler(CommandHandler("export",              cmd_export))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🏪 POS Bot berjalan dengan Gemini AI (GRATIS)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
