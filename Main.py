import os
import sqlite3
import uuid
from datetime import datetime, timezone

import requests
import psycopg
from psycopg.rows import dict_row
from flask import Flask, jsonify, request, render_template_string

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("DATABASE_PATH", "orders.db")

EITAA_TOKEN = os.environ.get("EITAA_TOKEN", "").strip()
EITAA_CHAT_ID = os.environ.get("EITAA_CHAT_ID", "").strip()
ADMIN_PANEL_KEY = os.environ.get("ADMIN_PANEL_KEY", "").strip()


PAGE = """<!doctype html>
<html lang="fa" dir="rtl">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="/manifest.json">
<title>کالای آشپزخانه لوکس | ثبت سفارش</title>
<style>
body{font-family:Tahoma,Arial;background:#f4f6f8;margin:0;padding:22px;color:#172033}
main{max-width:560px;margin:20px auto;background:white;padding:24px;border-radius:18px;box-shadow:0 5px 25px #0001}
h1{font-size:24px;color:#183b56}
p{color:#526273}
label{display:block;margin:14px 0 6px;font-weight:bold}
input,textarea{box-sizing:border-box;width:100%;padding:13px;border:1px solid #ccd4dc;border-radius:9px;font:inherit}
button{margin-top:20px;width:100%;padding:14px;background:#176b50;color:white;border:0;border-radius:9px;font:inherit;font-weight:bold}
#result{margin-top:15px;white-space:pre-wrap}
</style>
<main>
<h1>کالای آشپزخانه لوکس</h1>
<p>فرم ثبت سفارش</p>
<form id="order">
<label>نام کالا</label>
<input name="product" required maxlength="120">
<label>نام و نام خانوادگی</label>
<input name="customer_name" required maxlength="100">
<label>شماره تماس</label>
<input name="phone" required maxlength="30" inputmode="tel">
<label>نشانی تحویل</label>
<textarea name="address" required maxlength="700" rows="3"></textarea>
<label>تعداد</label>
<input name="quantity" type="number" min="1" max="999" value="1" required>
<label>توضیحات (اختیاری)</label>
<textarea name="notes" maxlength="700" rows="2"></textarea>
<button id="submit">ثبت سفارش</button>
</form>
<div id="result" role="status"></div>
</main>
<script>
document.getElementById('order').addEventListener('submit',async e=>{
    e.preventDefault();
    const b=document.getElementById('submit');
    const r=document.getElementById('result');
    b.disabled=true;
    r.textContent='در حال ثبت سفارش...';
    const d=Object.fromEntries(new FormData(e.target).entries());
    d.quantity=Number(d.quantity);
    try{
        const x=await fetch('/order',{
            method:'POST',
            headers:{'Content-Type':'application/json'},
            body:JSON.stringify(d)
        });
        const j=await x.json();
        if(!x.ok) throw new Error(j.error||'خطا');
        r.textContent=j.message;
        e.target.reset();
    }catch(err){
        r.textContent='خطا: '+err.message;
    }finally{
        b.disabled=false;
    }
});
if('serviceWorker' in navigator){
    navigator.serviceWorker.register('/sw.js').catch(()=>{});
}
</script>
</html>"""


