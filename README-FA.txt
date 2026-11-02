فایل‌های PWA کالای آشپزخانه لوکس

این فایل‌ها را در ریشه مخزن EitaaBot (کنار Main.py) بارگذاری کنید:
- manifest.json
- sw.js
- icon-192.png
- icon-512.png

در Main.py داخل HTML، لینک manifest باید به شکل <link rel="manifest" href="/manifest.json"> باشد.
برای ثبت Service Worker پیش از </body> اضافه کنید:
<script>
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js'));
}
</script>

این فایل ZIP به‌تنهایی APK نیست؛ پس از قرار دادن فایل‌ها، سایت را در PWABuilder بسته‌بندی کنید.
