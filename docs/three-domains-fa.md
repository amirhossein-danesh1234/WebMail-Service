# استقرار سه دامنه — ۲۰۲۶-۱۰-۲۷

هر سه وب‌میل از همان تصویر و سرویس موجود روی `ssh amirhossein-danesh` استفاده می‌کنند.
دامنه‌ها و صندوق‌ها جدا هستند؛ این تنظیمات جداسازی سازمانی یا tenant ایجاد نمی‌کند.

| سایت | وب‌میل | حساب مالک |
| --- | --- | --- |
| یونی‌لرن | https://mail.unilrn.ir/webmail/ | `me@unilrn.ir` |
| کاپائوت | https://mail.kappaut.ir/webmail/ | `me@kappaut.ir` |
| شخصی | https://mail.amirhossein-danesh.ir/webmail/ | `me@amirhossein-danesh.ir` |

Nginx فایل `deploy/nginx-mail.conf` هر سه نام را به `127.0.0.1:8080` می‌فرستد.
TLS عمومی در ابرآروان خاتمه می‌یابد. مسیر `/` به `/webmail/` هدایت می‌شود و
`/admin` و `/admin/` عمومی مسدودند. شناسهٔ سرویس و OAuth issuer اصلی حفظ شده است.
کلاینت عمومی `bulwark-webmail` فقط callbackهای دقیق HTTPS زیر را می‌پذیرد:
`/webmail/` و `/webmail/oauth/callback` روی همین سه نام.

## تنظیم و نگهداری

```bash
cd /opt/webmail-service
git pull --ff-only origin main
python3 scripts/configure-domains.py
cp deploy/nginx-mail.conf /etc/nginx/sites-available/webmail
nginx -t && systemctl reload nginx
```

اسکریپت برای ساخت حساب‌های تازه، رمز تصادفی تولید می‌کند. اجرای مجدد، حساب‌ها،
رمزهای موجود و callbackهای قبلی همان کلاینت را حفظ می‌کند. رمز یا کلید خصوصی
در مخزن قرار نمی‌گیرد. فایل‌های حساب‌ها با مجوز ۶۰۰ فقط روی سرور هستند:

- `/root/webmail-unilrn.ir-credentials.json`
- `/root/webmail-kappaut.ir-credentials.json`
- `/root/webmail-user-credentials.json` برای حساب شخصی قبلی

مدیریت خصوصی: `ssh -L 8080:127.0.0.1:8080 amirhossein-danesh`
و سپس `http://127.0.0.1:8080/admin/`؛ مشخصات مدیر در فایل موجود
`/root/webmail-admin-credentials.json` نگهداری می‌شود.

پیش از استقرار، هر دو volume در
`/root/webmail-backup-20261027T170837Z/stalwart-volumes.tar.gz`، همراه با پیکربندی
قبلی Nginx و شناسهٔ نسخهٔ قبلی، پشتیبان گرفته شدند. فهرست آرشیو خوانده و
SHA-256 ثبت شد؛ آزمون بازیابی کامل در این استقرار انجام نشده است.

## محدودهٔ تأیید و مانع ایمیل اینترنتی

هر سه رابط با HTTPS معتبر پاسخ ۲۰۰ می‌دهند؛ پنل مدیریت عمومی پاسخ ۴۰۳ می‌دهد.
شش آزمون مخزن، `nginx -t` و سلامت کانتینر موفق‌اند. پاسخ سایت‌های اصلی بعد از
استقرار با خط مبنا یکسان است. حساب شخصی قبلی و داده‌هایش حفظ شده‌اند.

در زمان بررسی، هر سه MX به `mail.<domain>` اشاره دارند، در حالی که این نام‌ها
IPهای HTTP CDN را برمی‌گردانند. SPF از نوع `v=spf1 mx -all` و DMARC از نوع
`p=none` موجودند؛ نباید SPF دوم ساخته شود. نام مستقیم `mx.amirhossein-danesh.ir`
وجود ندارد و PTR فعلی نام عمومی معتبر و هم‌خوان با میزبان ایمیل نیست.
اتصال خروجی TCP 25 به Gmail در مهلت هشت ثانیه موفق نشد؛ علت نهایی محدودیت
شبکه یا سرویس‌دهنده هنوز تعیین نشده است.

برای تکمیل ایمیل اینترنتی باید DNS مستقیم، MX، SPF، DKIM هر سه دامنه، PTR،
TLS معتبر SMTP/IMAP و دسترسی ورودی/خروجی پورت ۲۵ تنظیم و سپس عدم open relay
و ارسال/دریافت واقعی خارجی بررسی شوند. پورت‌های عمومی SMTP/IMAP هنوز
منتشر نشده‌اند و این استقرار اثبات ارسال یا دریافت اینترنتی نیست.

کلیدهای **عمومی** DKIM و zone پیشنهادی هر دامنه از API واقعی گرفته و در
`/root/webmail-<domain>-dns-zone.txt` ذخیره شده‌اند. zone تولیدی Stalwart را
نباید بدون اصلاح نام MX مستقیم و تطبیق با DNS موجود، یک‌جا منتشر کرد.
کلید خصوصی DKIM فقط در فضای دادهٔ سرویس باقی می‌ماند.
