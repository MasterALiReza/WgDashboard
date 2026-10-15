# ۰۹ — فیچرهای پیشنهادی

> هیچ‌کدام از این‌ها پیاده‌سازی نشده‌اند. هر کدام را خواستید بگویید تا اضافه کنم.
> **سند طراحی کامل هر فیچر** (مدل داده، API، امنیت، تغییرات ربات، تست، استقرار) در پوشه‌ی [Features/](Features/README.md) است و ترتیب اجرا در [نقشه‌ی راه](10-Implementation-Roadmap.md).
> **اولویت:** ⭐⭐⭐ ضروری · ⭐⭐ ارزش بالا · ⭐ خوب است داشته باشیم
> **حجم کار:** S (چند ساعت) · M (۱-۳ روز) · L (بیشتر)

## جدول خلاصه

| ID | اولویت | حجم | فیچر | مشکلاتی که حل می‌کند |
|----|---------|------|------|----------------------|
| [FEAT-01](#feat-01) | ⭐⭐⭐ | S | API سبک برای ربات (یک peer / مصرف فشرده‌ی همه) | BOT-02, PERF-02، فریز پنل |
| [FEAT-02](#feat-02) | ⭐⭐⭐ | M-L | محدودیت‌های دائمی peer (پلن: حجم، انقضا، on-hold) به‌جای job | BOT-03/04/05/06/07، BUG-02/05 |
| [FEAT-03](#feat-03) | ⭐⭐⭐ | M | عملیات اتمیک و idempotent (خرید، تمدید، bulk) | BOT-05, BOT-10, BOT-11 |
| [FEAT-04](#feat-04) | ⭐⭐ | S-M | Webhook امضاشده برای رویدادها (اتمام حجم، ۸۰٪ مصرف، ...) | حذف polling، اطلاع‌رسانی فوری به کاربر |
| [FEAT-05](#feat-05) | ⭐⭐⭐ | M | API Key با محدوده (اینترفیس + سطح دسترسی + IP) | SEC-03, SEC-04, BOT-14 |
| [FEAT-06](#feat-06) | ⭐⭐ | M | بکاپ امن: رمزنگاری، ارسال خودکار به تلگرام/S3، retention هوشمند | GB-02/03/09/12 |
| [FEAT-07](#feat-07) | ⭐⭐ | S-M | تاریخچه‌ی روزانه/ماهانه‌ی مصرف اینترفیس | TU-03، گزارش مصرف سرور |
| [FEAT-08](#feat-08) | ⭐⭐ | S | Health check، متریک و هشدار تلگرامی برای ادمین | تشخیص زودهنگام BUG-02, BUG-05, GB, PERF-07 |
| [FEAT-09](#feat-09) | ⭐⭐ | S | Audit log با هویت انجام‌دهنده (ادمین/کدام API Key) | ردگیری کارهای دو ربات |
| [FEAT-10](#feat-10) | ⭐ | M | محدودیت سرعت per-peer (فروش پلن سرعتی) | — |
| [FEAT-11](#feat-11) | ⭐ | S-M | تشخیص اشتراک‌گذاری اکانت | — |
| [FEAT-12](#feat-12) | ⭐ | S-M | صفحه‌ی اشتراک کاربر (مصرف، انقضا، QR، دانلود) | — |
| [FEAT-13](#feat-13) | ⭐⭐ | S | تخصیص IP توسط خود پنل (quick win بدون تغییر کد پنل) | سادگی و حذف قفل MySQL در ربات |
| [FEAT-14](#feat-14) | ⭐⭐ | M | ابزار چرخش کلید اینترفیس و صدور مجدد کانفیگ‌ها | پیامد سند ۰۱ |

---

<a id="feat-01"></a>
## FEAT-01 ⭐⭐⭐ API سبک برای ربات

**چرا:** الان برای هر کاربر کل اینترفیس (~۲ مگابایت) دانلود می‌شود ([BOT-02](06-OxBot-Integration.md#bot-02)).

**API پیشنهادی:**
```http
GET /api/v2/peer?configurationName=wg0&id=<public_key>
GET /api/v2/peer?configurationName=wg0&name=<username>        # fallback
→ { id, name, allowed_ip, restricted, restricted_reason,
    usage: { rx_gb, tx_gb, total_gb },            # همیشه cumu + total
    latest_handshake, status, endpoint, jobs[] , limits{} }

GET /api/v2/peers/usage?configurationName=wg0
→ [ [id, name, restricted, total_gb, rx_gb, tx_gb, last_handshake_ts], ... ]   # آرایه‌ی فشرده
   + هدر ETag برای اینکه اگر تغییری نبود 304 برگردد
```
ربات در کرون یک بار `peers/usage` را می‌گیرد و برای صفحه‌ی سرویس هر کاربر `peer` را. بار پنل چند صد برابر کم می‌شود.

---

<a id="feat-02"></a>
## FEAT-02 ⭐⭐⭐ محدودیت‌های دائمی (پلن) روی peer

**چرا:** محدودیت‌های فروش الان job های یک‌بارمصرف هستند: بعد از اجرا حذف می‌شوند، با یک job خراب کل سیستم می‌خوابد، بعد از ۱۱ شکست پاک می‌شوند، و ربات باید خودش حذف/ساختشان را هماهنگ کند. چند باگ مالی ربات (BOT-03، BOT-04) مستقیماً از همین طراحی آمده‌اند.

**طرح:**
- جدول `PeerLimits(configuration, peer_id, data_limit_gb, expire_at_utc, on_hold_days, activated_at, plan_name)`.
- حلقه‌ی enforcement هر ۶۰ ثانیه: اگر `usage > data_limit` یا `now > expire_at` ← restrict با دلیل؛ **محدودیت حذف نمی‌شود**.
- `allowAccessPeers` اگر محدودیت هنوز رد شده باشد خطای `LIMIT_EXCEEDED` می‌دهد (مگر `force=true`).
- **On-hold:** اگر `on_hold_days` ست باشد، `expire_at` از اولین handshake محاسبه می‌شود (Mirza تنظیمات `on_hold_day` دارد و پنل‌های دیگر مثل Marzban این را دارند).
- API:
```http
PUT  /api/v2/peer/limits   { configurationName, id, data_limit_gb, expire_at, on_hold_days }
POST /api/v2/peer/limits/add { configurationName, id, add_data_gb, add_days }   # حجم/زمان اضافه
```
- job های فعلی برای سازگاری باقی می‌مانند ولی ربات دیگر از آن‌ها استفاده نمی‌کند.

---

<a id="feat-03"></a>
## FEAT-03 ⭐⭐⭐ عملیات اتمیک و idempotent

**چرا:** خرید الان ۴ درخواست است و اگر وسطش قطع شود، کاربر بدون محدودیت می‌ماند ([BOT-05](06-OxBot-Integration.md#bot-05)). تمدید ~۱۰ درخواست و ۳-۴ بار `wg-quick save` است ([BOT-10](06-OxBot-Integration.md#bot-10)). اگر ربات بعد از timeout دوباره تلاش کند، ممکن است دو peer ساخته شود.

**API پیشنهادی:**
```http
POST /api/v2/peers
Idempotency-Key: <invoice_id>
{ configurationName, name, limits: { data_limit_gb, expire_at, on_hold_days } }
→ { peer, config_file, qr_png_base64 }        # همه در یک پاسخ؛ تکرار با همان کلید = همان پاسخ

POST /api/v2/peer/renew
{ configurationName, id, reset_usage: true, data_limit_gb, expire_at }
→ ریست + محدودیت جدید + allow در یک تراکنش و یک wg-quick save

POST /api/v2/peers/bulk   { configurationName, action: restrict|allow|delete, ids: [...] }
```

---

<a id="feat-04"></a>
## FEAT-04 ⭐⭐ Webhook امضاشده برای رویدادها

**چرا:** الان ربات با کرون polling می‌کند تا بفهمد حجم کسی تمام شده یا ۸۰٪ رسیده (`volumewarn`، `daywarn`). زیرساخت webhook در پنل هست ([DashboardWebHooks.py](../src/modules/DashboardWebHooks.py)) ولی فقط `peer_created/deleted/updated` دارد.

**رویدادهای پیشنهادی:** `peer_restricted` (با دلیل)، `peer_allowed`، `peer_usage_reset`، `peer_limit_warning` (مثلاً ۸۰٪ حجم یا ۲۴ ساعت مانده — آستانه قابل تنظیم)، `job_failed`، `backup_completed`/`backup_failed`، `interface_down`، `db_error`.

**امنیت:** هدر `X-WGD-Signature: sha256=HMAC(secret, body)` و `X-WGD-Timestamp` تا ربات جعل را رد کند. ربات بلافاصله به کاربر پیام می‌دهد: «حجم شما تمام شد، برای تمدید ...».

---

<a id="feat-05"></a>
## FEAT-05 ⭐⭐⭐ API Key با محدوده

**چرا:** الان هر API Key = ادمین کامل = root ([SEC-03](02-Security-Vulnerabilities.md#sec-03)). ربات دوستتان روی اینترفیس‌های شما و کل سرور دسترسی دارد ([BOT-14](06-OxBot-Integration.md#bot-14)).

**طرح:**
- فیلدهای جدید: `Label`، `AllowedConfigurations` (لیست یا `*`)، `Scope` (`read` / `peers` / `admin`)، `AllowedIPs` (مثلاً فقط IP سرور ربات)، `LastUsedAt`، ذخیره به‌صورت **هش** (فقط هنگام ساخت یک بار نمایش داده شود).
- اعمال در `auth_req`: scope `peers` فقط به endpointهای peer همان اینترفیس‌ها دسترسی دارد؛ هیچ‌وقت به تنظیمات، PostUp، API Keyها، بکاپ و ریستور.
- انقضای واقعی ([SEC-04](02-Security-Vulnerabilities.md#sec-04)).

---

<a id="feat-06"></a>
## FEAT-06 ⭐⭐ بکاپ امن و خودکار

- رمزنگاری اختیاری با passphrase (AES-GCM یا `age`)؛ بدون passphrase ریستور ممکن نیست.
- ارسال خودکار هر بکاپ به **چت ادمین در تلگرام** (از طریق Bot API — بکاپ‌ها معمولاً چند مگابایت‌اند) یا S3/rclone. اگر سرور از دست برود، بکاپ جای دیگری هست.
- بکاپ خودکار `PreRestore` قبل از هر ریستور و قبل از حذف کانفیگ.
- retention جداگانه برای خودکار/دستی + pin.
- امضای HMAC روی MANIFEST و بررسی sha256 هنگام ریستور ([GB-03](05-Global-Backup-Review.md#gb-03)).
- ریستور انتخابی (فقط یک اینترفیس) و حالت «آزمایشی» که فقط تفاوت‌ها را نشان می‌دهد.

---

<a id="feat-07"></a>
## FEAT-07 ⭐⭐ تاریخچه‌ی مصرف اینترفیس

جدول `InterfaceUsageDaily(configuration, date, rx_gb, tx_gb)` که هر ساعت با اختلاف Total فعلی و قبلی پر شود. مزایا:
- نمودار روزانه/ماهانه در UI و API برای پنل وب ربات («مصرف این ماه سرور»).
- مقایسه با سقف ترافیک دیتاسنتر.
- اگر جدول snapshot روزی خراب/ریست شود ([TU-03](04-Total-Usage-Review.md#tu-03))، تاریخچه از دست نمی‌رود.
- دکمه‌ی «شروع دوره‌ی جدید» با ثبت در audit log.

---

<a id="feat-08"></a>
## FEAT-08 ⭐⭐ Health، متریک و هشدار

```http
GET /api/health   (بدون احراز، فقط وضعیت)
→ { ok, db: "ok", interfaces: {wg0: "up"}, job_loop_last_run: "...", last_backup: "...", disk_free_pct }
GET /metrics      (با API Key، فرمت Prometheus)
```
و ارسال هشدار تلگرامی به ادمین وقتی: حلقه‌ی job بیش از ۱۰ دقیقه اجرا نشده ([BUG-02](03-Backend-Bugs-and-Logic.md#bug-02))، job به‌خاطر شکست پاک شد ([BUG-05](03-Backend-Bugs-and-Logic.md#bug-05))، خطای دیتابیس/self-heal ([PERF-07](07-Performance-and-Stability.md#perf-07))، بکاپ شکست خورد، اینترفیس down شد، دیسک بیش از ۹۰٪ پر است. ربات هم می‌تواند قبل از فروش، health را چک کند و اگر پنل سالم نیست، فروش را موقتاً متوقف کند.

---

<a id="feat-09"></a>
## FEAT-09 ⭐⭐ Audit log

ثبت هر عملیات تغییردهنده با: زمان، انجام‌دهنده (ادمین یا **برچسب API Key**)، IP، endpoint، peer/کانفیگ هدف، و خلاصه‌ی قبل/بعد. با دو ربات روی یک پنل، تنها راه فهمیدن «چه کسی این کاربر را حذف کرد» همین است. الان `DashboardLog` فقط URL و بخشی از کلید را دارد و به ۵۰۰۰ ردیف محدود است.

---

<a id="feat-10"></a>
## FEAT-10 ⭐ محدودیت سرعت per-peer

`tcconfig` در `requirements.txt` هست ولی استفاده نمی‌شود. با HTB روی اینترفیس WireGuard (و IFB برای ترافیک ورودی) بر اساس IP داخلی peer می‌توان سرعت هر کاربر را محدود کرد ← فروش پلن‌های سرعتی (مثلاً «گیمینگ بدون محدودیت سرعت» در برابر «اقتصادی ۱۰ مگ»). نیاز به تست دقیق دارد.

---

<a id="feat-11"></a>
## FEAT-11 ⭐ تشخیص اشتراک‌گذاری اکانت

جدول تاریخچه‌ی endpoint موجود است ([WireguardConfiguration.py:588-619](../src/modules/WireguardConfiguration.py#L588-L619)). اگر endpoint یک peer در بازه‌ی کوتاه بین چند IP/شبکه‌ی مختلف جابه‌جا شود، نشانه‌ی استفاده‌ی هم‌زمان چند نفر است. گزارش در UI + webhook برای ربات.

---

<a id="feat-12"></a>
## FEAT-12 ⭐ صفحه‌ی اشتراک کاربر

یک لینک دائمی امن برای هر peer (بر پایه‌ی Share Link موجود) که صفحه‌ای سبک با مصرف، تاریخ انقضا، QR و دکمه‌ی دانلود نشان دهد. ربات به‌جای فایل، این لینک را می‌فرستد و کاربر همیشه کانفیگ و وضعیت به‌روز را می‌بیند.

---

<a id="feat-13"></a>
## FEAT-13 ⭐⭐ تخصیص IP توسط پنل (quick win)

ربات الان IP را خودش انتخاب می‌کند و برای جلوگیری از تداخل از `GET_LOCK` در MySQL، ترکیب IPهای دیتابیس ربات و پنل، و انتخاب تصادفی استفاده می‌کند (`remote_WGDashboard.php:198-307`). در حالی که `addPeers` اگر `allowed_ips` خالی باشد، **خودش اولین IP آزاد را زیر قفل کانفیگ انتخاب می‌کند** ([dashboard.py:1465-1473](../src/dashboard.py#L1465-L1473)) و در پاسخ برمی‌گرداند. پس بدون هیچ تغییری در پنل، ربات می‌تواند کل منطق IP را حذف کند و `allowed_ip` را از پاسخ بخواند. (اگر IPهای «رزروشده‌ی» ربات برای کاربران حذف‌شده مهم است، قبلش بررسی کنید.)

---

<a id="feat-14"></a>
## FEAT-14 ⭐⭐ ابزار چرخش کلید اینترفیس

چون کلید خصوصی سرور افشا شده (سند ۰۱)، یک wizard مفید است: ساخت کلید جدید برای اینترفیس (یا اینترفیس جدید روی پورت دیگر با همان peerها)، تولید کانفیگ جدید برای همه، و یک endpoint که ربات با آن لینک/فایل جدید را برای هر کاربر بفرستد. اینترفیس قدیمی تا تاریخ مشخص فعال می‌ماند تا کاربران مهاجرت کنند.
