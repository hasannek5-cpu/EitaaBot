
import os
import sqlite3
import uuid
import requests
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# =========================
# تنظیمات برنامه
# =========================

EITAA_TOKEN = os.getenv("EITAA_TOKEN", "")
EITAA_CHAT_ID = os.getenv("EITAA_CHAT_ID", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "orders.db")

# =========================
# ایجاد دیتابیس
# =========================

def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            address TEXT NOT NULL,
            product TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

init_db()

# =========================
# صفحه ثبت سفارش
# =========================

HTML_PAGE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>کالای آشپزخانه لوکس</title>

<style>
body {
    font-family: Tahoma, Arial;
    background: #f1f3f5;
    padding: 20px;
    margin: 0;
}
.container {
    max-width: 500px;
    margin: auto;
    background: white;
    padding: 25px;
    border-radius: 15px;
    box-shadow: 0 4px 15px #ddd;
}
h2 {
    text-align: center;
    color: #222;
}
input, textarea, button {
    width: 100%;
    padding: 13px;
    margin-top: 12px;
    box-sizing: border-box;
    border-radius: 8px;
    font-size: 15px;
}
input, textarea {
    border: 1px solid #ccc;
}
button {
    background: #087f5b;
    color: white;
    border: none;
    cursor: pointer;
}
button:hover {
    background: #066b4b;
}
</style>
</head>

<body>
<div class="container">

<h2>🛍 کالای آشپزخانه لوکس</h2>
<p style="text-align:center">فرم ثبت سفارش</p>

<form method="POST" action="/order">

<input name="name"
placeholder="نام و نام خانوادگی"
required>

<input name="phone"
placeholder="شماره تماس"
required>

<input name="address"
placeholder="نشانی تحویل"
required>

<input name="product"
placeholder="نام کالا"
required>

<input name="quantity"
type="number"
min="1"
value="1"
required>

<textarea name="description"
placeholder="توضیحات (اختیاری)"></textarea>

<button type="submit">ثبت سفارش</button>

</form>

</div>
</body>
</html>
"""

# =========================
# صفحه اصلی
# =========================

@app.route("/")
def home():
    return render_template_string(HTML_PAGE)

# =========================
# ثبت سفارش
# =========================

@app.route("/order", methods=["POST"])
def order():

    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    product = request.form.get("product", "").strip()
    description = request.form.get("description", "").strip()

    try:
        quantity = int(request.form.get("quantity", 1))
    except (ValueError, TypeError):
        quantity = 1

    if not name or not phone or not address or not product:
        return "اطلاعات ضروری کامل نیست.", 400

    if quantity < 1:
        return "تعداد کالا باید حداقل ۱ باشد.", 400

    order_code = uuid.uuid4().hex[:8].upper()

    # ذخیره سفارش در دیتابیس
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO orders (
                order_code,
                name,
                phone,
                address,
                product,
                quantity,
                description
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_code,
                name,
                phone,
                address,
                product,
                quantity,
                description
            )
        )

        conn.commit()

    except sqlite3.Error:
        conn.close()
        return "خطا در ذخیره سفارش.", 500

    finally:
        if conn:
            conn.close()

    # =========================
    # اطلاع رسانی به ایتا
    # =========================

    message = f"""
🛍 سفارش جدید کالای آشپزخانه لوکس

🆔 کد سفارش: {order_code}

👤 نام مشتری: {name}

📞 شماره تماس: {phone}

📦 نام کالا: {product}

🔢 تعداد: {quantity}

📍 نشانی تحویل:
{address}

📝 توضیحات:
{description or 'ندارد'}
"""

    notification_sent = False

    if EITAA_TOKEN and EITAA_CHAT_ID:

        try:
            response = requests.post(
                "https://eitaayar.ir/api/sendMessage",
                data={
                    "token": EITAA_TOKEN,
                    "chat_id": EITAA_CHAT_ID,
                    "text": message
                },
                timeout=20
            )

            if response.status_code == 200:
                result = response.json()
                notification_sent = result.get("ok", False)

        except (requests.RequestException, ValueError):
            notification_sent = False

    # =========================
    # نتیجه ثبت سفارش
    # =========================

    status_message = (
        "اطلاع رسانی به مدیر انجام شد."
        if notification_sent
        else "سفارش ذخیره شد؛ اطلاع رسانی ایتا انجام نشد."
    )

    return render_template_string("""
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>نتیجه سفارش</title>
    </head>

    <body style="font-family:Tahoma;text-align:center;padding:30px">

    <h2>✅ سفارش شما ثبت شد</h2>

    <p>کد سفارش:</p>

    <h3>{{ code }}</h3>

    <p>{{ status }}</p>

    <a href="/">بازگشت به صفحه اصلی</a>

    </body>
    </html>
    """, code=order_code, status=status_message)

# =========================
# مشاهده سلامت برنامه
# =========================

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "app": "Kalay Kitchen Luxury"
    })

# =========================
# اجرای برنامه در Render
# =========================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )
