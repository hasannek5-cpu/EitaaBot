بسته اصلاحات کالای آشپزخانه لوکس

1) render.yaml را با فایل render.yaml داخل بسته جایگزین کنید.
2) static/manifest.json و static/sw.js را در مخزن قرار دهید.
3) در Main.py مقدار EITAA_TOKEN را طوری تنظیم کنید که EITAA_BOT_TOKEN قدیمی را هم بخواند:
EITAA_TOKEN = os.environ.get("EITAA_TOKEN", os.environ.get("EITAA_BOT_TOKEN", "")).strip()
4) سرویس Render باید Web Service باشد و پس از Deploy یک URL عمومی بدهد.
5) برای APK، URL سرویس را در PWABuilder وارد و Android Package را بسازید.
