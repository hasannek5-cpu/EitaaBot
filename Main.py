import os
import sqlite3
import uuid
from datetime import datetime, timezone
from flask import Flask, jsonify, request, render_template_string, send_from_directory, Response

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.environ.get("DATABASE_PATH", os.path.join(BASE_DIR, "orders.db"))

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024

def env(name):
    return os.environ.get(name, "").strip()

PAGE = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#183b56">
<meta name="description" content="ثبت سفارش کالای آشپزخانه لوکس">
<link rel="manifest" href="/manifest.json">
<link rel="icon" href="/static/icon-192.png" type="image/png">
<title>کالای آشپزخانه لوکس | ثبت سفارش</title>
<style>
*{box-sizing:border-box}
body{font-family:Tahoma,Arial,sans-serif;background:#f4f6f8;margin:0;padding:18px;color:#172033}
main{max-width:560px;margin:20px auto;background:white;padding:22px;border-radius:18px;box-shadow:0 5px 25px #0001}
h1{font-size:23px;color:#183b56;margin-top:4px}
p{color:#526273;line-height:1.8}
label{display:block;margin:14px 0 6px;font-weight:bold}
input,textarea{width:100%;padding:13px;border:1px solid #ccd4dc;border-radius:9px;font:inherit;background:white}
button{margin-top:20px;width:100%;padding:14px;background:#176b50;color:white;border:0;border-radius:9px;font:inherit;font-weight:bold}
button:disabled{opacity:.6}
#result{margin-top:15px;white-space:pre-wrap;line-height:1.8}
.small{font-size:12px;color:#718096}
</style>
</head>
<body>
<main>
<h1>کالای آشپزخانه لوکس</h1>
<p>فرم ثبت سفارش</p>
<form id="order">
<label>نام کالا</label><input name="product" required maxlength="120">
<label>نام و نام خانوادگی</label><input name="customer_name" required maxlength="100">
<label>شماره تماس</label><input name="phone" required maxlength="30" inputmode="tel">
<label>نشانی تحویل</label><textarea name="address" required maxlength="700" rows="3"></textarea>
<label>تعداد</label><input name="quantity" type="number" min="1" max="999" value="1" required>
<label>توضیحات (اختیاری)</label><textarea name="notes" maxlength="700" rows="2"></textarea>
<button id="submit" type="submit">ثبت سفارش</button>
</form>
<div id="result" role="status" aria-live="polite"></div>
<p class="small">پس از ثبت، سفارش در سامانه ذخیره می‌شود.</p>
</main>
<script>
document.getElementById('order').addEventListener('submit',async e=>{
 e.preventDefault();
 const b=document.getElementById('submit'),r=document.getElementById('result');
 b.disabled=true;r.textContent='در حال ثبت سفارش...';
 const d=Object.fromEntries(new FormData(e.target).entries());d.quantity=Number(d.quantity);
 try{
  const x=await fetch('/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
  const j=await x.json();if(!x.ok)throw new Error(j.error||'خطا در ثبت سفارش');
  r.textContent=j.message;e.target.reset();
 }catch(err){r.textContent='خطا: '+err.message;}
 finally{b.disabled=false;}
});
if('serviceWorker' in navigator){window.addEventListener('load',()=>navigator.serviceWorker.register('/sw.js').catch(err=>console.log('SW:',err)));}
</script>
</body></html>"""

def connect_db():
    c = sqlite3.connect(DB_PATH, timeout=20)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    folder = os.path.dirname(DB_PATH)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with connect_db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS orders(
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, product TEXT NOT NULL,
            customer_name TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
            quantity INTEGER NOT NULL, notes TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'جدید', notified INTEGER NOT NULL DEFAULT 0
        )""")

def notify_eitaa(message):
    token = env("EITAA_TOKEN")
    chat_id = env("EITAA_CHAT_ID")
    if not token or not chat_id:
        app.logger.warning("Eitaa notification skipped: missing EITAA_TOKEN/EITAA_CHAT_ID")
        return False, "تنظیمات ایتا کامل نیست"
    try:
        response = requests_post_eitaa(token, chat_id, message)
        data = response
        # Eitaa API responses may vary; accept only explicit success.
        ok = data.get("ok") is True or data.get("success") is True
        return ok, str(data.get("description") or data.get("message") or "")
    except Exception as exc:
        app.logger.exception("Eitaa notification failed")
        return False, str(exc)

def requests_post_eitaa(token, chat_id, message):
    import requests
    url = "https://eitaayar.ir/api/app/sendMessage"
    response = requests.post(url, json={
        "token": token, "chat_id": chat_id, "text": message
    }, timeout=20)
    response.raise_for_status()
    return response.json()

@app.get("/")
def index():
    return render_template_string(PAGE)

@app.get("/manifest.json")
def manifest():
    return send_from_directory(STATIC_DIR, "manifest.json", mimetype="application/manifest+json")

@app.get("/sw.js")
def service_worker():
    response = send_from_directory(STATIC_DIR, "sw.js", mimetype="application/javascript")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response

@app.get("/health")
def health():
    return jsonify(ok=True, service="KalayIra order form")

@app.post("/order")
def create_order():
    d = request.get_json(silent=True) or {}
    fields = {k: str(d.get(k, "")).strip() for k in
              ("product", "customer_name", "phone", "address", "notes")}
    try:
        quantity = int(d.get("quantity", 1))
    except (TypeError, ValueError):
        return jsonify(error="تعداد باید عدد باشد"), 400
    required = ("product", "customer_name", "phone", "address")
    if any(not fields[k] for k in required):
        return jsonify(error="فیلدهای ضروری را تکمیل کنید"), 400
    limits = {"product":120, "customer_name":100, "phone":30, "address":700, "notes":700}
    if any(len(fields[k]) > limits[k] for k in limits):
        return jsonify(error="یکی از فیلدها بیش از حد طولانی است"), 400
    if not 1 <= quantity <= 999:
        return jsonify(error="تعداد باید بین ۱ تا ۹۹۹ باشد"), 400

    order_id = uuid.uuid4().hex[:8].upper()
    created = datetime.now(timezone.utc).isoformat()
    with connect_db() as c:
        c.execute("""INSERT INTO orders
            (id,created_at,product,customer_name,phone,address,quantity,notes)
            VALUES(?,?,?,?,?,?,?,?)""",
            (order_id, created, fields["product"], fields["customer_name"],
             fields["phone"], fields["address"], quantity, fields["notes"]))

    message = (
        f"🛍 سفارش جدید | کالای آشپزخانه لوکس\nشماره: {order_id}\n"
        f"کالا: {fields['product']}\nنام: {fields['customer_name']}\n"
        f"تماس: {fields['phone']}\nتعداد: {quantity}\n"
        f"نشانی: {fields['address']}\nتوضیحات: {fields['notes'] or 'ندارد'}\nوضعیت: جدید"
    )
    sent, error = notify_eitaa(message)
    with connect_db() as c:
        c.execute("UPDATE orders SET notified=? WHERE id=?", (int(sent), order_id))
    if sent:
        return jsonify(ok=True, order_id=order_id,
                       message=f"سفارش {order_id} ثبت و اطلاع‌رسانی شد.")
    app.logger.error("Order %s saved; Eitaa notification failed: %s", order_id, error)
    return jsonify(ok=True, order_id=order_id,
                   message=f"سفارش {order_id} ثبت شد؛ اما پیام ایتا ارسال نشد. مدیر باید تنظیمات را بررسی کند."), 202

def is_admin():
    key = env("ADMIN_PANEL_KEY")
    supplied = request.headers.get("X-Admin-Key", "")
    return bool(key) and supplied == key

@app.get("/admin/orders")
def list_orders():
    if not is_admin():
        return jsonify(error="دسترسی غیرمجاز"), 401
    with connect_db() as c:
        rows = c.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 100").fetchall()
    return jsonify(orders=[dict(row) for row in rows])

@app.post("/admin/status")
def set_status():
    if not is_admin():
        return jsonify(error="دسترسی غیرمجاز"), 401
    data = request.get_json(silent=True) or {}
    order_id = str(data.get("order_id", "")).strip()
    status = str(data.get("status", "")).strip()
    allowed = ("جدید", "در حال آماده‌سازی", "ارسال شد", "تحویل شد", "لغو شد")
    if not order_id or status not in allowed:
        return jsonify(error="شماره سفارش یا وضعیت نامعتبر است"), 400
    with connect_db() as c:
        cur = c.execute("UPDATE orders SET status=? WHERE id=?", (status, order_id))
    if not cur.rowcount:
        return jsonify(error="سفارش پیدا نشد"), 404
    return jsonify(ok=True, order_id=order_id, status=status)

@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
