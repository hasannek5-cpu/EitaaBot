import os
import json
import html
import secrets
import logging
from datetime import datetime
from urllib.parse import quote

import requests
from flask import (
    Flask, request, redirect, url_for, session,
    render_template_string, jsonify, make_response
)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None

try:
    import sqlite3
except ImportError:
    sqlite3 = None


# =========================================================
# تنظیمات اصلی
# =========================================================

app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KalayKitchen")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
EITAA_TOKEN = os.getenv("EITAA_TOKEN", "").strip()
EITAA_CHAT_ID = os.getenv("EITAA_CHAT_ID", "").strip()
ADMIN_PANEL_KEY = os.getenv("ADMIN_PANEL_KEY", "").strip()

APP_NAME = "کالای آشپزخانه لوکس"


# =========================================================
# دیتابیس
# =========================================================

def get_connection():
    if DATABASE_URL:
        if psycopg is None:
            raise RuntimeError("کتابخانه psycopg نصب نیست")

        db_url = DATABASE_URL.replace(
            "postgres://", "postgresql://", 1
        ) if DATABASE_URL.startswith("postgres://") else DATABASE_URL

        return psycopg.connect(
            db_url,
            row_factory=dict_row,
            connect_timeout=15
        )

    if sqlite3 is None:
        raise RuntimeError("SQLite در دسترس نیست")

    conn = sqlite3.connect(
        os.getenv("DATABASE_PATH", "orders.db"),
        timeout=30
    )
    conn.row_factory = sqlite3.Row
    return conn


def execute_sql(conn, sql, params=()):
    if DATABASE_URL:
        sql = sql.replace("?", "%s")
    return conn.execute(sql, params)


def init_db():
    conn = get_connection()

    try:
        execute_sql(conn, """
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                product TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price NUMERIC(14,2) NOT NULL,
                total NUMERIC(14,2) NOT NULL,
                address TEXT,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'جدید',
                eitaa_sent INTEGER NOT NULL DEFAULT 0,
                eitaa_error TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """) if DATABASE_URL else conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                product TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                price REAL NOT NULL,
                total REAL NOT NULL,
                address TEXT,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'جدید',
                eitaa_sent INTEGER NOT NULL DEFAULT 0,
                eitaa_error TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

    finally:
        conn.close()


