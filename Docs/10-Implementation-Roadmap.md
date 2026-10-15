# ۱۰ — نقشه‌ی راه اجرای همه‌ی پیشنهادها

> این سند همه‌ی پیشنهادهای اسناد ۰۱ تا ۰۹ (رفع باگ‌ها و فیچرها) را به فازهای قابل اجرا تبدیل می‌کند: ترتیب، وابستگی، معیار پذیرش، تست و روش استقرار.
> طراحی کامل هر فیچر: [Features/](Features/README.md)

## اصول اجرا
1. **هر تغییر کوچک و مستقل:** یک شاخه و یک PR برای هر ID (یا گروه کوچک مرتبط)، با تست.
2. **اول جلوی ضرر، بعد امکانات:** امنیت و از دست رفتن داده/پول ← پایداری ← فیچر.
3. **سازگاری با ربات فعلی:** هیچ endpoint نسخه‌ی ۱ شکسته نمی‌شود؛ قابلیت‌های جدید زیر `/api/v2`.
4. **هر استقرار قابل برگشت:** بکاپ pin‌شده قبل از استقرار، tag نسخه، دستور rollback مشخص.

## نمای کلی

| فاز | محتوا | زمان تقریبی | پیش‌نیاز |
|-----|-------|-------------|-----------|
| [۰](#phase-0) | اقدامات عملیاتی بدون کد | ۱ روز (شما) | — |
| [۱](#phase-1) | رفع بحرانی‌های پنل | ۲-۳ روز | ۰ |
| [۲](#phase-2) | رفع باگ‌های ربات | ۱ روز | ۱ (BUG-01) |
| [۳](#phase-3) | پایه‌ی امنیتی | ۳ روز | ۱ |
| [۴](#phase-4) | پلتفرم ربات (v2) | ۴-۵ روز | ۳ |
| [۵](#phase-5) | پایداری، کارایی، بکاپ | ۴ روز | ۱ |
| [۶](#phase-6) | فیچرهای اختیاری | به انتخاب شما | ۴ |

---

<a id="phase-0"></a>
## فاز ۰ — اقدامات عملیاتی (بدون تغییر کد)

| # | کار | مرجع | معیار انجام |
|---|-----|------|-------------|
| 0.1 | چرخش همه‌ی اسرار (توکن تلگرام، MySQL، API Key، پسورد ادمین، `flask_secret_key`، root) | [سند ۰۱](01-URGENT-Leaked-Secrets.md) | مقادیر قدیمی دیگر کار نمی‌کنند |
| 0.2 | پاک کردن `scratch/` و بکاپ‌ها از تاریخچه‌ی git | [سند ۰۱ مرحله ۵](01-URGENT-Leaked-Secrets.md#purge) | `git log --all -- scratch` خالی |
| 0.3 | بررسی نفوذ روی سرورها | سند ۰۱ مرحله ۳ | کلید/کرون/پلاگین ناشناس وجود ندارد |
| 0.4 | غیرفعال کردن پرتال Clients | [SEC-11](02-Security-Vulnerabilities.md#sec-11) | `/client` → 404 |
| 0.5 | bind پنل روی `127.0.0.1` + nginx با TLS | بخش «نکات تکمیلی» [سند ۰۲](02-Security-Vulnerabilities.md) | پورت 10086 از بیرون بسته |
| 0.6 | `chmod 700 /etc/wireguard && chmod 600 /etc/wireguard/*.conf` | [SEC-10](02-Security-Vulnerabilities.md#sec-10) | |
| 0.7 | Keepalive/MTU پیش‌فرض Peer را `0` نگذارید | [BUG-09](03-Backend-Bugs-and-Logic.md#bug-09) | افزودن peer از UI کار می‌کند |
| 0.8 | یکسان‌سازی منطقه‌ی زمانی سرور ربات و پنل | [BOT-07](06-OxBot-Integration.md#bot-07) | `timedatectl` و `date_default_timezone_get` یکی |
| 0.9 | کوئری‌های پایش job و drift ترافیک | [BOT-06](06-OxBot-Integration.md#bot-06)، [TU-01](04-Total-Usage-Review.md#tu-01) | نتیجه ثبت شد |
| 0.10 | Global Backup دستی و دانلود آن به جای امن | — | فایل خارج از سرور |

---

<a id="phase-1"></a>
## فاز ۱ — رفع بحرانی‌های پنل

| # | ID | تغییر | فایل‌ها | معیار پذیرش (تست) |
|---|----|-------|---------|-------------------|
| 1.1 | [SEC-01](02-Security-Vulnerabilities.md#sec-01) | `APIAccessed` ← `flask.g` | dashboard.py | تست هم‌زمانی: ۱۰۰۰ ورود ناموفق هم‌زمان با درخواست‌های API Key ← هیچ session |
| 1.2 | [SEC-02](02-Security-Vulnerabilities.md#sec-02) | مخفی کردن `flask_secret_key` و پسوردها در `toJson` | DashboardConfig.py | پاسخ `getDashboardConfiguration` شامل این کلیدها نیست؛ ذخیره‌ی تنظیمات ایمیل همچنان کار می‌کند |
| 1.3 | [SEC-04](02-Security-Vulnerabilities.md#sec-04) | بررسی انقضا در هر درخواست + `GetConfig(...)[1]` | dashboard.py | کلید منقضی بدون ری‌استارت 401 |
| 1.4 | [SEC-05](02-Security-Vulnerabilities.md#sec-05) | حذف `PrivateKey` از `toJson` اینترفیس | WireguardConfiguration.py، AmneziaConfiguration.py، صفحه‌ی ویرایش کانفیگ | UI ویرایش کانفیگ کار می‌کند؛ پاسخ API بدون PrivateKey |
| 1.5 | [SEC-09](02-Security-Vulnerabilities.md#sec-09) | نوشتن اتمیک ini | DashboardConfig.py | kill وسط ذخیره ← ini سالم |
| 1.6 | [BUG-01](03-Backend-Bugs-and-Logic.md#bug-01) | `configurationPeers` فقط فعال‌ها + فیلد `restricted` | dashboard.py، Peer.py | UI بدون تکرار؛ ربات کاربر محدود را «غیرفعال» می‌بیند |
| 1.7 | [BUG-02](03-Backend-Bugs-and-Logic.md#bug-02) | `try/except` برای هر job + اعتبارسنجی `savePeerScheduleJob` | PeerJobs.py، dashboard.py | job با Value خالی ← بقیه اجرا می‌شوند |
| 1.8 | [BUG-03](03-Backend-Bugs-and-Logic.md#bug-03) | حذف auto-heal خودکار (فقط دکمه‌ی دستی) | WireguardConfiguration.py، Peer.py | دیتابیس خالی + conf قدیمی ← کلیدها عوض **نمی‌شوند** |
| 1.9 | [BUG-04](03-Backend-Bugs-and-Logic.md#bug-04) | گزارش `not found` در حذف | WireguardConfiguration.py | حذف id ناموجود ← `status: false` |
| 1.10 | [BUG-05](03-Backend-Bugs-and-Logic.md#bug-05) | عدم حذف خودکار job های محدودیت + هشدار | PeerJobs.py | ۱۱ شکست ← job باقی و هشدار در لاگ |
| 1.11 | [BUG-06](03-Backend-Bugs-and-Logic.md#bug-06) | جایگزینی اتمیک لیست job ها و قفل | PeerJobs.py | تست هم‌زمانی save/run |
| 1.12 | [BUG-07](03-Backend-Bugs-and-Logic.md#bug-07)، [TU-02](04-Total-Usage-Review.md#tu-02) | ریستور per-config با drop+import و روشن کردن مجدد | WireguardConfiguration.py | ریستور موفق؛ اینترفیس دوباره up |
| 1.13 | [BUG-08](03-Backend-Bugs-and-Logic.md#bug-08) | آرگومان `DashboardWebHooks` | dashboard.py | ساخت کانفیگ wg از بکاپ بدون خطا |
| 1.14 | [BUG-13](03-Backend-Bugs-and-Logic.md#bug-13) | sleep قبل از `continue` | dashboard.py | CPU در ریستور عادی |
| 1.15 | [TU-01](04-Total-Usage-Review.md#tu-01) | `peer_total_data = rx + tx` در حذف + اصلاح داده | WireguardConfiguration.py | بعد از حذف، `Total == Sent + Receive` |
| 1.16 | [GB-01](05-Global-Backup-Review.md#gb-01)، [GB-02](05-Global-Backup-Review.md#gb-02)، [GB-05](05-Global-Backup-Review.md#gb-05) | توقف threadها، dispose engineها، حذف wal/shm، بکاپ pre-restore، maintenance تا ری‌استارت | GlobalBackup.py، dashboard.py | ریستور زیر بار ← `integrity_check = ok` |
| 1.17 | [FE-01](08-Frontend-Integration.md#fe-01)، [FE-03](08-Frontend-Integration.md#fe-03) | مدیریت پاسخ‌های غیر 2xx؛ رفع گیر کردن Global Backup؛ `getUrl` | fetch.js، globalBackup.vue | 409/429/503 پیام درست نشان می‌دهند |

**تحویل:** نسخه‌ی `v4.3.3-oxbot.1`.

---

<a id="phase-2"></a>
## فاز ۲ — رفع باگ‌های ربات (PHP)

| # | ID | تغییر | معیار پذیرش |
|---|----|-------|-------------|
| 2.1 | [BOT-01](06-OxBot-Integration.md#bot-01)، [BOT-08](06-OxBot-Integration.md#bot-08) | جستجو با public key؛ اول لیست محدودها | toggle فعال/غیرفعال برای کاربر محدود کار می‌کند |
| 2.2 | [BOT-03](06-OxBot-Integration.md#bot-03) | حذف ریست در `extra_volume` | خرید ۵ گیگ بعد از اتمام ۲۰ گیگ ← فقط ۵ گیگ قابل استفاده |
| 2.3 | [BOT-04](06-OxBot-Integration.md#bot-04) | بررسی `restricted_reason` قبل از allow در `extra_time` و ساخت مجدد job حجم | کاربر با حجم تمام‌شده بعد از تمدید زمان وصل نمی‌شود |
| 2.4 | [BOT-05](06-OxBot-Integration.md#bot-05) | بررسی نتیجه‌ی `setjob`/`deletejob` با retry | شکست setjob ← سرویس تحویل نمی‌شود/ادمین مطلع می‌شود |
| 2.5 | [BOT-09](06-OxBot-Integration.md#bot-09) | حذف `allowAccessPeers` قبل از حذف | |
| 2.6 | [BOT-11](06-OxBot-Integration.md#bot-11) | `downloadconfig` حداکثر ۳ تلاش | |
| 2.7 | [BOT-13](06-OxBot-Integration.md#bot-13) | `user_info` حداقلی + پاک‌سازی `configuration` قدیمی | کلید سرور در دیتابیس ربات نیست |
| 2.8 | [FEAT-13 مرحله ۱](Features/FEAT-13-Panel-IP-Allocation.md) | حذف منطق IP از ربات | دو خرید هم‌زمان ← دو IP متفاوت |

---

<a id="phase-3"></a>
## فاز ۳ — پایه‌ی امنیتی

| # | ID | تغییر |
|---|----|-------|
| 3.1 | [FEAT-05](Features/FEAT-05-Scoped-API-Keys.md) | API Key با scope/اینترفیس/IP، ذخیره‌ی هش (حل SEC-03، BOT-14) |
| 3.2 | [SEC-06](02-Security-Vulnerabilities.md#sec-06) | `SameSite=Strict`، `Secure`؛ endpointهای GET تغییردهنده ← POST (با تغییر فرانت) |
| 3.3 | [SEC-07](02-Security-Vulnerabilities.md#sec-07) | `ProxyFix` + شمارش فقط ورودهای ناموفق |
| 3.4 | [SEC-08](02-Security-Vulnerabilities.md#sec-08) | عمر session ۱۲ ساعت + `session_epoch` |
| 3.5 | [SEC-10](02-Security-Vulnerabilities.md#sec-10) | فایل‌های موقت امن، lock در `/run`، اصلاح `chmod` در wgd.sh |
| 3.6 | [SEC-12](02-Security-Vulnerabilities.md#sec-12)، [SEC-13](02-Security-Vulnerabilities.md#sec-13)، [SEC-14](02-Security-Vulnerabilities.md#sec-14)، [SEC-15](02-Security-Vulnerabilities.md#sec-15)، [SEC-16](02-Security-Vulnerabilities.md#sec-16) | geolocation اختیاری، TOTP با پنجره، اعتبارسنجی مسیرها، حذف پیوست‌ها، محدود کردن CORS |
| 3.7 | [GB-03](05-Global-Backup-Review.md#gb-03)، [GB-04](05-Global-Backup-Review.md#gb-04) | بررسی MANIFEST؛ عدم ریستور کد؛ مسیر conf از سرور فعلی؛ ریستور فقط با session |
| 3.8 | [FEAT-09](Features/FEAT-09-Audit-Log.md) | Audit Log |
| 3.9 | [FEAT-08](Features/FEAT-08-Health-Metrics-Alerts.md) | Health، متریک، هشدار تلگرامی (+ بررسی سلامت قبل از فروش در ربات) |

**تحویل:** `v4.3.3-oxbot.2` + جایگزینی کلید ربات‌ها با کلیدهای محدود و revoke کلیدهای legacy.

---

<a id="phase-4"></a>
## فاز ۴ — پلتفرم ربات (v2)

| # | فیچر | نکته |
|---|------|------|
| 4.1 | [FEAT-01](Features/FEAT-01-Lightweight-Bot-API.md) | ربات با fallback به v1 |
| 4.2 | [FEAT-02](Features/FEAT-02-Peer-Limits.md) | با پرچم خاموش مستقر، `migrate-jobs` با dry-run، سپس روشن |
| 4.3 | [FEAT-03](Features/FEAT-03-Atomic-Idempotent-Operations.md) | خرید/تمدید/حذف در ربات به v2 |
| 4.4 | [FEAT-04](Features/FEAT-04-Signed-Event-Webhooks.md) | گیرنده‌ی webhook در ربات؛ کرون‌های هشدار به تور ایمنی ساعتی تبدیل می‌شوند |

**تست پذیرش انتها-به-انتها (روی یک اینترفیس آزمایشی):** خرید ← مصرف تا ۸۰٪ (پیام هشدار) ← اتمام حجم (قطع + پیام) ← خرید حجم اضافه (وصل) ← تمدید (ریست بدون قطع) ← حذف. هر مرحله با Audit Log بررسی شود.

---

<a id="phase-5"></a>
## فاز ۵ — پایداری، کارایی، بکاپ

| # | ID | تغییر |
|---|----|-------|
| 5.1 | [PERF-01](07-Performance-and-Stability.md#perf-01) | ایندکس دیکشنری job/share link |
| 5.2 | [PERF-03](07-Performance-and-Stability.md#perf-03) | reload درجای کانفیگ به‌جای ساخت شیء جدید |
| 5.3 | [PERF-04](07-Performance-and-Stability.md#perf-04) | registry موتورهای دیتابیس |
| 5.4 | [PERF-05](07-Performance-and-Stability.md#perf-05)، [PERF-11](07-Performance-and-Stability.md#perf-11) | timeout کوتاه‌تر قفل‌ها، پاسخ 503 `BUSY` |
| 5.5 | [PERF-07](07-Performance-and-Stability.md#perf-07) | حذف self-heal زمان اجرا (فقط maintenance + هشدار) |
| 5.6 | [PERF-08](07-Performance-and-Stability.md#perf-08)، [PERF-09](07-Performance-and-Stability.md#perf-09)، [PERF-10](07-Performance-and-Stability.md#perf-10) | سقف ping/traceroute، retention جداول tracking، INSERT OR IGNORE |
| 5.7 | [BUG-09](03-Backend-Bugs-and-Logic.md#bug-09) تا [BUG-12](03-Backend-Bugs-and-Logic.md#bug-12)، [BUG-14](03-Backend-Bugs-and-Logic.md#bug-14) تا [BUG-21](03-Backend-Bugs-and-Logic.md#bug-21) | باگ‌های متوسط/جزئی بک‌اند |
| 5.8 | [TU-04](04-Total-Usage-Review.md#tu-04) تا [TU-06](04-Total-Usage-Review.md#tu-06) | snapshot اتمیک، صفر واقعی، refresh زیر قفل |
| 5.9 | [FEAT-06](Features/FEAT-06-Secure-Backups.md) فاز A، B، C | بکاپ سازگار، رمزنگاری، ارسال به تلگرام، ریستور انتخابی (+ GB-06..16) |
| 5.10 | [FEAT-07](Features/FEAT-07-Interface-Usage-History.md) | تاریخچه‌ی مصرف + اصلاحیه‌ی TU-03 |
| 5.11 | [FE-02](08-Frontend-Integration.md#fe-02)، [FE-04](08-Frontend-Integration.md#fe-04) تا [FE-06](08-Frontend-Integration.md#fe-06) | باقی موارد فرانت |

---

<a id="phase-6"></a>
## فاز ۶ — فیچرهای اختیاری (به انتخاب شما)

| فیچر | پیشنهاد زمان |
|------|---------------|
| [FEAT-14](Features/FEAT-14-Interface-Key-Rotation.md) چرخش کلید اینترفیس | **بلافاصله بعد از فاز ۴** (به‌خاطر افشای کلید سرور) |
| [FEAT-12](Features/FEAT-12-Subscription-Page.md) صفحه‌ی اشتراک | بعد از فاز ۴ |
| [FEAT-11](Features/FEAT-11-Account-Sharing-Detection.md) تشخیص اشتراک‌گذاری | هر زمان (یک هفته فقط-ثبت) |
| [FEAT-10](Features/FEAT-10-Per-Peer-Speed-Limit.md) محدودیت سرعت | اگر پلن سرعتی می‌خواهید |

---

## ماتریس ردیابی (همه‌ی یافته‌ها ← فاز)

| یافته | فاز |
|-------|-----|
| SEC-01، 02، 04، 05، 09 | ۱ |
| SEC-03 | ۳ (FEAT-05)؛ موقت: allowlist در سند ۰۲ |
| SEC-06، 07، 08، 10، 12، 13، 14، 15، 16 | ۳ |
| SEC-11 | ۰ |
| BUG-01 تا 08، 13 | ۱ |
| BUG-09 تا 12، 14 تا 21 | ۵ (BUG-09 موقتاً در ۰) |
| TU-01، 02 | ۱ |
| TU-03 | ۵ (FEAT-07) — موقت: SQL دستی سند ۰۴ |
| TU-04، 05، 06 | ۵ |
| GB-01، 02، 05 | ۱ |
| GB-03، 04 | ۳ |
| GB-06 تا 16 | ۵ (FEAT-06) |
| BOT-01، 03، 04، 05، 08، 09، 11، 13 | ۲ |
| BOT-02، 06، 10، 12 | ۴ |
| BOT-07 | ۰ و ۴ (FEAT-02) |
| BOT-14 | ۳ (FEAT-05) |
| BOT-15 | ۱ (BUG-03، BUG-04) |
| PERF-01 تا 11 | ۵ (PERF-02 در ۴، PERF-06 با FEAT-02) |
| FE-01، 03 | ۱ |
| FE-02 | ۱ (با BUG-01) |
| FE-04، 05، 06 | ۳ و ۵ |

---

## زیرساخت تست و CI
- **تست‌ها:** `pytest` با SQLite موقت؛ یک fake برای `subprocess` (`wg`، `wg-quick`، `tc`) که فراخوانی‌ها را ثبت می‌کند. تست‌های فعلی در `tests/` الگو هستند.
- **GitHub Actions** (workflowها در commit `fba6ecc` به‌خاطر محدودیت scope توکن حذف شده‌اند؛ برای بازگرداندن، توکن با scope `workflow` لازم است):
  - `pytest`
  - `python -m compileall src`
  - build فرانت و خطا اگر `src/static/dist` با build جدید فرق داشته باشد ([FE-05](08-Frontend-Integration.md#fe-05))
  - `gitleaks` برای جلوگیری از تکرار سند ۰۱
- **تست دستی روی VM** با WireGuard واقعی قبل از هر نسخه.

## روند انتشار و استقرار
1. نسخه: `DashboardConfig.DashboardVersion = "v4.3.3-oxbot.N"` ([DashboardConfig.py:15](../src/modules/DashboardConfig.py#L15)) و یک `CHANGELOG.md`.
2. روی سرور:
```bash
cd /path/to/WGDashboard/src
# 1) بکاپ pin‌شده از UI (یا API) و دانلود آن
git fetch && git checkout v4.3.3-oxbot.N
./venv/bin/pip install -r requirements.txt
sudo systemctl restart wg-dashboard
curl -fsS http://127.0.0.1:10086/api/health      # بعد از FEAT-08
```
3. Smoke test با ربات: ساخت یک سرویس تست، تمدید، حذف.
4. **Rollback:** `git checkout <نسخه‌ی قبلی>` + `systemctl restart wg-dashboard`؛ اگر migration دیتابیس داشته، ریستور بکاپ همان لحظه (هر سند فیچر بخش Rollback خودش را دارد).
