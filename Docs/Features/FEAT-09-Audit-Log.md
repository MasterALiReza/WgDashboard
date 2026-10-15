# FEAT-09 — Audit Log (چه کسی، چه کاری، روی چه چیزی)

| | |
|---|---|
| اولویت | ⭐⭐ ارزش بالا |
| حجم کار | S (۱ روز) |
| حل می‌کند | ردگیری کارهای دو ربات و ادمین روی یک پنل ([BOT-14](../06-OxBot-Integration.md#bot-14))؛ تحقیق بعد از نفوذ ([سند ۰۱](../01-URGENT-Leaked-Secrets.md)) |
| وابستگی | [FEAT-05](FEAT-05-Scoped-API-Keys.md) برای برچسب کلیدها (بدون آن «api_key:<prefix>» ثبت می‌شود) |

## ۱. مسئله
`DashboardLog` فعلی فقط URL، IP و ۴ کاراکتر کلید را نگه می‌دارد، درخواست‌های GET را ثبت نمی‌کند، به **۵۰۰۰ ردیف** محدود است ([dashboard.py:161-167](../../src/dashboard.py#L161-L167)) و کارهای سیستمی (job ها، reconcile، auto-heal) را اصلاً ثبت نمی‌کند. وقتی کاربری ناپدید می‌شود نمی‌توان فهمید ربات شما، ربات دوستتان، ادمین یا یک job او را حذف کرده است.

## ۲. اهداف
1. ثبت **هر عملیات تغییردهنده** با: زمان، انجام‌دهنده (ادمین / کدام API Key / سیستم)، IP، شناسه‌ی درخواست، اینترفیس، peerهای هدف، نتیجه، و جزئیات (قبل/بعد) بدون اسرار.
2. جستجو بر اساس peer، اینترفیس، انجام‌دهنده و بازه‌ی زمانی.
3. نگهداری بر اساس زمان (پیش‌فرض ۱۸۰ روز)، نه تعداد.
4. قابلیت تشخیص دستکاری (زنجیره‌ی هش).

## ۳. غیر اهداف
ثبت درخواست‌های خواندنی (حجم زیاد و ارزش کم؛ برای آن access log گانیکورن هست).

## ۴. طراحی

### ۴.۱ جداول (دیتابیس `wgdashboard_log`)
**`AuditLog`**

| ستون | توضیح |
|------|--------|
| `AuditID` | UUID |
| `At` | زمان UTC |
| `ActorType` | `admin` · `api_key` · `system` · `job` · `limits` |
| `ActorID` / `ActorLabel` | نام کاربری ادمین، یا `KeyID`/`Label` کلید، یا نام زیرسیستم |
| `IP` | بعد از ProxyFix |
| `RequestID` | از هدر `X-Request-ID` (اگر ربات فرستاده) یا تولیدشده؛ در هدر پاسخ هم برمی‌گردد |
| `Action` | مثل `peer.create`، `peer.delete`، `peer.restrict`، `limits.update`، `config.update`، `apikey.create`، `backup.restore`، `settings.update` |
| `Configuration` | |
| `Targets` | JSON لیست public keyها |
| `Result` | `ok` · `fail` · `partial` |
| `Details` | JSON پاک‌سازی‌شده (قبل/بعد، پیام خطا) |
| `PrevHash` / `RowHash` | `RowHash = sha256(PrevHash + canonical_json(row))` |

ایندکس‌ها: `(At)`، `(Configuration, At)`، `(ActorID, At)`.

**`AuditTargets`** (`AuditID`, `PeerID`) با ایندکس روی `PeerID` ← «تاریخچه‌ی یک کاربر» در یک کوئری.

### ۴.۲ ثبت
- endpointهای تغییردهنده (v1 و v2) با decorator:
```python
@app.post(f'{APP_PREFIX}/api/deletePeers/<configName>')
@audited("peer.delete", configuration=lambda kw: kw["configName"],
         targets=lambda req: req.get_json().get("peers", []))
def API_deletePeers(configName): ...
```
- کارهای سیستمی مستقیم: `audit.system("limits", "peer.restrict", conf, [ids], details={"reason": ...})` در موتور محدودیت، `PeerJobs.runJob`، `reconcileWithKernel` (repaired/removed)، auto-heal (اگر حفظ شود)، ریستور بکاپ.
- **پاک‌سازی اسرار:** هر کلیدی که با `private_key|preshared_key|password|secret|token|totp|apikey|Key` مطابقت کند ← `"***"` (بازگشتی در JSON تو در تو).
- نوشتن در یک صف درون‌حافظه و ذخیره‌ی دسته‌ای هر ۱ ثانیه توسط یک thread کوچک، تا درخواست‌ها منتظر دیتابیس لاگ نمانند. هنگام خاموشی، صف flush می‌شود.

### ۴.۳ زنجیره‌ی هش
هر ردیف هش ردیف قبلی را دارد. حذف یا تغییر یک ردیف وسط زنجیره در `verify` آشکار می‌شود. هرس قدیمی‌ها با ثبت «لنگر» (هش آخرین ردیف حذف‌شده) انجام می‌شود تا زنجیره‌ی باقی‌مانده قابل بررسی بماند.

### ۴.۴ نگهداری
```ini
[Audit]
retention_days = 180
```
هرس در همان کار ۶ ساعته ([dashboard.py:147-223](../../src/dashboard.py#L147-L223)).

## ۵. API
```http
GET /api/v2/audit?configuration=wg0&peer=<pk>&actor=<key_id>&action=peer.delete&from=…&to=…&limit=100&cursor=…
    (فقط session ادمین)
GET /api/v2/audit/verify     → { "ok": true, "rows_checked": 182345 }
GET /api/v2/audit/export?format=csv&from=…&to=…
```

## ۶. امنیت
- خواندن فقط با session ادمین (API Key حتی admin نه — تا کلید دزدیده‌شده نتواند ردپا را ببیند).
- هیچ اسراری ذخیره نمی‌شود (تست جدا دارد).
- IP کاربران VPN ثبت نمی‌شود؛ فقط IP فراخواننده‌ی API.

## ۷. سازگاری
`DashboardLog` فعلی دست نمی‌خورد.

## ۸. تغییرات ربات OxBot
اختیاری ولی مفید: در هر درخواست هدر `X-Request-ID: inv-<id_invoice>-<action>` بفرستد تا در Audit Log مستقیم به فاکتور برسید.

## ۹. تغییرات UI
- صفحه‌ی **Audit** در Settings با فیلترها و جدول.
- تب **History** در مودال جزئیات peer (از `AuditTargets`).

## ۱۰. تست
- هر endpoint تغییردهنده دقیقاً یک ردیف با `ActorType` درست.
- پاک‌سازی: بدنه‌ی `addPeers` با `private_key` ← `***` در `Details`.
- زنجیره: تغییر دستی یک ردیف ← `verify` آن را گزارش کند.
- کار سیستمی: اجرای job محدودسازی ← ردیف با `ActorType = job`.

## ۱۱. استقرار
افزودنی؛ rollback = غیرفعال کردن decorator با پرچم `[Features] audit_log`.

## ۱۲. فایل‌های درگیر
`src/modules/AuditLog.py` (جدید)، [dashboard.py](../../src/dashboard.py) (decorator روی endpointها، `X-Request-ID`)، [PeerJobs.py](../../src/modules/PeerJobs.py)، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py)، صفحه‌ی جدید Vue، `tests/test_audit.py`.
