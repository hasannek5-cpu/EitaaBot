کالای آشپزخانه لوکس — نسخه آماده اصلاح دستی

فایل‌ها:
- Main.py: برنامه Flask و فرم سفارش
- requirements.txt: کتابخانه‌ها
- render.yaml: تنظیمات سرویس Web Service در Render
- static/manifest.json: مشخصات نصب‌پذیری PWA
- static/sw.js: Service Worker
- static/icon-192.png و static/icon-512.png: آیکون‌ها

مراحل:
1) فایل‌های داخل ZIP را در ریشه مخزن EitaaBot جایگزین/اضافه کنید؛ پوشه static را هم کامل آپلود کنید.
2) در Render سرویس را از نوع Web Service بسازید (نه Background Worker).
3) Build Command: pip install -r requirements.txt
4) Start Command: gunicorn Main:app
5) در Environment Variables سه مقدار EITAA_TOKEN، EITAA_CHAT_ID و ADMIN_PANEL_KEY را تنظیم کنید.
   توکن را فقط در Render وارد کنید؛ در GitHub یا چت قرار ندهید.
6) برای ذخیره دائمی SQLite باید دیسک پایدار به مسیر /var/data وصل شود؛ در پلن/تنظیمات بدون دیسک پایدار، اطلاعات سفارش‌ها ممکن است پس از redeploy از بین برود.
7) پس از Deploy، آزمایش کنید: https://آدرس-سرویس/health باید {"ok":true,...} برگرداند.
8) سپس در Chrome اندروید آدرس اصلی را باز کنید و از منوی سه‌نقطه گزینه Install app / Add to Home screen را بزنید.

نکته مهم:
- این بسته APK نیست؛ کد و فایل‌های لازم برای وب‌اپ نصب‌پذیر PWA است.
- ساخت APK/TWA بعد از Deploy موفق و داشتن HTTPS انجام می‌شود.
- ارسال ایتا فقط وقتی درست کار می‌کند که API، توکن و شناسه chat_id واقعی و مجاز باشند. قبل از فروش واقعی، سفارش آزمایشی بفرستید.
- APIهای /admin/orders و /admin/status با هدر X-Admin-Key محافظت شده‌اند؛ کلید را محرمانه نگه دارید.
