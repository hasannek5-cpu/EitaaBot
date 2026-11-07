import os
import re
import secrets
import logging
import sqlite3
from datetime import datetime
from urllib.parse import quote

import requests
from flask import (
    Flask, request, redirect, url_for,
    render_template_string, jsonify, make_response, session
)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


# ==================================================
# تنظیمات اصلی
# ==================================================
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KalayKitchen")

APP_NAME = "کالای آشپزخانه لوکس"


# ==================================================
# اتصال دیتابیس
# ==================================================
def using_postgres():
    return bool(os.getenv("DATABASE_URL", "").strip())


def get_connection():
    database_url = os.getenv("DATABASE_URL", "").strip()

    if database_url:
        if psycopg is None:
            raise RuntimeError("psycopg نصب نیست")

        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)

        return psycopg.connect(
            database_url,
            row_factory=dict_row,
            connect_timeout=20
        )

    conn = sqlite3.connect(
        os.getenv("DATABASE_PATH", "orders.db"),
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    return conn


def execute_sql(conn, sql, params=()):
    if using_postgres():
        sql = sql.replace("?", "%s")
    return conn.execute(sql, params)


# ==================================================
# ایجاد/تکمیل جدول؛ بدون حذف اطلاعات قبلی
# ==================================================
def init_db():
    conn = get_connection()
    try:
        if using_postgres():
            execute_sql(conn, """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT '',
                    product TEXT NOT NULL DEFAULT '',
                    customer_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'جدید',
                    notified INTEGER NOT NULL DEFAULT 0,
                    description TEXT,
                    eitaa_sent INTEGER DEFAULT 0,
                    eitaa_error TEXT,
                    price NUMERIC DEFAULT 0,
                    total NUMERIC DEFAULT 0
                )
            """)

            migrations = [
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS product TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS address TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS notes TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'جدید'",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS notified INTEGER DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS description TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS eitaa_sent INTEGER DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS eitaa_error TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS price NUMERIC DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS total NUMERIC DEFAULT 0",
            ]
            for sql in migrations:
                execute_sql(conn, sql)

            rows = execute_sql(conn, """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name='orders'
            """).fetchall()
            columns = {row["column_name"] for row in rows}

            if "notes" in columns and "description" in columns:
                execute_sql(conn, """
                    UPDATE orders
                    SET description = COALESCE(NULLIF(description, ''), notes, '')
                    WHERE description IS NULL OR description = ''
                """)

            if "notified" in columns and "eitaa_sent" in columns:
                execute_sql(conn, """
                    UPDATE orders SET eitaa_sent = notified
                    WHERE notified IS NOT NULL
                    AND (eitaa_sent IS NULL OR eitaa_sent = 0)
                """)
        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL DEFAULT '',
                    product TEXT NOT NULL DEFAULT '',
                    customer_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    notes TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'جدید',
                    notified INTEGER NOT NULL DEFAULT 0,
                    description TEXT,
                    eitaa_sent INTEGER DEFAULT 0,
                    eitaa_error TEXT,
                    price NUMERIC DEFAULT 0,
                    total NUMERIC DEFAULT 0
                )
            """)

            existing = {
                row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
            }
            sqlite_migrations = {
                "created_at": "TEXT DEFAULT ''",
                "product": "TEXT DEFAULT ''",
                "customer_name": "TEXT DEFAULT ''",
                "phone": "TEXT DEFAULT ''",
                "address": "TEXT DEFAULT ''",
                "quantity": "INTEGER DEFAULT 1",
                "notes": "TEXT DEFAULT ''",
                "status": "TEXT DEFAULT 'جدید'",
                "notified": "INTEGER DEFAULT 0",
                "description": "TEXT",
                "eitaa_sent": "INTEGER DEFAULT 0",
                "eitaa_error": "TEXT",
                "price": "NUMERIC DEFAULT 0",
                "total": "NUMERIC DEFAULT 0",
            }
            for column, definition in sqlite_migrations.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

            conn.execute("""
                UPDATE orders
                SET description = COALESCE(NULLIF(description, ''), notes, '')
                WHERE description IS NULL OR description = ''
            """)
            conn.execute("""
                UPDATE orders SET eitaa_sent = notified
                WHERE notified IS NOT NULL
                AND (eitaa_sent IS NULL OR eitaa_sent = 0)
            """)

        conn.commit()
        logger.info("Database initialized successfully")
    except Exception:
        conn.rollback()
        logger.exception("Database initialization failed")
        raise
    finally:
        conn.close()


# ==================================================
# ثبت سفارش و خواندن سفارش‌ها
# ==================================================
def save_order(data):
    conn = get_connection()
    try:
        order_id = secrets.token_hex(12)
        created_at = datetime.utcnow().isoformat()

        sql = """
            INSERT INTO orders (
                id, created_at, product, customer_name, phone, address,
                quantity, notes, status, notified, description,
                eitaa_sent, eitaa_error, price, total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        description = data.get("description", "")
        params = (
            order_id, created_at, data["product"], data["customer_name"],
            data["phone"], data.get("address", ""), data["quantity"],
            description, "جدید", 0, description, 0, None,
            data["price"], data["total"]
        )
        execute_sql(conn, sql, params)
        conn.commit()
        logger.info("Order saved: %s", order_id)
        return order_id
    except Exception:
        conn.rollback()
        logger.exception("SAVE ORDER ERROR")
        raise
    finally:
        conn.close()


def fetch_order(order_id):
    conn = get_connection()
    try:
        row = execute_sql(
            conn, "SELECT * FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_orders(limit=100):
    conn = get_connection()
    try:
        rows = execute_sql(
            conn, "SELECT * FROM orders ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_eitaa_result(order_id, sent, error=None):
    conn = get_connection()
    try:
        execute_sql(conn, """
            UPDATE orders SET eitaa_sent = ?, notified = ?, eitaa_error = ?
            WHERE id = ?
        """, (1 if sent else 0, 1 if sent else 0, error, order_id))
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Eitaa status update failed")
        raise
    finally:
        conn.close()


# ==================================================
# پیام و ارسال ایتا
# ==================================================
def money(value):
    try:
        return f"{float(value or 0):,.0f}"
    except (ValueError, TypeError):
        return str(value)


def make_order_message(order):
    return (
        "🛒 سفارش جدید کالای آشپزخانه لوکس\n"
        "━━━━━━━━━━━━━━\n"
        f"شماره سفارش: {order['id']}\n"
        f"نام مشتری: {order['customer_name']}\n"
        f"تلفن: {order['phone']}\n"
        f"محصول: {order['product']}\n"
        f"تعداد: {order['quantity']}\n"
        f"قیمت واحد: {money(order.get('price'))} تومان\n"
        f"مبلغ کل: {money(order.get('total'))} تومان\n"
        f"آدرس: {order.get('address') or 'ثبت نشده'}\n"
        f"توضیحات: {order.get('description') or order.get('notes') or 'ندارد'}\n"
        "━━━━━━━━━━━━━━"
    )


def send_eitaa_message(message):
    """ارسال پیام از طریق API ایتایار؛ توکن فقط از Environment خوانده می‌شود."""
    token = (os.getenv("EITAA_TOKEN") or "").strip()
    chat_id = (os.getenv("EITAA_CHAT_ID") or "").strip()

    if not token:
        logger.error("EITAA_TOKEN missing at runtime")
        return False, "EITAA_TOKEN در Render خالی است"
    if not chat_id:
        logger.error("EITAA_CHAT_ID missing at runtime")
        return False, "EITAA_CHAT_ID در Render خالی است"

    api_url = "https://eitaayar.ir/api/" + quote(token, safe="") + "/sendMessage"

    try:
        response = requests.post(
            api_url,
            data={"chat_id": chat_id, "text": message},
            timeout=30
        )
        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            logger.error("Eitaa returned non-JSON response: %s", response.text[:500])
            return False, "پاسخ ایتایار JSON معتبر نیست"

        if not isinstance(result, dict) or result.get("ok") is not True:
            logger.error("Eitaa API rejected message: %s", result)
            return False, str(result)[:1000]

        logger.info("Eitaa message sent successfully")
        return True, None

    except requests.RequestException as exc:
        logger.exception("Eitaa HTTP/network error")
        return False, str(exc)[:1000]
    except Exception as exc:
        logger.exception("Eitaa sending failed")
        return False, str(exc)[:1000]


def notify_order(order_id):
    order = fetch_order(order_id)
    if not order:
        return False, "سفارش در دیتابیس پیدا نشد"

    sent, error = send_eitaa_message(make_order_message(order))
    try:
        update_eitaa_result(order_id, sent, error)
    except Exception:
        logger.exception("Could not save Eitaa result for order %s", order_id)
    return sent, error


# ==================================================
# اعداد فارسی و اعتبارسنجی
# ==================================================
def normalize_digits(value):
    trans = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return (
        str(value or "").translate(trans)
        .replace(",", "").replace("٬", "").strip()
    )


def parse_int(value, default=0):
    cleaned = normalize_digits(value)
    if not cleaned:
        return default
    if not re.fullmatch(r"\d+", cleaned):
        raise ValueError("عدد نامعتبر است")
    return int(cleaned)


# ==================================================
# صفحه سفارش - طراحی لوکس و واکنش‌گرا
# ==================================================
ORDER_PAGE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#173b35">
<meta name="description" content="فروش آنلاین کالای آشپزخانه لوکس و ثبت سفارش سریع">
<link rel="manifest" href="/manifest.json">
<title>کالای آشپزخانه لوکس</title>
<style>
:root{--green:#173b35;--green2:#24574c;--gold:#c5a46d;--cream:#f7f4ed;--ink:#202820;--muted:#778078;--line:#e7e6df}
*{box-sizing:border-box}
body{margin:0;background:var(--cream);color:var(--ink);font-family:Tahoma,Arial,sans-serif;line-height:1.8}
.topbar{background:linear-gradient(135deg,#102d27,#24574c);color:#fff;padding:30px 18px 34px;text-align:center;position:relative;overflow:hidden}
.topbar:before,.topbar:after{content:"";position:absolute;border:1px solid #ffffff18;border-radius:50%;width:220px;height:220px;pointer-events:none}
.topbar:before{top:-145px;right:-70px}.topbar:after{bottom:-180px;left:-60px}
.brand-mark{width:58px;height:58px;margin:0 auto 12px;border:1px solid #d7bd8b;border-radius:18px;display:grid;place-items:center;color:#e6c98f;font-size:27px;background:#ffffff0b}
.topbar h1{font-size:clamp(21px,5vw,29px);margin:0;font-weight:800;letter-spacing:-.4px}
.topbar p{margin:8px 0 0;color:#e5ece8;font-size:14px}
.gold-line{width:62px;height:2px;background:var(--gold);margin:17px auto 0;border-radius:9px}
.wrap{max-width:650px;margin:0 auto;padding:18px 14px 28px}
.intro{display:flex;align-items:center;gap:12px;padding:14px 16px;margin:0 0 14px;border:1px solid #e8dfce;background:#fffaf1;border-radius:16px;color:#65563d;font-size:13px}
.intro-icon{flex:0 0 38px;width:38px;height:38px;display:grid;place-items:center;border-radius:12px;background:#f0e5d0;font-size:19px}
.card{background:#fff;border:1px solid #eeeee8;border-radius:22px;padding:20px;box-shadow:0 12px 35px #173b3509}
.card-head{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.card-head .badge-icon{width:40px;height:40px;display:grid;place-items:center;background:#edf4ef;color:var(--green);border-radius:13px;font-size:20px}
.card-head h2{font-size:18px;margin:0;color:var(--green)}
.card-head p{font-size:12px;color:var(--muted);margin:2px 0 0}
label{display:block;margin:17px 0 7px;font-size:13px;font-weight:700;color:#37443b}
input,textarea{width:100%;padding:13px 14px;border:1px solid var(--line);border-radius:12px;font-size:15px;font-family:inherit;background:#fcfcfa;color:var(--ink);outline:none;transition:border .2s,box-shadow .2s}
input:focus,textarea:focus{border-color:#8eaa9b;box-shadow:0 0 0 3px #173b3510;background:#fff}
input::placeholder,textarea::placeholder{color:#a0a69f;font-size:13px}
textarea{min-height:88px;resize:vertical}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.total-box{display:flex;align-items:center;justify-content:space-between;gap:12px;background:linear-gradient(135deg,#edf4ef,#f5f7f1);border:1px solid #dce9df;border-radius:15px;padding:15px;margin-top:18px;color:var(--green)}
.total-caption{font-size:13px;font-weight:700}.total-value{font-size:20px;font-weight:900;white-space:nowrap}.total-value small{font-size:12px;font-weight:500}
.submit-btn{width:100%;border:0;border-radius:14px;padding:15px;margin-top:20px;background:linear-gradient(135deg,#173b35,#2a6254);color:white;font-size:16px;font-weight:700;font-family:inherit;cursor:pointer;box-shadow:0 8px 18px #173b3520;transition:transform .15s,opacity .15s}
.submit-btn:active{transform:scale(.99)}.submit-btn:disabled{opacity:.65;cursor:wait}
.notice,.error{padding:14px 15px;border-radius:13px;margin:0 0 16px;font-size:13px;overflow-wrap:anywhere}
.notice{background:#edf7ef;border:1px solid #d4ead8;color:#20633a}.error{background:#fff1ef;border:1px solid #f2d5d0;color:#a33125}
.footer{text-align:center;color:#899087;font-size:12px;padding:24px 8px 12px}
.instagram{display:inline-flex;align-items:center;gap:8px;margin-top:13px;padding:11px 18px;border-radius:13px;background:linear-gradient(120deg,#833ab4,#fd1d1d,#fcb045);color:white;text-decoration:none;font-weight:700;font-size:13px;box-shadow:0 7px 16px #c43b6920}
.footer-note{margin-top:12px;color:#a0a59e}
@media(max-width:420px){.card{padding:16px;border-radius:18px}.two-col{gap:9px}.topbar{padding-top:25px}.total-value{font-size:18px}}
</style>
</head>
<body>
<header class="topbar">
<div class="brand-mark">✦</div>
<h1>کالای آشپزخانه لوکس</h1>
<p>انتخابی زیبا برای آشپزخانه شما</p>
<div class="gold-line"></div>
</header>
<main class="wrap">
<div class="intro"><div class="intro-icon">✨</div><div>سفارش خود را در چند مرحله ساده ثبت کنید؛ مبلغ نهایی به‌صورت خودکار محاسبه می‌شود.</div></div>
<section class="card">
<div class="card-head"><div class="badge-icon">🛍️</div><div><h2>فرم ثبت سفارش</h2><p>لطفاً اطلاعات سفارش را دقیق وارد کنید.</p></div></div>
{% if message %}<div class="notice">✓ {{ message }}</div>{% endif %}
{% if error %}<div class="error">⚠ {{ error }}</div>{% endif %}
<form id="orderForm" method="POST" action="/order">
<label for="customer_name">نام و نام خانوادگی <span style="color:#b45309">*</span></label>
<input id="customer_name" name="customer_name" required maxlength="150" autocomplete="name" placeholder="مثلاً علی رضایی">
<label for="phone">شماره تلفن <span style="color:#b45309">*</span></label>
<input id="phone" name="phone" required maxlength="30" inputmode="tel" autocomplete="tel" placeholder="09xxxxxxxxx">
<label for="product">نام محصول <span style="color:#b45309">*</span></label>
<input id="product" name="product" required maxlength="250" placeholder="نام کالای موردنظر">
<div class="two-col">
<div><label for="price">قیمت واحد (تومان) <span style="color:#b45309">*</span></label><input id="price" name="price" type="text" inputmode="numeric" required placeholder="مثلاً ۳۰۰۰۰۰"></div>
<div><label for="quantity">تعداد <span style="color:#b45309">*</span></label><input id="quantity" name="quantity" type="number" min="1" max="100000" value="1" required></div>
</div>
<div class="total-box"><div class="total-caption">مبلغ نهایی سفارش</div><div class="total-value"><span id="total">۰</span> <small>تومان</small></div></div>
<label for="address">آدرس تحویل</label><textarea id="address" name="address" maxlength="2000" autocomplete="street-address" placeholder="آدرس کامل برای تحویل سفارش"></textarea>
<label for="description">توضیحات سفارش</label><textarea id="description" name="description" maxlength="2000" placeholder="رنگ، مدل یا توضیح تکمیلی (اختیاری)"></textarea>
<button class="submit-btn" id="submitBtn" type="submit">ثبت نهایی سفارش ←</button>
</form>
</section>
<footer class="footer">کالای آشپزخانه لوکس<br>
<a class="instagram" href="https://www.instagram.com/kala_louxs/" target="_blank" rel="noopener noreferrer">📷 مشاهده پیج اینستاگرام ما</a>
<div class="footer-note">تجربه خریدی ساده، سریع و مطمئن</div>
</footer>
</main>
<script>
const p=document.getElementById('price'),q=document.getElementById('quantity'),t=document.getElementById('total');
const form=document.getElementById('orderForm'),btn=document.getElementById('submitBtn');
function digits(x){return String(x).replace(/[۰-۹]/g,d=>'۰۱۲۳۴۵۶۷۸۹'.indexOf(d)).replace(/[٠-٩]/g,d=>'٠١٢٣٤٥٦٧٨٩'.indexOf(d));}
function calc(){
 const a=parseInt(digits(p.value).replace(/[٬,\\s]/g,''))||0;
 const b=parseInt(digits(q.value))||0;
 t.textContent=(a*b).toLocaleString('fa-IR');
}
p.addEventListener('input',calc);q.addEventListener('input',calc);
form.addEventListener('submit',()=>{btn.disabled=true;btn.textContent='در حال ثبت سفارش…';});
calc();
</script>
</body></html>
"""


@app.get("/")
def index():
    return render_template_string(
        ORDER_PAGE,
        message=request.args.get("message", ""),
        error=request.args.get("error", "")
    )


# ==================================================
# ثبت سفارش فرم
# ==================================================
@app.post("/order")
def create_order():
    try:
        customer_name = request.form.get("customer_name", "").strip()
        phone = request.form.get("phone", "").strip()
        product = request.form.get("product", "").strip()
        address = request.form.get("address", "").strip()
        description = request.form.get("description", "").strip()
        price = parse_int(request.form.get("price"))
        quantity = parse_int(request.form.get("quantity"), 1)

        if not customer_name or not phone or not product:
            return redirect(url_for("index", error="لطفاً اطلاعات ضروری را کامل وارد کنید"))
        if len(phone) < 7:
            return redirect(url_for("index", error="شماره تلفن صحیح نیست"))
        if price < 0 or quantity < 1 or quantity > 100000:
            return redirect(url_for("index", error="قیمت یا تعداد نامعتبر است"))

        total_amount = price * quantity

        order_id = save_order({
            "customer_name": customer_name,
            "phone": phone,
            "product": product,
            "quantity": quantity,
            "price": price,
            "total": total_amount,
            "address": address,
            "description": description
        })

        sent, error = notify_order(order_id)

        if sent:
            message = (
                f"سفارش شماره {order_id} ثبت و به ایتا ارسال شد. "
                f"مبلغ کل: {money(total_amount)} تومان"
            )
        else:
            message = (
                f"سفارش شماره {order_id} در دیتابیس ثبت شد؛ "
                f"مبلغ کل: {money(total_amount)} تومان؛ "
                f"ارسال ایتا ناموفق بود: {error}"
            )

        return redirect(url_for("index", message=message))

    except ValueError:
        return redirect(url_for("index", error="قیمت و تعداد باید عدد باشند"))
    except Exception:
        logger.exception("Order submission failed")
        return redirect(url_for("index", error="خطا در ذخیره سفارش. Application Logs را بررسی کنید."))


# ==================================================
# API ثبت سفارش
# ==================================================
@app.post("/api/order")
def api_create_order():
    try:
        data = request.get_json(silent=True) or {}
        price = parse_int(data.get("price"))
        quantity = parse_int(data.get("quantity"), 1)
        customer_name = str(data.get("customer_name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        product = str(data.get("product", "")).strip()

        if (
            not customer_name or not phone or not product
            or price < 0 or quantity < 1 or quantity > 100000
        ):
            return jsonify(ok=False, error="اطلاعات سفارش نامعتبر است"), 400

        total_amount = price * quantity
        order_id = save_order({
            "customer_name": customer_name,
            "phone": phone,
            "product": product,
            "quantity": quantity,
            "price": price,
            "total": total_amount,
            "address": str(data.get("address", "")).strip(),
            "description": str(data.get("description", "")).strip()
        })

        sent, error = notify_order(order_id)
        return jsonify(
            ok=True,
            order_id=order_id,
            total=total_amount,
            eitaa_sent=sent,
            eitaa_error=error
        ), 201

    except ValueError:
        return jsonify(ok=False, error="قیمت و تعداد باید عدد باشند"), 400
    except Exception:
        logger.exception("API order failed")
        return jsonify(ok=False, error="خطا در ثبت سفارش"), 500


# ==================================================
# پنل مدیریت
# ==================================================
ADMIN_LOGIN = """
<!DOCTYPE html><html lang="fa" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ورود مدیر</title>
<style>body{font-family:Tahoma;background:#f3f4f2;padding:20px}.card{max-width:420px;margin:60px auto;background:white;padding:25px;border-radius:12px}input,button{width:100%;padding:14px;margin-top:15px;font-size:16px}button{background:#173b35;color:white;border:0}</style>
<div class="card"><h2>ورود به پنل مدیریت</h2>
{% if error %}<p style="color:red">{{error}}</p>{% endif %}
<form method="post"><input name="key" type="password" placeholder="رمز مدیریت" required><button>ورود</button></form></div></html>
"""

ADMIN_PAGE = """
<!DOCTYPE html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>مدیریت سفارش‌ها</title>
<style>body{font-family:Tahoma;background:#f3f4f2;margin:0}header{background:#173b35;color:white;padding:20px}.container{padding:12px;max-width:900px;margin:auto}.card{background:white;padding:15px;border-radius:12px;margin:12px 0;overflow-wrap:anywhere}button,select{padding:10px;border-radius:7px}button{background:#173b35;color:white;border:0}.bad{color:#b42318}.good{color:#18733a}a{color:white}</style></head><body>
<header><h2>پنل مدیریت کالای آشپزخانه لوکس</h2><a href="/admin/logout">خروج</a></header>
<div class="container"><div class="card"><h3>تعداد سفارش‌ها: {{orders|length}}</h3></div>
{% for o in orders %}<div class="card"><h3>سفارش شماره {{o.id}}</h3>
<p>مشتری: {{o.customer_name}} | تلفن: {{o.phone}}</p><p>محصول: {{o.product}} | تعداد: {{o.quantity}}</p>
<p>قیمت واحد: {{o.price}} تومان</p><p><b>مبلغ کل: {{o.total}} تومان</b></p>
<p>آدرس: {{o.address or 'ثبت نشده'}}</p><p>توضیحات: {{o.description or o.notes or 'ندارد'}}</p>
<p>زمان: {{o.created_at}}</p><p>وضعیت: {{o.status}}</p>
{% if o.eitaa_sent or o.notified %}<p class="good">پیام ایتا ارسال شده</p>{% else %}<p class="bad">پیام ایتا ارسال نشده</p>{% endif %}
{% if o.eitaa_error %}<p class="bad">خطای ایتا: {{o.eitaa_error}}</p>{% endif %}
<form method="post" action="/admin/status/{{o.id}}"><select name="status">{% for s in statuses %}<option value="{{s}}" {% if s==o.status %}selected{% endif %}>{{s}}</option>{% endfor %}</select><button>ذخیره وضعیت</button></form>
{% if not o.eitaa_sent %}<form method="post" action="/admin/retry/{{o.id}}"><button style="margin-top:10px">ارسال مجدد به ایتا</button></form>{% endif %}
</div>{% else %}<div class="card">هنوز سفارشی ثبت نشده است.</div>{% endfor %}</div></body></html>
"""


def admin_required():
    return (
        bool(os.getenv("ADMIN_PANEL_KEY", "").strip())
        and session.get("admin_logged_in") is True
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    admin_key = os.getenv("ADMIN_PANEL_KEY", "").strip()
    if not admin_key:
        return "ADMIN_PANEL_KEY تنظیم نشده است", 503

    error = ""
    if request.method == "POST":
        key = request.form.get("key", "")
        if secrets.compare_digest(key, admin_key):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))
        error = "رمز واردشده صحیح نیست"

    return render_template_string(ADMIN_LOGIN, error=error)


@app.get("/admin")
def admin_panel():
    if not admin_required():
        return redirect(url_for("admin_login"))

    statuses = ["جدید", "در حال بررسی", "تأیید شده", "در حال ارسال", "تحویل شده", "لغو شده"]
    return render_template_string(
        ADMIN_PAGE, orders=get_orders(100), statuses=statuses
    )


@app.post("/admin/status/<order_id>")
def admin_update_status(order_id):
    if not admin_required():
        return redirect(url_for("admin_login"))

    status = request.form.get("status", "").strip()
    allowed = {
        "جدید", "در حال بررسی", "تأیید شده",
        "در حال ارسال", "تحویل شده", "لغو شده"
    }

    if status in allowed:
        conn = get_connection()
        try:
            execute_sql(
                conn, "UPDATE orders SET status = ? WHERE id = ?",
                (status, order_id)
            )
            conn.commit()
        finally:
            conn.close()

    return redirect(url_for("admin_panel"))


@app.post("/admin/retry/<order_id>")
def admin_retry_eitaa(order_id):
    if not admin_required():
        return redirect(url_for("admin_login"))

    notify_order(order_id)
    return redirect(url_for("admin_panel"))


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# ==================================================
# سلامت دیتابیس
# ==================================================
@app.get("/health")
def health():
    try:
        conn = get_connection()
        try:
            execute_sql(conn, "SELECT 1").fetchone()
        finally:
            conn.close()

        return jsonify(
            ok=True,
            service="Kalay Kitchen Luxury",
            database="postgresql" if using_postgres() else "sqlite",
            time=datetime.utcnow().isoformat()
        )
    except Exception as exc:
        logger.exception("Health check failed")
        return jsonify(ok=False, error=type(exc).__name__), 500


@app.get("/db-check")
def db_check():
    try:
        conn = get_connection()
        try:
            if using_postgres():
                rows = execute_sql(conn, """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='orders'
                    ORDER BY ordinal_position
                """).fetchall()
                columns = [dict(row) for row in rows]
            else:
                rows = conn.execute("PRAGMA table_info(orders)").fetchall()
                columns = [
                    {
                        "column_name": row["name"],
                        "data_type": row["type"],
                        "is_nullable": not bool(row["notnull"])
                    }
                    for row in rows
                ]

            return jsonify(
                ok=True,
                database="postgresql" if using_postgres() else "sqlite",
                table="orders",
                columns=columns
            )
        finally:
            conn.close()

    except Exception as exc:
        logger.exception("DB check failed")
        return jsonify(ok=False, error=type(exc).__name__), 500


# ==================================================
# Manifest
# ==================================================
@app.get("/manifest.json")
def manifest():
    response = make_response(jsonify({
        "id": "/",
        "name": APP_NAME,
        "short_name": "کالای آشپزخانه",
        "description": "فروش آنلاین کالای آشپزخانه لوکس و ثبت سفارش",
        "lang": "fa",
        "dir": "rtl",
        "display": "standalone",
        "orientation": "any",
        "scope": "/",
        "start_url": "/",
        "background_color": "#ffffff",
        "theme_color": "#173b35",
        "icons": [
            {
                "src": "/static/icon-192.png",
                "sizes": "192x192",
                "type": "image/png"
            },
            {
                "src": "/static/icon-512.png",
                "sizes": "512x512",
                "type": "image/png"
            }
        ]
    }))
    response.headers["Content-Type"] = "application/manifest+json"
    return response


# ==================================================
# خطاها و اجرای برنامه
# ==================================================
@app.errorhandler(404)
def not_found(error):
    return jsonify(ok=False, error="مسیر مورد نظر پیدا نشد"), 404


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error")
    return jsonify(ok=False, error="خطای داخلی سرور"), 500


try:
    init_db()
except Exception:
    logger.exception("Database initialization failed")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
    )
