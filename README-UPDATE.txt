محتویات بسته:
- Main.py: نسخه کامل با پنل موبایل /admin، API سفارش‌ها و تغییر وضعیت.
- static/manifest.json و static/sw.js: فایل‌های PWA.
- render.yaml: نمونه تنظیم Render برای دیسک دائمی.

مهم:
1) Main.py و فایل‌های static را در مخزن GitHub جایگزین/اضافه کنید.
2) در Render، Disk با mount path /var/data بسازید و DATABASE_PATH را /var/data/orders.db قرار دهید.
3) متغیر ADMIN_PANEL_KEY را با یک رمز قوی تنظیم کنید؛ EITAA_TOKEN و EITAA_CHAT_ID نیز در Environment باشند.
4) render.yaml را بدون بررسی روی سرویس موجود اعمال نکنید؛ ممکن است Blueprint سرویس/دیسک ایجاد یا تغییر دهد.
5) آدرس پنل پس از Deploy: https://دامنه-سرویس/admin