ADMIN_PAGE = """<!doctype html>
<html lang="fa" dir="rtl">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>مدیریت سفارش‌ها</title>
<style>
*{box-sizing:border-box}
body{font:14px Tahoma;background:#f1f4f8;color:#172033;margin:0;padding:14px}
header,.login,.order{background:#fff;border-radius:14px;padding:15px;margin:0 0 12px;box-shadow:0 2px 10px #00008}
h1{font-size:20px;margin:4px 0 12px}
.top{display:flex;gap:8px}
.top button{flex:1}
input,button,select{font:inherit;padding:11px;border-radius:8px;border:1px solid #ccd4dc}
input{width:100%;margin:8px 0}
button{background:#176b50;color:white;border:0;cursor:pointer}
.order h3{margin:0 0 8px;color:#176b50}
.order p{margin:7px 0;overflow-wrap:anywhere}
.order select{width:100%;margin-top:8px;background:white}
.order button{width:100%;margin-top:8px}
#msg{padding:8px;white-space:pre-wrap}
</style>
<header>
<h1>پنل مدیریت سفارش‌ها</h1>
<div class="top">
<button id="refresh">بروزرسانی</button>
<button id="logout">خروج</button>
</div>
</header>
<section class="login" id="login">
<label>کلید مدیریت</label>
<input id="key" type="password" autocomplete="current-password">
<button id="enter">ورود</button>
</section>
<div id="msg" role="status"></div>
<section id="orders"></section>
<script>
const $=id=>document.getElementById(id);
let key=sessionStorage.getItem('admin_key')||'';
const statuses=['جدید','در حال آماده‌سازی','ارسال شد','تحویل شد','لغو شد'];

function el(tag,text){
    const n=document.createElement(tag);
    if(text!==undefined)n.textContent=text;
    return n;
}

async function api(path,body){
    const r=await fetch(path,{
        method:body?'POST':'GET',
        headers:{
            'X-Admin-Key':key,
            'Content-Type':'application/json'
        },
        body:body?JSON.stringify(body):undefined
    });
    const j=await r.json();
    if(!r.ok)throw Error(j.error||'خطا');
    return j;
}

function showLogin(){
    $('login').style.display=key?'none':'block';
}

async function load(){
    try{
        const j=await api('/admin/orders');
        $('orders').replaceChildren();
        $('msg').textContent=j.orders.length+' سفارش';

        j.orders.forEach(o=>{
            const card=el('article');
            card.className='order';
            card.append(el('h3','سفارش '+o.id));
            card.append(el('p','تاریخ: '+new Date(o.created_at).toLocaleString('fa-IR')));

            [
                ['کالا',o.product],
                ['نام',o.customer_name],
                ['تماس',o.phone],
                ['تعداد',o.quantity],
                ['نشانی',o.address],
                ['توضیحات',o.notes||'—'],
                ['وضعیت فعلی',o.status],
                ['اطلاع ایتا',o.notified?'ارسال شد':'ارسال نشده']
            ].forEach(([a,b])=>{
                card.append(el('p',a+': '+b));
            });

            const sel=el('select');
            statuses.forEach(x=>{
                const op=el('option',x);
                op.value=x;
                op.selected=x===o.status;
                sel.append(op);
            });
            card.append(sel);

            const save=el('button','ثبت وضعیت');
            save.onclick=async()=>{
                try{
                    await api('/admin/status',{
                        order_id:o.id,
                        status:sel.value
                    });
                    $('msg').textContent='وضعیت ذخیره شد';
                    await load();
                }catch(e){
                    $('msg').textContent=e.message;
                }
            };
            card.append(save);
            $('orders').append(card);
        });
    }catch(e){
        $('msg').textContent=e.message;
        if(e.message.includes('غیرمجاز')){
            key='';
            sessionStorage.removeItem('admin_key');
            showLogin();
        }
    }
}

$('enter').onclick=()=>{
    key=$('key').value;
    sessionStorage.setItem('admin_key',key);
    showLogin();
    load();
};

$('refresh').onclick=load;

$('logout').onclick=()=>{
    key='';
    sessionStorage.removeItem('admin_key');
    showLogin();
    $('orders').replaceChildren();
    $('msg').textContent='خارج شدید';
};

showLogin();
if(key)load();
</script>
</html>"""


def connect_db():
    if DATABASE_URL:
        db_url = DATABASE_URL

        if db_url.startswith("postgres://"):
            db_url = db_url.replace(
                "postgres://",
                "postgresql://",
                1
            )

        return psycopg.connect(
            db_url,
            row_factory=dict_row,
            connect_timeout=15
        )

    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def db_execute(conn, sql, params=()):
    if DATABASE_URL:
        sql = sql.replace("?", "%s")

    return conn.execute(sql, params)


def init_db():
    with connect_db() as c:
        db_execute(c, """
            CREATE TABLE IF NOT EXISTS orders(
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                product TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                address TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'جدید',
                notified INTEGER NOT NULL DEFAULT 0
            )
        """)


def notify_eitaa(message):
    if not EITAA_TOKEN or not EITAA_CHAT_ID:
        return False, "EITAA_TOKEN یا EITAA_CHAT_ID تنظیم نشده"

    url = "https://eitaayar.ir/api/app/sendMessage"

    try:
        r = requests.post(
            url,
            json={
                "token": EITAA_TOKEN,
                "chat_id": EITAA_CHAT_ID,
                "text": message
            },
            timeout=20
        )

        r.raise_for_status()
        result = r.json()

        return (
            result.get("ok") is True,
            str(result.get("description", ""))
        )

    except (requests.RequestException, ValueError) as exc:
        app.logger.exception("Eitaa notification failed")
        return False, str(exc)


