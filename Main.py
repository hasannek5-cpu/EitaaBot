import os
import re
import json
import secrets
import logging
import sqlite3
from datetime import datetime
from urllib.parse import quote

import requests
from flask import (
    Flask, request, redirect, url_for, render_template_string,
    jsonify, make_response, session
)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


# -------------------- تنظیمات --------------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KalayKitchen")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
EITAA_TOKEN = os.getenv("EITAA_TOKEN", "").strip()
EITAA_CHAT_ID = os.getenv("EITAA_CHAT_ID", "").strip()
ADMIN_PANEL_KEY = os.getenv("ADMIN_PANEL_KEY", "").strip()
APP_NAME = "کالای آشپزخانه لوکس"


# -------------------- اتصال دیتابیس --------------------
def get_connection():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("کتابخانه psycopg نصب نیست؛ requirements.txt را بررسی کنید")
        db_url = DATABASE_URL
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        return psycopg.connect(db_url, row_factory=dict_row, connect_timeout=20)

    conn = sqlite3.connect(os.getenv("DATABASE_PATH", "orders.db"), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def execute_sql(conn, sql, params=()):
    if DATABASE_URL:
        sql = sql.replace("?", "%s")
    return conn.execute(sql, params)


def init_db():
    """ساخت جدول و تکمیل ستون‌های نسخه‌های قدیمی بدون حذف سفارش‌ها."""
    conn = get_connection()
    try:
        if DATABASE_URL:
            execute_sql(conn, """
                CREATE TABLE IF NOT EXISTS orders (
                    id BIGSERIAL PRIMARY KEY,
                    customer_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    product TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    price NUMERIC(14,2) NOT NULL DEFAULT 0,
                    total NUMERIC(18,2) NOT NULL DEFAULT 0,
                    address TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'جدید',
                    eitaa_sent INTEGER NOT NULL DEFAULT 0,
                    eitaa_error TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # هر ستونِ لازم که در نسخه قدیمی وجود نداشته باشد اضافه می‌شود.
            migrations = (
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_name TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS phone TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS product TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS quantity INTEGER DEFAULT 1",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS price NUMERIC(14,2) DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS total NUMERIC(18,2) DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS address TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS description TEXT DEFAULT ''",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'جدید'",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS eitaa_sent INTEGER DEFAULT 0",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS eitaa_error TEXT",
                "ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            )
            for statement in migrations:
                execute_sql(conn, statement)

            # نام ستون‌های موجود را می‌خوانیم تا انتقال اطلاعات قدیمی ایمن باشد.
            rows = execute_sql(conn, """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'orders'
            """).fetchall()
            columns = {row["column_name"] for row in rows}

            if "notes" in columns:
                execute_sql(conn, """
                    UPDATE orders
                    SET description = COALESCE(NULLIF(description, ''), notes, '')
                    WHERE notes IS NOT NULL
                """)

            if "notified" in columns:
                execute_sql(conn, """
                    UPDATE orders
                    SET eitaa_sent = CASE
                        WHEN LOWER(notified::text) IN ('1', 'true', 't', 'yes') THEN 1
                        ELSE 0
                    END
                    WHERE notified IS NOT NULL
                """)

        else:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    customer_name TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    product TEXT NOT NULL DEFAULT '',
                    quantity INTEGER NOT NULL DEFAULT 1,
                    price REAL NOT NULL DEFAULT 0,
                    total REAL NOT NULL DEFAULT 0,
                    address TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'جدید',
                    eitaa_sent INTEGER NOT NULL DEFAULT 0,
                    eitaa_error TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            rows = conn.execute("PRAGMA table_info(orders)").fetchall()
            columns = {row["name"] for row in rows}
            sqlite_migrations = {
                "customer_name": "TEXT DEFAULT ''",
                "phone": "TEXT DEFAULT ''",
                "product": "TEXT DEFAULT ''",
                "quantity": "INTEGER DEFAULT 1",
                "price": "REAL DEFAULT 0",
                "total": "REAL DEFAULT 0",
                "address": "TEXT DEFAULT ''",
                "description": "TEXT DEFAULT ''",
                "status": "TEXT DEFAULT 'جدید'",
                "eitaa_sent": "INTEGER DEFAULT 0",
                "eitaa_error": "TEXT",
                "created_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            }
            for column, definition in sqlite_migrations.items():
                if column not in columns:
                    conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {definition}")

            if "notes" in columns:
                conn.execute("""
                    UPDATE orders
                    SET description = COALESCE(NULLIF(description, ''), notes, '')
                    WHERE notes IS NOT NULL
                """)
            if "notified" in columns:
                conn.execute("""
                    UPDATE orders
                    SET eitaa_sent = CASE
                        WHEN LOWER(CAST(notified AS TEXT)) IN ('1', 'true', 'yes') THEN 1
                        ELSE 0
                    END
                    WHERE notified IS NOT NULL
                """)

        conn.commit()
        logger.info("Database initialized; legacy schema migration completed")
    except Exception:
        conn.rollback()
        logger.exception("Database initialization/migration failed")
        raise
    finally:
        conn.close()


def save_order(data):
    conn = get_connection()
    try:
        sql = """
            INSERT INTO orders
            (customer_name, phone, product, quantity, price, total,
             address, description, status, eitaa_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            data["customer_name"], data["phone"], data["product"],
            data["quantity"], data["price"], data["total"],
            data.get("address", ""), data.get("description", ""),
            "جدید", 0
        )
        if DATABASE_URL:
            row = execute_sql(conn, sql + " RETURNING id", params).fetchone()
            order_id = row["id"]
        else:
            order_id = execute_sql(conn, sql, params).lastrowid
        conn.commit()
        logger.info("Order %s saved to database", order_id)
        return order_id
    except Exception:
        conn.rollback()
        logger.exception("save_order failed")
        raise
    finally:
        conn.close()


def fetch_order(order_id):
    conn = get_connection()
    try:
        row = execute_sql(conn, "SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_eitaa_result(order_id, sent, error=None):
    conn = get_connection()
    try:
        execute_sql(
            conn,
            "UPDATE orders SET eitaa_sent = ?, eitaa_error = ? WHERE id = ?",
            (1 if sent else 0, error, order_id)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("Could not update Eitaa result for order %s", order_id)
        raise
    finally:
        conn.close()


def update_order_status(order_id, status):
    conn = get_connection()
    try:
        execute_sql(conn, "UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        conn.commit()
    finally:
        conn.close()


def get_orders(limit=100):
    conn = get_connection()
    try:
        rows = execute_sql(
            conn, "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# -------------------- ایتا --------------------
def send_eitaa_message(message):
    if not EITAA_TOKEN:
        return False, "متغیر EITAA_TOKEN در Render تنظیم نشده است"
    if not EITAA_CHAT_ID:
        return False, "متغیر EITAA_CHAT_ID در Render تنظیم نشده است"

    api_url = "https://eitaayar.ir/api/" + quote(EITAA_TOKEN, safe="") + "/sendMessage"
    try:
        response = requests.post(
            api_url,
            data={"chat_id": EITAA_CHAT_ID, "text": message},
            timeout=30
        )
        response.raise_for_status()
        try:
            result = response.json()
        except ValueError:
            return False, "پاسخ ایتایار JSON معتبر نیست: " + response.text[:300]

        if result.get("ok") is False or result.get("error"):
            return False, str(result)[:1000]
        return True, None
    except requests.RequestException as exc:
        logger.exception("Eitaa API request failed")
        return False, str(exc)[:1000]


def money(value):
    try:
        return f"{float(value):,.0f}"
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
        f"قیمت واحد: {money(order['price'])} تومان\n"
        f"مبلغ کل: {money(order['total'])} تومان\n"
        f"آدرس: {order.get('address') or 'ثبت نشده'}\n"
        f"توضیحات: {order.get('description') or 'ندارد'}\n"
        "━━━━━━━━━━━━━━"
    )


def notify_order(order_id):
    try:
        order = fetch_order(order_id)
        if not order:
            return False, "سفارش در دیتابیس پیدا نشد"
        success, error = send_eitaa_message(make_order_message(order))
        update_eitaa_result(order_id, success, error)
        if success:
            logger.info("Eitaa sent for order %s", order_id)
        else:
            logger.error("Eitaa failed for order %s: %s", order_id, error)
        return success, error
    except Exception as exc:
        logger.exception("notify_order failed for order %s", order_id)
        return False, str(exc)[:1000]


# -------------------- اعتبارسنجی فرم --------------------
def normalize_digits(value):
    trans = str.maketrans(
        "۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
        "01234567890123456789"
    )
    return str(value or "").translate(trans).replace(",", "").replace("٬", "").strip()


def parse_int(value, default=0):
    cleaned = normalize_digits(value)
    if not cleaned:
        return default
    if not re.fullmatch(r"-?\d+", cleaned):
        raise ValueError("عدد نامعتبر")
    return int(cleaned)


# -------------------- صفحه سفارش --------------------
ORDER_PAGE = r"""
<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#173b35">
<meta name="description" content="فروش آنلاین کالای آشپزخانه لوکس و ثبت سفارش">
<link rel="manifest" href="/manifest.json">
<title>کالای آشپزخانه لوکس</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#f4f5f1;color:#202820;font-family:Tahoma,Arial,sans-serif}
header{background:#173b35;color:white;text-align:center;padding:25px 15px}
header h1{font-size:23px;margin:0 0 10px}
.container{max-width:600px;margin:25px auto;padding:0 14px}
.card{background:white;border-radius:15px;padding:22px;box-shadow:0 4px 18px #0000000b}
label{display:block;margin:15px 0 7px;font-size:14px}
input,textarea{width:100%;padding:13px;border:1px solid #ddd;border-radius:9px;font-size:15px;font-family:inherit}
textarea{min-height:90px}
.total,.notice{background:#edf5ef;padding:14px;border-radius:9px;margin-top:16px;font-weight:bold;color:#173b35}
button{width:100%;border:0;border-radius:10px;padding:15px;margin-top:20px;background:#173b35;color:white;font-size:16px;font-family:inherit;cursor:pointer}
footer{text-align:center;color:#777;font-size:12px;padding:25px}
.error{background:#fff0f0;color:#a00;padding:12px;border-radius:8px;overflow-wrap:anywhere}
</style>
</head>
<body>
<header><h1>کالای آشپزخانه لوکس</h1><p>ثبت سفارش آسان و سریع</p></header>
<div class="container"><div class="card">
{% if message %}<div class="notice">{{ message }}</div>{% endif %}
{% if error %}<div class="error">{{ error }}</div>{% endif %}
<form method="POST" action="/order">
<label>نام و نام خانوادگی *</label><input name="customer_name" required maxlength="150" autocomplete="name">
<label>شماره تلفن *</label><input name="phone" required maxlength="30" inputmode="tel" autocomplete="tel">
<label>نام محصول *</label><input name="product" required maxlength="250">
<label>قیمت واحد (تومان) *</label><input id="price" name="price" type="text" inputmode="numeric" placeholder="مثلاً ۲۰۰۰۰۰" required>
<label>تعداد *</label><input id="quantity" name="quantity" type="number" min="1" max="100000" value="1" required>
<div class="total">مبلغ کل: <span id="total">۰</span> تومان</div>
<label>آدرس تحویل</label><textarea name="address" maxlength="2000"></textarea>
<label>توضیحات سفارش</label><textarea name="description" maxlength="2000"></textarea>
<button type="submit">ثبت سفارش</button>
</form></div></div>
<footer>کالای آشپزخانه لوکس</footer>
<script>
const p=document.getElementById('price'),q=document.getElementById('quantity'),t=document.getElementById('total');
function digits(x){return String(x).replace(/[۰-۹]/g,d=>'۰۱۲۳۴۵۶۷۸۹'.indexOf(d)).replace(/[٠-٩]/g,d=>'٠١٢٣٤٥٦٧٨٩'.indexOf(d));}
function calc(){let a=parseInt(digits(p.value).replace(/[٬,\\s]/g,''))||0,b=parseInt(q.value)||0;t.textContent=(a*b).toLocaleString('fa-IR');}
p.addEventListener('input',calc);q.addEventListener('input',calc);
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
            return redirect(url_for("index", error="لطفاً نام، تلفن و محصول را کامل وارد کنید."))
        if price < 0 or quantity < 1 or quantity > 100000:
            return redirect(url_for("index", error="قیمت یا تعداد واردشده معتبر نیست."))
        if len(phone) < 7:
            return redirect(url_for("index", error="شماره تلفن را صحیح وارد کنید."))

        order_id = save_order({
            "customer_name": customer_name,
            "phone": phone,
            "product": product,
            "quantity": quantity,
            "price": price,
            "total": price * quantity,
            "address": address,
            "description": description
        })

        # سفارش قبل از تلاش برای ارسال پیام ایتا commit شده است.
        sent, eitaa_error = notify_order(order_id)
        if sent:
            msg = f"سفارش شماره {order_id} ثبت و به ایتا ارسال شد."
        else:
            msg = (
                f"سفارش شماره {order_id} در دیتابیس ثبت شد؛ "
                f"ارسال ایتا ناموفق بود. خطا: {eitaa_error}"
            )
        return redirect(url_for("index", message=msg))

    except ValueError:
        return redirect(url_for("index", error="قیمت و تعداد باید عدد باشند."))
    except Exception:
        logger.exception("Order form submission failed")
        return redirect(url_for(
            "index",
            error="خطا در ذخیره سفارش. جزئیات در Application Logs بخش Render ثبت شده است."
        ))


@app.post("/api/order")
def api_create_order():
    try:
        data = request.get_json(silent=True) or {}
        price = parse_int(data.get("price"))
        quantity = parse_int(data.get("quantity"))
        customer_name = str(data.get("customer_name", "")).strip()
        phone = str(data.get("phone", "")).strip()
        product = str(data.get("product", "")).strip()

        if (
            not customer_name or not phone or not product
            or price < 0 or quantity < 1 or quantity > 100000
        ):
            return jsonify(ok=False, error="اطلاعات یا قیمت/تعداد نامعتبر است"), 400

        order_id = save_order({
            "customer_name": customer_name,
            "phone": phone,
            "product": product,
            "quantity": quantity,
            "price": price,
            "total": price * quantity,
            "address": str(data.get("address", "")).strip(),
            "description": str(data.get("description", "")).strip()
        })
        sent, error = notify_order(order_id)
        return jsonify(
            ok=True, order_id=order_id,
            eitaa_sent=sent, eitaa_error=error
        ), 201
    except (ValueError, TypeError):
        return jsonify(ok=False, error="قیمت و تعداد باید عدد باشند"), 400
    except Exception:
        logger.exception("API order error")
        return jsonify(ok=False, error="خطا در ثبت سفارش؛ لاگ سرور را بررسی کنید"), 500


# -------------------- پنل مدیریت --------------------
ADMIN_LOGIN = """
<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ورود مدیر</title>
<style>
body{font-family:Tahoma;background:#f3f4f2;padding:20px}
.card{max-width:420px;margin:60px auto;background:white;padding:25px;border-radius:12px}
input,button{width:100%;padding:14px;margin-top:15px;font-size:16px;border-radius:8px}
input{border:1px solid #ddd}button{background:#173b35;color:white;border:0}
</style>
<div class="card"><h2>ورود به پنل مدیریت</h2>
{% if error %}<p style="color:red">{{error}}</p>{% endif %}
<form method="post"><input name="key" type="password" placeholder="رمز مدیریت" required><button>ورود</button></form>
</div></html>
"""

ADMIN_PAGE = """
<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>مدیریت سفارش‌ها</title>
<style>
body{font-family:Tahoma;background:#f3f4f2;margin:0}
header{background:#173b35;color:white;padding:20px}
.container{padding:12px;max-width:900px;margin:auto}
.card{background:white;padding:15px;border-radius:12px;margin:12px 0;overflow-wrap:anywhere}
button,select{padding:10px;border-radius:7px}
button{background:#173b35;color:white;border:0}
.bad{color:#b42318}.good{color:#18733a}a{color:white}
</style></head><body>
<header><h2>پنل مدیریت کالای آشپزخانه لوکس</h2><a href="/admin/logout">خروج</a></header>
<div class="container">
<div class="card"><h3>تعداد سفارش‌ها: {{orders|length}}</h3></div>
{% for o in orders %}
<div class="card"><h3>سفارش شماره {{o.id}}</h3>
<p>مشتری: {{o.customer_name}} | تلفن: {{o.phone}}</p>
<p>محصول: {{o.product}} | تعداد: {{o.quantity}}</p>
<p>قیمت واحد: {{o.price}} تومان</p><p><b>مبلغ کل: {{o.total}} تومان</b></p>
<p>آدرس: {{o.address or 'ثبت نشده'}}</p><p>توضیحات: {{o.description or 'ندارد'}}</p>
<p>زمان: {{o.created_at}}</p><p>وضعیت: <b>{{o.status}}</b></p>
{% if o.eitaa_sent %}<p class="good">پیام ایتا ارسال شده</p>
{% else %}<p class="bad">پیام ایتا ارسال نشده</p>{% endif %}
{% if o.eitaa_error %}<p class="bad">خطای ایتا: {{o.eitaa_error}}</p>{% endif %}
<form method="post" action="/admin/status/{{o.id}}">
<select name="status">
{% for s in statuses %}<option value="{{s}}" {% if s==o.status %}selected{% endif %}>{{s}}</option>{% endfor %}
</select><button>ذخیره وضعیت</button></form>
{% if not o.eitaa_sent %}
<form method="post" action="/admin/retry/{{o.id}}"><button style="margin-top:10px">ارسال مجدد به ایتا</button></form>
{% endif %}
</div>
{% else %}<div class="card">هنوز سفارشی ثبت نشده است.</div>{% endfor %}
</div></body></html>
"""


def admin_required():
    return bool(ADMIN_PANEL_KEY) and session.get("admin_logged_in") is True


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PANEL_KEY:
        return "ADMIN_PANEL_KEY در Environment Render تنظیم نشده است", 503
    error = ""
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("key", ""), ADMIN_PANEL_KEY):
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


@app.post("/admin/status/<int:order_id>")
def admin_update_status(order_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    status = request.form.get("status", "").strip()
    allowed = {"جدید", "در حال بررسی", "تأیید شده", "در حال ارسال", "تحویل شده", "لغو شده"}
    if status in allowed:
        update_order_status(order_id, status)
    return redirect(url_for("admin_panel"))


@app.post("/admin/retry/<int:order_id>")
def admin_retry_eitaa(order_id):
    if not admin_required():
        return redirect(url_for("admin_login"))
    notify_order(order_id)
    return redirect(url_for("admin_panel"))


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# -------------------- سلامت، تشخیص و PWA --------------------
@app.get("/health")
def health():
    try:
        conn = get_connection()
        try:
            execute_sql(conn, "SELECT 1").fetchone()
            if DATABASE_URL:
                execute_sql(conn, """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'orders'
                """).fetchall()
            else:
                conn.execute("PRAGMA table_info(orders)").fetchall()
        finally:
            conn.close()
        return jsonify(
            ok=True,
            service="Kalay Kitchen Luxury",
            database="postgresql" if DATABASE_URL else "sqlite",
            time=datetime.utcnow().isoformat()
        )
    except Exception as exc:
        logger.exception("Health check failed")
        # جزئیات حساس اتصال/رمز عبور به مرورگر ارسال نمی‌شود.
        return jsonify(ok=False, database="error", error=type(exc).__name__), 500


@app.get("/db-check")
def db_check():
    """گزارش امن ساختار جدول؛ هیچ داده یا اطلاعات اتصال را افشا نمی‌کند."""
    try:
        conn = get_connection()
        try:
            if DATABASE_URL:
                rows = execute_sql(conn, """
                    SELECT column_name, data_type, is_nullable
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'orders'
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
                database="postgresql" if DATABASE_URL else "sqlite",
                table="orders",
                columns=columns
            )
        finally:
            conn.close()
    except Exception as exc:
        logger.exception("Database schema check failed")
        return jsonify(ok=False, error=type(exc).__name__), 500


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
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }))
    response.headers["Content-Type"] = "application/manifest+json"
    return response


@app.errorhandler(404)
def not_found(_error):
    return jsonify(ok=False, error="مسیر مورد نظر پیدا نشد"), 404


@app.errorhandler(500)
def internal_error(_error):
    logger.exception("Internal server error")
    return jsonify(ok=False, error="خطای داخلی سرور"), 500


# راه‌اندازی دیتابیس هنگام شروع برنامه
try:
    init_db()
except Exception:
    logger.exception("Database initialization failed; service started but DB is unavailable")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