def fetch_order(order_id):
    conn = get_connection()

    try:
        row = execute_sql(
            conn,
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        return dict(row) if row else None

    finally:
        conn.close()


def save_order(data):
    conn = get_connection()

    try:
        sql = """
            INSERT INTO orders (
                customer_name, phone, product,
                quantity, price, total,
                address, description,
                status, eitaa_sent
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """

        params = (
            data["customer_name"],
            data["phone"],
            data["product"],
            data["quantity"],
            data["price"],
            data["total"],
            data["address"],
            data["description"],
            "جدید",
            0
        )

        if DATABASE_URL:
            row = execute_sql(
                conn,
                sql + " RETURNING id",
                params
            ).fetchone()
            order_id = row["id"]
        else:
            cursor = execute_sql(conn, sql, params)
            order_id = cursor.lastrowid

        conn.commit()
        return order_id

    finally:
        conn.close()


def update_eitaa_result(order_id, sent, error=None):
    conn = get_connection()

    try:
        execute_sql(
            conn,
            """
            UPDATE orders
            SET eitaa_sent = ?, eitaa_error = ?
            WHERE id = ?
            """,
            (1 if sent else 0, error, order_id)
        )
        conn.commit()

    finally:
        conn.close()


def update_order_status(order_id, status):
    conn = get_connection()

    try:
        execute_sql(
            conn,
            "UPDATE orders SET status = ? WHERE id = ?",
            (status, order_id)
        )
        conn.commit()

    finally:
        conn.close()


def get_orders(limit=100):
    conn = get_connection()

    try:
        rows = execute_sql(
            conn,
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()

        return [dict(row) for row in rows]

    finally:
        conn.close()


# =========================================================
# ارسال پیام ایتا
# =========================================================

def send_eitaa_message(message):
    if not EITAA_TOKEN:
        return False, "EITAA_TOKEN تنظیم نشده است"

    if not EITAA_CHAT_ID:
        return False, "EITAA_CHAT_ID تنظیم نشده است"

    api_url = (
        "https://eitaayar.ir/api/"
        + quote(EITAA_TOKEN, safe="")
        + "/sendMessage"
    )

    try:
        response = requests.post(
            api_url,
            data={
                "chat_id": EITAA_CHAT_ID,
                "text": message
            },
            timeout=25
        )

        response.raise_for_status()

        try:
            result = response.json()
        except ValueError:
            result = {}

        if result.get("ok") is False:
            return False, str(result)

        if result.get("error"):
            return False, str(result["error"])

        return True, None

    except requests.RequestException as exc:
        logger.exception("Eitaa request failed")
        return False, str(exc)


def make_order_message(order):
    return (
        "🛒 سفارش جدید\n"
        "━━━━━━━━━━━━━━\n"
        f"شماره سفارش: {order['id']}\n"
        f"مشتری: {order['customer_name']}\n"
        f"تلفن: {order['phone']}\n"
        f"محصول: {order['product']}\n"
        f"تعداد: {order['quantity']}\n"
        f"قیمت واحد: {order['price']:,}\n"
        f"مبلغ کل: {order['total']:,}\n"
        f"آدرس: {order.get('address') or 'ثبت نشده'}\n"
        f"توضیحات: {order.get('description') or 'ندارد'}\n"
        "━━━━━━━━━━━━━━\n"
        "کالای آشپزخانه لوکس"
    )


def notify_order(order_id):
    order = fetch_order(order_id)

    if not order:
        return False, "سفارش پیدا نشد"

    success, error = send_eitaa_message(
        make_order_message(order)
    )

    update_eitaa_result(order_id, success, error)

    if success:
        logger.info("Eitaa notification sent for order %s", order_id)
    else:
        logger.error(
            "Eitaa notification failed for order %s: %s",
            order_id,
            error
        )

    return success, error


# =========================================================
# قالب فرم سفارش
# =========================================================

ORDER_PAGE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#173b35">
<meta name="description" content="فروش آنلاین کالای آشپزخانه لوکس و ثبت سفارش">
<link rel="manifest" href="/manifest.json">
<title>کالای آشپزخانه لوکس</title>

<style>
*{box-sizing:border-box}
body{
 margin:0;background:#f4f5f1;color:#202820;
 font-family:Tahoma,Arial,sans-serif
}
header{
 background:#173b35;color:white;text-align:center;
 padding:25px 15px
}
header h1{font-size:23px;margin:0 0 10px}
header p{margin:0;font-size:14px}
.container{max-width:600px;margin:25px auto;padding:0 14px}
.card{
 background:white;border-radius:15px;padding:22px;
 box-shadow:0 4px 18px #0000000b
}
label{display:block;margin:15px 0 7px;font-size:14px}
input,textarea{
 width:100%;padding:13px;border:1px solid #ddd;
 border-radius:9px;font-size:15px;font-family:inherit
}
textarea{min-height:90px;resize:vertical}
.total{
 background:#edf5ef;padding:14px;border-radius:9px;
 margin-top:16px;font-weight:bold;color:#173b35
}
button{
 width:100%;border:0;border-radius:10px;padding:15px;
 margin-top:20px;background:#173b35;color:white;
 font-size:16px;font-family:inherit;cursor:pointer
}
button:disabled{opacity:.5}
.notice{
 padding:13px;border-radius:9px;background:#edf5ef;
 margin-bottom:15px
}
footer{text-align:center;color:#777;font-size:12px;padding:25px}
</style>
</head>

<body>
<header>
<h1>کالای آشپزخانه لوکس</h1>
<p>ثبت سفارش آسان و سریع</p>
</header>

<div class="container">
<div class="card">

{% if message %}
<div class="notice">{{ message }}</div>
{% endif %}

<form method="POST" action="/order">

<label>نام و نام خانوادگی *</label>
<input name="customer_name" required maxlength="150">

<label>شماره تلفن *</label>
<input name="phone" required maxlength="30"
       inputmode="tel">

<label>نام محصول *</label>
<input name="product" required maxlength="250">

<label>قیمت واحد (تومان) *</label>
<input id="price" name="price" type="number"
       min="0" step="1" required>

<label>تعداد *</label>
<input id="quantity" name="quantity" type="number"
       min="1" max="100000" value="1" required>

<div class="total">
مبلغ کل: <span id="total">0</span> تومان
</div>

<label>آدرس تحویل</label>
<textarea name="address" maxlength="2000"></textarea>

<label>توضیحات سفارش</label>
<textarea name="description" maxlength="2000"></textarea>

<button type="submit">ثبت سفارش</button>
</form>
</div>
</div>

<footer>کالای آشپزخانه لوکس</footer>

<script>
const price=document.getElementById("price");
const quantity=document.getElementById("quantity");
const total=document.getElementById("total");

function calc(){
 const p=Number(price.value||0);
 const q=Number(quantity.value||0);
 total.textContent=(p*q).toLocaleString("fa-IR");
}
price.addEventListener("input",calc);
quantity.addEventListener("input",calc);
</script>
</body>
</html>
"""


# =========================================================
# صفحه اصلی
# =========================================================

@app.route("/", methods=["GET"])
def index():
    return render_template_string(
        ORDER_PAGE,
        message=request.args.get("message", "")
    )


# =========================================================
# ثبت سفارش
# =========================================================

@app.route("/order", methods=["POST"])
def create_order():
    try:
        customer_name = request.form.get(
            "customer_name", ""
        ).strip()

        phone = request.form.get("phone", "").strip()
        product = request.form.get("product", "").strip()

        address = request.form.get("address", "").strip()
        description = request.form.get(
            "description", ""
        ).strip()

        price = int(request.form.get("price", "0"))
        quantity = int(request.form.get("quantity", "0"))

        if not customer_name or not phone or not product:
            return "اطلاعات ضروری وارد نشده است", 400

        if price < 0 or quantity < 1 or quantity > 100000:
            return "قیمت یا تعداد نامعتبر است", 400

        total = price * quantity

        data = {
            "customer_name": customer_name,
            "phone": phone,
            "product": product,
            "quantity": quantity,
            "price": price,
            "total": total,
            "address": address,
            "description": description
        }

        order_id = save_order(data)

        success, error = notify_order(order_id)

        if success:
            message = (
                f"سفارش شماره {order_id} ثبت و به ایتا ارسال شد."
            )
        else:
            message = (
                f"سفارش شماره {order_id} ثبت شد؛ "
                "اما اطلاع‌رسانی ایتا انجام نشد. "
                "مدیر باید تنظیمات را بررسی کند."
            )

        logger.info(
            "Order %s created. Eitaa success=%s",
            order_id,
            success
        )

        return redirect(
            url_for("index", message=message)
        )

    except ValueError:
        return "قیمت و تعداد باید عدد باشند", 400

    except Exception:
        logger.exception("Order creation failed")
        return "خطا در ثبت سفارش. لطفاً دوباره تلاش کنید.", 500


# =========================================================
# API ثبت سفارش
# =========================================================

@app.route("/api/order", methods=["POST"])
def api_create_order():
    try:
        data = request.get_json(silent=True) or {}

        customer_name = str(
            data.get("customer_name", "")
        ).strip()

        phone = str(data.get("phone", "")).strip()
        product = str(data.get("product", "")).strip()

        price = int(data.get("price", 0))
        quantity = int(data.get("quantity", 0))

        address = str(data.get("address", "")).strip()
        description = str(
            data.get("description", "")
        ).strip()

        if not customer_name or not phone or not product:
            return jsonify(
                ok=False,
                error="اطلاعات ضروری وارد نشده است"
            ), 400

        if price < 0 or quantity < 1 or quantity > 100000:
            return jsonify(
                ok=False,
                error="قیمت یا تعداد نامعتبر است"
            ), 400

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

        success, error = notify_order(order_id)

        return jsonify({
            "ok": True,
            "order_id": order_id,
            "eitaa_sent": success,
            "eitaa_error": error
        }), 201

    except (ValueError, TypeError):
        return jsonify(
            ok=False,
            error="قیمت و تعداد باید عدد باشند"
        ), 400

    except Exception:
        logger.exception("API order failed")
        return jsonify(ok=False, error="خطای سرور"), 500


# =========================================================
# احراز هویت پنل مدیریت
# =========================================================

ADMIN_LOGIN_PAGE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ورود مدیر</title>
<style>
body{font-family:Tahoma;background:#f3f4f2;padding:20px}
.card{max-width:420px;margin:60px auto;background:white;
padding:25px;border-radius:12px}
input,button{width:100%;padding:14px;margin-top:15px;
font-size:16px;border-radius:8px}
input{border:1px solid #ddd}
button{background:#173b35;color:white;border:0}
</style>
</head>
<body>
<div class="card">
<h2>ورود به پنل مدیریت</h2>
{% if error %}<p style="color:red">{{ error }}</p>{% endif %}
<form method="POST">
<input name="key" type="password" placeholder="رمز مدیریت" required>
<button>ورود</button>
</form>
</div>
</body>
</html>
"""


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PANEL_KEY:
        return "ADMIN_PANEL_KEY در Render تنظیم نشده است", 503

    error = ""

    if request.method == "POST":
        supplied = request.form.get("key", "")

        if secrets.compare_digest(
            supplied, ADMIN_PANEL_KEY
        ):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_panel"))

        error = "رمز وارد شده صحیح نیست"

    return render_template_string(
        ADMIN_LOGIN_PAGE,
        error=error
    )


def admin_required():
    return (
        bool(ADMIN_PANEL_KEY)
        and session.get("admin_logged_in") is True
    )


# =========================================================
# پنل مدیریت سفارش
# =========================================================

ADMIN_PAGE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>مدیریت سفارش‌ها</title>
<style>
body{font-family:Tahoma;background:#f3f4f2;margin:0}
header{background:#173b35;color:white;padding:20px}
.container{padding:12px;max-width:1100px;margin:auto}
.card{background:white;padding:15px;border-radius:12px;
margin:12px 0;overflow-wrap:anywhere}
table{width:100%;border-collapse:collapse}
td,th{padding:9px;border-bottom:1px solid #eee;
text-align:right;font-size:13px}
select,button{padding:10px;border-radius:7px}
button{background:#173b35;color:white;border:0}
.bad{color:#b42318}
.good{color:#18733a}
a{color:white}
</style>
</head>
<body>
<header>
<h2>پنل مدیریت کالای آشپزخانه لوکس</h2>
<a href="/admin/logout">خروج از پنل</a>
</header>

<div class="container">

<div class="card">
<h3>تعداد سفارش‌ها: {{ orders|length }}</h3>
<p>سفارش‌های جدید در ابتدای فهرست قرار دارند.</p>
</div>

{% for o in orders %}
<div class="card">
<h3>سفارش شماره {{ o.id }}</h3>
<p>مشتری: {{ o.customer_name }}</p>
<p>تلفن: {{ o.phone }}</p>
<p>محصول: {{ o.product }}</p>
<p>تعداد: {{ o.quantity }}</p>
<p>قیمت واحد: {{ o.price }}</p>
<p><b>مبلغ کل: {{ o.total }}</b></p>
<p>آدرس: {{ o.address or 'ثبت نشده' }}</p>
<p>توضیحات: {{ o.description or 'ندارد' }}</p>
<p>تاریخ: {{ o.created_at }}</p>

<p>وضعیت:
<b>{{ o.status }}</b>
</p>

<p>
{% if o.eitaa_sent %}
<span class="good">پیام ایتا ارسال شده</span>
{% else %}
<span class="bad">پیام ایتا ارسال نشده</span>
{% endif %}
</p>

{% if o.eitaa_error %}
<p class="bad">خطای ایتا: {{ o.eitaa_error }}</p>
{% endif %}

<form method="POST" action="/admin/status/{{ o.id }}">
<select name="status">
{% for s in statuses %}
<option value="{{ s }}" {% if s==o.status %}selected{% endif %}>
{{ s }}
</option>
{% endfor %}
</select>
<button>ذخیره وضعیت</button>
</form>

{% if not o.eitaa_sent %}
<form method="POST" action="/admin/retry/{{ o.id }}">
<button style="margin-top:10px">ارسال مجدد به ایتا</button>
</form>
{% endif %}
</div>
{% else %}
<div class="card">هنوز سفارشی ثبت نشده است.</div>
{% endfor %}

</div>
</body>
</html>
"""


@app.route("/admin", methods=["GET"])
def admin_panel():
    if not admin_required():
        return redirect(url_for("admin_login"))

    statuses = [
        "جدید",
        "در حال بررسی",
        "تأیید شده",
        "در حال ارسال",
        "تحویل شده",
        "لغو شده"
    ]

    return render_template_string(
        ADMIN_PAGE,
        orders=get_orders(100),
        statuses=statuses
    )


@app.route("/admin/status/<int:order_id>", methods=["POST"])
def admin_update_status(order_id):
    if not admin_required():
        return redirect(url_for("admin_login"))

    status = request.form.get("status", "").strip()

    allowed = {
        "جدید",
        "در حال بررسی",
        "تأیید شده",
        "در حال ارسال",
        "تحویل شده",
        "لغو شده"
    }

    if status in allowed:
        update_order_status(order_id, status)

    return redirect(url_for("admin_panel"))


@app.route("/admin/retry/<int:order_id>", methods=["POST"])
def admin_retry_eitaa(order_id):
    if not admin_required():
        return redirect(url_for("admin_login"))

    notify_order(order_id)
    return redirect(url_for("admin_panel"))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# =========================================================
# سلامت سرویس و دیتابیس
# =========================================================

@app.route("/health", methods=["GET"])
def health():
    try:
        conn = get_connection()

        try:
            execute_sql(conn, "SELECT 1").fetchone()
        finally:
            conn.close()

        return jsonify({
            "ok": True,
            "service": "Kalay Kitchen Luxury",
            "database": "postgresql" if DATABASE_URL else "sqlite",
            "time": datetime.utcnow().isoformat()
        })

    except Exception as exc:
        logger.exception("Health check failed")

        return jsonify({
            "ok": False,
            "database": "error",
            "error": str(exc)
        }), 500


# =========================================================
# Manifest برنامه
# =========================================================

@app.route("/manifest.json")
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


# =========================================================
# خطاهای عمومی
# =========================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "ok": False,
        "error": "مسیر مورد نظر پیدا نشد"
    }), 404


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error")
    return jsonify({
        "ok": False,
        "error": "خطای داخلی سرور"
    }), 500


# =========================================================
# شروع برنامه
# =========================================================

try:
    init_db()
    logger.info("Database initialized successfully")
except Exception:
    logger.exception("Database initialization failed")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
    )
