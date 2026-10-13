# راهنمای استقرار amirhossein-danesh mail

**وضعیت:** نسخهٔ اولیهٔ یکپارچه‌سازی؛ تا پیش از موفقیت Build و تست‌های عملی، آمادهٔ Production محسوب نمی‌شود.

## ۱. بررسی سرور

روی VPS فعلی ابتدا RAM، دیسک و پورت‌ها را بررسی کن:

```bash
free -h
df -h /
sudo ss -lntup
```

برای SMTP عمومی باید سرویس‌دهنده VPS اتصال خروجی روی پورت 25 و امکان ثبت PTR (Reverse DNS) را پشتیبانی کند. پورت 25 فقط با وب‌پراکسی HTTP راه‌اندازی نمی‌شود. از سرویس‌های فعلی VPS نسخهٔ پشتیبان بگیر.

## ۲. بالا آوردن لوکال و ایمن

```bash
cp .env.example .env
docker compose build --pull
docker compose up -d
docker compose logs --tail=100 mail
```

در حالت پیش‌فرض فقط `127.0.0.1:8080` در میزبان باز می‌شود؛ برای دسترسی از لپ‌تاپ:

```bash
ssh -L 8080:127.0.0.1:8080 USER@VPS_IP
```

از `http://127.0.0.1:8080/admin` نصب Stalwart را کامل کن. رمز اولیهٔ نمایش‌داده‌شده در لاگ را عوض کن؛ رمزها را در Git قرار نده.

## ۳. فعال کردن Webmail در همان کانتینر

در پنل Stalwart مسیر **Settings → Web Applications** یک Application جدید ثبت کن:

| گزینه | مقدار |
|---|---|
| Description | Bulwark Lite |
| Resource URL | `file:///opt/webmail-service/bulwark-lite-stalwart.zip` |
| URL prefix | `/webmail` به شکل map مطابق رابط Stalwart |

بعد از ثبت، از **Actions → Update applications** استفاده کن (یا Stalwart را restart کن) و لاگ را چک کن. مسیر تست: `http://127.0.0.1:8080/webmail/`.

## ۴. تنظیم دامنه

در Stalwart دامنهٔ `amirhossein-danesh.ir` و حساب `me@amirhossein-danesh.ir` را بساز. نمونهٔ رکوردهای پایه (مقادیر دقیق را با پنل Stalwart و زیرساخت خودت تطبیق بده):

```text
mail       A        VPS_PUBLIC_IP
@          MX 10    mail.amirhossein-danesh.ir.
@          TXT      "v=spf1 mx -all"
_dmarc     TXT      "v=DMARC1; p=none"
```

رکورد SPF را بدون بررسی رکورد موجود دومرتبه نساز؛ هر دامنه تنها باید یک SPF داشته باشد. DKIM باید از تنظیمات واقعی سرور گرفته و در DNS اضافه شود. PTR باید با hostname صحیح تطبیق داده شود. اگر Cloudflare داری، رکورد `mail` روی **DNS only** باشد.

## ۵. HTTPS و پورت‌ها

Reverse Proxy موجود روی VPS مسیر `https://mail.amirhossein-danesh.ir` را به HTTP loopback `127.0.0.1:8080` بفرستد؛ headerهای forwarding درست و TLS معتبر لازم‌اند. دسترسی به `/admin` را محدود کن. وقتی SMTP، DKIM/SPF/DMARC، TLS و firewall آماده شد:

```bash
docker compose -f compose.yaml -f compose.public.yaml up -d
```

این override پورت‌های 25، 587 و 993 را در کنار پورت loopback باز می‌کند. سرویس‌های دیگر روی VPS نباید تداخل پورت داشته باشند.

## ۶. تعریف کامل Done برای Production

- Build واقعی image، سلامت سرویس و بازشدن Webmail.
- ساخت کاربر و ورود از طریق UI، تست SMTP و IMAP/JMAP.
- عدم امکان relay ناشناس (Open Relay).
- تست ارسال/دریافت با Gmail و بررسی SPF، DKIM و DMARC در header.
- تست TLS/STARTTLS، PTR، فایروال و پورت 25.
- محدودیت دسترسی پنل ادمین، MFA، اسکن آسیب‌پذیری.
- بکاپ رمزنگاری‌شدهٔ دو volume و **آزمایش بازیابی**.

برای ایمیل‌های تراکنشی پروژه‌های دیگر، قبل از افزایش حجم ترافیک اعتبار IP و isolation را جداگانه بررسی کن.
