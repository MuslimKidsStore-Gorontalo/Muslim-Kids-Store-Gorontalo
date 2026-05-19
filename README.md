# 🏪 POS Toko Kelontong AI — Telegram Bot

Aplikasi Point of Sale (POS) lengkap untuk toko kelontong berbasis Telegram.
Powered by Google Gemini AI (GRATIS).

---

## ✨ Fitur Lengkap

| Fitur | Detail |
|---|---|
| 🛒 Kasir / Penjualan | Tambah item, hitung total, cetak struk, kembalian otomatis |
| 📦 Manajemen Produk | Tambah, cari, edit produk dengan harga jual & beli |
| 📉 Manajemen Stok | Cek stok, stok masuk, alert stok tipis |
| 📊 Laporan | Omzet, laba kotor, laba bersih, produk terlaris |
| 💸 Pengeluaran | Catat pengeluaran toko dengan AI |
| 📥 Export CSV | Export penjualan, produk, pengeluaran |
| 🤖 AI Parsing | Input bahasa natural, AI yang mengerti konteks |
| 👥 Multi-user | Setiap pemilik toko punya data sendiri |

---

## 🚀 Cara Deploy

### 1. Buat Bot Telegram
- Buka **@BotFather** → `/newbot` → salin token

### 2. Dapatkan Gemini API Key (Gratis)
- Buka https://aistudio.google.com/app/apikey
- Login Google → **Create API Key** → salin

### 3. Deploy ke Railway (Gratis)
1. Upload folder ini ke GitHub
2. Buka https://railway.app → **New Project** → pilih repo
3. Set environment variables:
   - `TELEGRAM_BOT_TOKEN` = token dari BotFather
   - `GEMINI_API_KEY` = key dari Google AI Studio
4. Railway otomatis deploy ✅

### Atau Jalankan Lokal
```bash
pip install -r requirements.txt
cp .env.example .env  # isi token & api key
python bot.py
```

---

## 📱 Panduan Penggunaan

### 🛒 Penjualan
```
/jual
→ indomie goreng 3
→ aqua 600ml 2, teh botol 1
→ selesai
→ 50000   (nominal bayar)
Bot akan cetak struk + kembalian otomatis
```

### 📦 Produk
```
/tambah          — tambah produk baru (wizard step-by-step)
/produk          — lihat semua produk per kategori
/cari indomie    — cari produk
```

### 📉 Stok
```
/stok            — lihat semua stok dengan status warna
/stok_masuk      — catat barang masuk dari supplier
/stok_tipis      — produk yang perlu segera direstok
```

### 💸 Pengeluaran
```
Ketik langsung (tanpa command):
beli rokok gudang garam 500rb
bayar listrik 200 ribu
gaji karyawan 2jt
```

### 📊 Laporan
```
/laporan → pilih:
- Hari Ini
- 7 Hari
- Bulan Ini (termasuk laba bersih)
- Produk Terlaris
```

### 📥 Export
```
/export → pilih:
- Penjualan (CSV)
- Produk (CSV)
- Pengeluaran (CSV)
```

---

## 🎨 Kategori Produk
Minuman, Makanan, Snack, Rokok, Sembako, Kebersihan, Perawatan, Bumbu, Frozen, Lainnya

---

## 🔒 Keamanan & Privasi
- Data tersimpan di SQLite lokal di servermu
- Setiap pemilik toko data terpisah by Telegram user_id
- Tidak ada data yang dikirim selain ke Gemini API untuk parsing

---

Dibuat dengan ❤️ untuk toko kelontong Indonesia
