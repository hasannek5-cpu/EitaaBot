
import os
import sqlite3
import requests
from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)

# تنظیمات
EITAA_TOKEN = os.getenv("EITAA_TOKEN", "")
EITAA_CHAT_ID = os.getenv("EITAA_CHAT_ID", "")
DATABASE_PATH = os.getenv("DATABASE_PATH", "orders.db")

# ساخت دیتابیس
def init_db():
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            name TEXT,
            phone TEXT,
            address TEXT,
            product TEXT,
            quantity INTEGER,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

init_db()

# صفحه اصلی ثبت سفارش
HTML_PAGE = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>کالای آشپزخانه لوکس</title>
<style>
body {
    font-family: Tahoma, sans-serif;
    background: #f4f6f9;
    padding: 20px;
}
.container {
    max-width: 500px;
    margin: auto;
    background: white;
    padding: 25px;
    border-radius: 15px;
    box-shadow: 0 4px 15px #ddd;
}
h2 { text-align: center; color: #222; }
input, textarea, button {
    width: 100%;
    padding: 12px;
    margin-top: 10px;
    box-sizing: border-box;
    border-radius: 8px;
}
input, textarea {
    border: 1px solid #ccc;
}
button {
    background: #0b8f55;
    color: white;
    border: none;
    font-size: 16px;
}
button:hover { background: #087443; }
</style>
</head>
<body>
<div class="container">
<h2>کالای آشپزخانه لوکس</h2>
<p style="text-align:center">فرم ثبت سفارش</p>

<form method="POST" action="/order">
<input name="name" placeholder="نام و نام خانوادگی" required>
<input name="phone" placeholder="شماره تماس" required>
<input name="address" placeholder="نشانی تحویل" required>
<input name="product" placeholder="نام کالا" required>
<input name="quantity" type="number" min="1" value="1" required>
<textarea name="description" placeholder="توضیحات (اختیاری)"></textarea>
<button type="submit">ثبت سفارش</button>
</form>
</div>
</body>
</html>
"""

@app.route("/")
def home():
    return render_template_string(HTML_PAGE)

# ثبت سفارش
@app.route("/order", methods=["POST"])
def order():
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    product = request.form.get("product", "").strip()
    description = request.form.get("description", "").strip()

    try:
        quantity = int(request.form.get("quantity", 1))
    except ValueError:
        quantity = 1

    if not name or not phone or not address or not product:
        return "لطفاً همه اطلاعات ضروری را وارد کنید.", 400

    if quantity < 1:
        return "تعداد کالا باید حداقل ۱ باشد.", 400

    import uuid
    order_code = uuid.uuid4().hex[:8].upper()

    # ذخیره سفارش
    conn = sqlite3.connect(DATABASE_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO orders
        (order_code, name, phone, address, product, quantity, description)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (order_code, name, phone, address, product, quantity, description))

    conn.commit()
    conn.close()

    # ارسال اطلاع‌رسانی به ایتا
    message = f"""
🛍 سفارش جدید کالای آشپزخانه لوکس

🆔 کد سفارش: {order_code}
👤 نام: {name}
📞 تماس: {phone}
📦 کالا: {product}
🔢 تعداد: {quantity}
📍 نشانی: {address}
📝 توضیحات: {description or 'ندارد'}
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
                timeout=15
            )

            if response.status_code == 200:
                notification_sent = True

        except requests.RequestException:
            notification_sent = False

    return f"""
    <div style="font-family:Tahoma;text-align:center;direction:rtl;padding:30px">
        <h2>✅ سفارش شما ثبت شد</h2>
        <p>کد سفارش: <b>{order_code}</b></p>
        <p>{'اطلاع‌رسانی به مدیر انجام شد.' if notification_sent else 'سفارش ذخیره شد؛ اطلاع‌رسانی ایتا انجام نشد.'}</p>
        <a href="/">بازگشت به صفحه اصلی</a>
    </div>
    """

# بررسی سلامت سرویس
@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# اجرای برنامه
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
