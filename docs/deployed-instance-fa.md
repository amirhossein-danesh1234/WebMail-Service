# استقرار ۲۰۲۶-۱۰-۱۴

- سرور: `ssh amirhossein-danesh`؛ مسیر کد: `/opt/webmail-service`؛ شاخه: `main`.
- رابط عمومی: `https://mail.amirhossein-danesh.ir/webmail/`؛ مسیر `/` به آن هدایت می‌شود.
- HTTPS عمومی با پراکسی موجود ابرآروان و Nginx فایل `deploy/nginx-mail.conf` برقرار است.
- کانتینر: `webmail-service-mail-1`؛ فقط پورت میزبان `127.0.0.1:8080` منتشر شده است.
- Stalwart تنظیم اولیه شده؛ دامنه و حساب `me@amirhossein-danesh.ir` ساخته و Bulwark از ZIP داخلی ثبت شده است.
- رمزهای تولیدشده فقط در فایل‌های با مجوز ۶۰۰ روی سرور هستند: `/root/webmail-user-credentials.json` و `/root/webmail-admin-credentials.json`.
- `/admin` عمومی پاسخ ۴۰۳ می‌دهد. برای دسترسی خصوصی: `ssh -L 8080:127.0.0.1:8080 amirhossein-danesh` و سپس `http://127.0.0.1:8080/admin/`.

## شواهد

Build واقعی تصویر و صحت SHA-256 فایل Bulwark موفق بود. شش آزمون مخزن و `nginx -t` موفق شدند؛ کانتینر healthy است. دامنهٔ عمومی با TLS معتبر رابط Bulwark را با پاسخ ۲۰۰ نمایش می‌دهد. احراز هویت حساب عادی روی HTTPS عمومی و `Mailbox/get` با پنج پوشه موفق بود. صفحهٔ ورود در مرورگر نمایش داده شد؛ ورود تعاملی مرورگر و ارسال/دریافت خارجی آزمایش نشده‌اند.

## باقی‌مانده برای ایمیل اینترنتی

در زمان بررسی MX و SPF دامنه موجود نبود و نام `mail` به IPهای پراکسی ابرآروان اشاره داشت. پورت‌های عمومی ایمیل فعال نشده‌اند. راه‌اندازی SMTP/IMAP نیاز به hostname مستقیم بدون HTTP proxy، بررسی پورت ۲۵ سرویس‌دهنده، PTR، گواهی TLS مبدأ، MX/SPF/DKIM/DMARC و آزمون عدم relay و ارسال/دریافت خارجی دارد. رکوردهای پیشنهادی واقعی Stalwart در `/root/webmail-dns-zone.txt` هستند؛ قبل از انتشار باید با این زیرساخت تطبیق داده شوند. این استقرار به معنی تایید آمادگی کامل سرویس ایمیل اینترنتی نیست.

## به‌روزرسانی

از مسیر مخزن روی سرور: `git pull --ff-only` سپس `docker compose build` و `docker compose up -d`. پیش از تغییرات، هر دو volume را پشتیبان بگیر. برای به‌روزرسانی پراکسی، فایل مخزن را در `/etc/nginx/sites-available/webmail` قرار بده، `nginx -t` اجرا کن و Nginx را reload کن.