@app.get("/")
def index():
    return render_template_string(PAGE)


@app.get("/admin")
def admin_page():
    return render_template_string(ADMIN_PAGE)


@app.get("/health")
def health():
    return jsonify(
        ok=True,
        service="KalayIra order form",
        database="postgresql" if DATABASE_URL else "sqlite"
    )


@app.post("/order")
def create_order():
    d = request.get_json(silent=True) or {}

    fields = {
        k: str(d.get(k, "")).strip()
        for k in (
            "product",
            "customer_name",
            "phone",
            "address",
            "notes"
        )
    }

    try:
        quantity = int(d.get("quantity", 1))
    except (TypeError, ValueError):
        return jsonify(error="تعداد باید عدد باشد"), 400

    required = (
        "product",
        "customer_name",
        "phone",
        "address"
    )

    if any(not fields[k] for k in required):
        return jsonify(error="فیلدهای ضروری را تکمیل کنید"), 400

    if not 1 <= quantity <= 999:
        return jsonify(error="تعداد باید بین ۱ تا ۹۹۹ باشد"), 400

    limits = {
        "product": 120,
        "customer_name": 100,
        "phone": 30,
        "address": 700,
        "notes": 700
    }

    if any(len(fields[k]) > limits[k] for k in limits):
        return jsonify(error="یکی از فیلدها بیش از حد طولانی است"), 400

    oid = uuid.uuid4().hex[:8].upper()
    created = datetime.now(timezone.utc).isoformat()

    with connect_db() as c:
        db_execute(
            c,
            """
            INSERT INTO orders
            (id,created_at,product,customer_name,phone,address,quantity,notes)
            VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                oid,
                created,
                fields["product"],
                fields["customer_name"],
                fields["phone"],
                fields["address"],
                quantity,
                fields["notes"]
            )
        )

    msg = (
        f"🛍 سفارش جدید | کالای آشپزخانه لوکس\n"
        f"شماره: {oid}\n"
        f"کالا: {fields['product']}\n"
        f"نام: {fields['customer_name']}\n"
        f"تماس: {fields['phone']}\n"
        f"تعداد: {quantity}\n"
        f"نشانی: {fields['address']}\n"
        f"توضیحات: {fields['notes'] or 'ندارد'}\n"
        f"وضعیت: جدید"
    )

    sent, error = notify_eitaa(msg)

    with connect_db() as c:
        db_execute(
            c,
            "UPDATE orders SET notified=? WHERE id=?",
            (int(sent), oid)
        )

    if sent:
        return jsonify(
            ok=True,
            order_id=oid,
            message=f"سفارش {oid} ثبت و اطلاع‌رسانی شد."
        )

    app.logger.error(
        "Order %s saved; notification not sent: %s",
        oid,
        error
    )

    return jsonify(
        ok=True,
        order_id=oid,
        message=(
            f"سفارش {oid} ثبت شد؛ اما اطلاع‌رسانی ایتا انجام نشد "
            "و مدیر باید تنظیمات را بررسی کند."
        )
    ), 202


def is_admin():
    return (
        bool(ADMIN_PANEL_KEY)
        and request.headers.get("X-Admin-Key", "") == ADMIN_PANEL_KEY
    )


@app.get("/admin/orders")
def list_orders():
    if not is_admin():
        return jsonify(error="دسترسی غیرمجاز"), 401

    with connect_db() as c:
        rows = db_execute(
            c,
            "SELECT * FROM orders ORDER BY created_at DESC LIMIT 100"
        ).fetchall()

    return jsonify(orders=[dict(x) for x in rows])


@app.post("/admin/status")
def set_status():
    if not is_admin():
        return jsonify(error="دسترسی غیرمجاز"), 401

    d = request.get_json(silent=True) or {}

    oid = str(d.get("order_id", "")).strip()
    status = str(d.get("status", "")).strip()

    allowed = (
        "جدید",
        "در حال آماده‌سازی",
        "ارسال شد",
        "تحویل شد",
        "لغو شد"
    )

    if not oid or status not in allowed:
        return jsonify(error="شماره سفارش یا وضعیت نامعتبر است"), 400

    with connect_db() as c:
        cur = db_execute(
            c,
            "UPDATE orders SET status=? WHERE id=?",
            (status, oid)
        )

    if not cur.rowcount:
        return jsonify(error="سفارش پیدا نشد"), 404

    return jsonify(
        ok=True,
        order_id=oid,
        status=status
    )


init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "10000"))
)
