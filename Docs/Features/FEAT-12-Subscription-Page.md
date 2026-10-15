# FEAT-12 — صفحه‌ی اشتراک کاربر (Subscription Page)

| | |
|---|---|
| اولویت | ⭐ خوب است داشته باشیم |
| حجم کار | S-M (۱-۱.۵ روز) |
| حل می‌کند | کاربر بدون پرسیدن از ربات، مصرف/انقضا/کانفیگ را ببیند؛ کاهش بار پشتیبانی و درخواست‌های ربات |
| وابستگی | [FEAT-01](FEAT-01-Lightweight-Bot-API.md)، [FEAT-02](FEAT-02-Peer-Limits.md) |

## ۱. مسئله
کاربر برای دیدن حجم باقی‌مانده یا دریافت دوباره‌ی کانفیگ باید به ربات پیام بدهد و ربات هر بار از پنل بپرسد. Share Link فعلی ([PeerShareLinks.py](../../src/modules/PeerShareLinks.py) و [share.vue](../../src/static/app/src/views/share.vue)) فقط کانفیگ را نشان می‌دهد، شناسه‌اش UUID ذخیره‌شده به‌صورت متن ساده است و مصرف/انقضا ندارد.

## ۲. اهداف
1. یک لینک دائمی و غیرقابل حدس برای هر سرویس: صفحه‌ی سبک (بدون SPA) با وضعیت، مصرف، انقضا، QR و دانلود.
2. فارسی و راست‌به‌چپ به‌طور پیش‌فرض؛ قابل استفاده روی موبایل.
3. قابل ابطال/تعویض؛ ذخیره‌ی **هش** توکن.
4. خروجی JSON برای اپ‌ها/ربات.

## ۳. غیر اهداف
پرتال کاربری با ورود (پرتال Client فعلی همین کار را می‌کند و برای OxBot لازم نیست).

## ۴. طراحی

### ۴.۱ مدل داده — `PeerSubscriptions` (دیتابیس `wgdashboard`)
| ستون | توضیح |
|------|--------|
| `TokenHash` (PK) | `sha256(token)` |
| `Configuration`, `PeerID` | |
| `CreatedAt`, `RevokedAt` | |
| `LastAccessAt`, `AccessCount` | |
| `ConfigDownloads` | شمارنده |

توکن: `secrets.token_urlsafe(32)` (۲۵۶ بیت).

### ۴.۲ مسیرها (خارج از `/api`، در whitelist احراز)
```
GET {prefix}/sub/<token>            صفحه‌ی HTML (Jinja، قالب templates/sub.html)
GET {prefix}/sub/<token>/config     فایل .conf با Content-Disposition
GET {prefix}/sub/<token>/qr.svg     QR کانفیگ (تولید سمت سرور با کتابخانه‌ی کوچک segno، بدون CDN)
GET {prefix}/sub/<token>/json       { status, usage, limits, expire_at }
```
محتوای صفحه: نام سرویس، وضعیت (فعال / حجم تمام / منقضی / On-Hold)، نوار مصرف، زمان باقی‌مانده، QR، دکمه‌ی دانلود، لینک نصب WireGuard برای Android/iOS/Windows، متن پشتیبانی قابل تنظیم.

### ۴.۳ تنظیمات
```ini
[Subscription]
enabled = false
base_url =                 ; اگر لینک باید با دامنه‌ی دیگری ساخته شود، مثلاً https://sub.example.com
support_text = پشتیبانی: @YourSupportBot
config_downloads_limit = 0 ; 0 = نامحدود
```

### ۴.۴ چرخه‌ی عمر
- ساخت: در ساخت سرویس ([FEAT-03](FEAT-03-Atomic-Idempotent-Operations.md)، گزینه‌ی `create_subscription: true`) یا با API.
- تعویض: توکن جدید، قدیمی باطل (مثلاً اگر کاربر لینک را پخش کرده).
- ابطال خودکار: با حذف peer یا تغییر کلیدش.

## ۵. API
```http
POST   /api/v2/peer/subscription   { "configuration": "wg0", "id": "<pk>", "rotate": false }   (peers:config)
→ { "url": "https://…/sub/<token>" }      ← توکن کامل فقط در این پاسخ
DELETE /api/v2/peer/subscription?configuration=wg0&id=<pk>
```

## ۶. امنیت
- لینک = رمز عبور سرویس (کانفیگ کامل را می‌دهد). فقط در چت خصوصی ربات ارسال شود؛ فقط روی HTTPS.
- هدرها: `Cache-Control: no-store`، `X-Robots-Tag: noindex`، `Referrer-Policy: no-referrer`، CSP سخت‌گیرانه (بدون اسکریپت خارجی).
- rate limit به ازای IP (مثلاً ۳۰ درخواست در دقیقه) با ProxyFix درست.
- پاسخ برای توکن نامعتبر یا باطل: همان صفحه‌ی ۴۰۴ عمومی (بدون تمایز).
- توکن در دیتابیس فقط به‌صورت هش؛ بکاپ افشاشده لینک معتبر نمی‌دهد.

## ۷. سازگاری
Share Link فعلی دست نمی‌خورد.

## ۸. تغییرات ربات OxBot
- بعد از خرید: ارسال لینک (و در صورت تمایل فایل هم مثل الان).
- دکمه‌ی «مشاهده‌ی سرویس» می‌تواند لینک را با Telegram Web App باز کند.
- دکمه‌ی «لینک جدید» برای کاربر ← `rotate: true`.

## ۹. تغییرات UI
در مودال peer: نمایش/کپی/تعویض/ابطال لینک اشتراک؛ در تنظیمات: بخش Subscription.

## ۱۰. تست
- توکن معتبر/باطل/تعویض‌شده؛ عدم وجود توکن خام در دیتابیس؛ هدرهای امنیتی؛ rate limit؛ نمایش درست RTL روی موبایل؛ peer محدودشده ← وضعیت «تمام شده» ولی دانلود همچنان ممکن (یا طبق تنظیم).

## ۱۱. استقرار
پیش‌فرض خاموش؛ اگر از دامنه‌ی جدا استفاده می‌کنید، بلاک nginx فقط مسیر `/sub/` را به پنل proxy کند تا خود پنل از آن دامنه در دسترس نباشد.

## ۱۲. فایل‌های درگیر
`src/subscription.py` (Blueprint جدید)، `templates/sub.html`، `src/modules/PeerSubscriptions.py`، [dashboard.py](../../src/dashboard.py) (whitelist)، `requirements.txt` (`segno`)، `tests/test_subscription.py`.
