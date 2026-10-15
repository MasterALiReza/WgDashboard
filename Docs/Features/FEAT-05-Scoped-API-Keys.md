# FEAT-05 — API Key با محدوده (Scope، اینترفیس، IP)

| | |
|---|---|
| اولویت | ⭐⭐⭐ ضروری (امنیتی) |
| حجم کار | M (۲ روز پنل، نیم روز UI) |
| حل می‌کند | [SEC-03](../02-Security-Vulnerabilities.md#sec-03)، [SEC-04](../02-Security-Vulnerabilities.md#sec-04)، [BOT-14](../06-OxBot-Integration.md#bot-14)، بخشی از [SEC-01](../02-Security-Vulnerabilities.md#sec-01) و [SEC-02](../02-Security-Vulnerabilities.md#sec-02) |
| وابستگی | ندارد؛ پایه‌ی امنیتی بقیه‌ی فیچرهاست |

## ۱. مسئله
- هر API Key الان **ادمین کامل** است: می‌تواند PostUp بنویسد (= root)، کلیدهای دیگر را ببیند/حذف کند، پسورد ادمین را عوض کند و بکاپ ریستور کند.
- ربات دوستتان با کلید خودش به همه‌ی اینترفیس‌های شما دسترسی دارد.
- کلیدها **متن ساده** در دیتابیس و در هر Global Backup هستند؛ انقضا تا ری‌استارت اعمال نمی‌شود (SEC-04)؛ نمی‌دانید کدام کلید آخرین بار کی و از کجا استفاده شده.

## ۲. اهداف
1. هر کلید: **برچسب**، **مجموعه‌ی دسترسی (scope)**، **لیست اینترفیس‌های مجاز**، **لیست IPهای مجاز**، انقضای واقعی.
2. ذخیره‌ی **هش** کلید؛ مقدار کامل فقط یک بار هنگام ساخت نمایش داده شود.
3. ثبت `LastUsedAt` و `LastUsedIP`.
4. اعمال روی **همه‌ی** endpointها (v1 و v2) با سیاست «پیش‌فرض: رد».
5. کلیدهای فعلی بدون قطعی به‌عنوان «legacy admin» ادامه دهند.

## ۳. غیر اهداف
- OAuth/JWT. کلید ثابت با scope برای ربات کافی است.
- RBAC برای کاربران انسانی (فقط یک ادمین وجود دارد).

## ۴. طراحی

### ۴.۱ تغییر جدول `DashboardAPIKeys`
ستون‌های فعلی: `Key` (PK)، `CreatedAt`، `ExpiredAt` ([DashboardConfig.py:180-191](../../src/modules/DashboardConfig.py#L180-L191)). ستون‌های جدید:

| ستون | نوع | توضیح |
|------|------|--------|
| `KeyID` | String(36), unique | شناسه‌ی عمومی (UUID) |
| `KeyHash` | String(64), unique index | `sha256(key)` به hex. چون کلید ۲۵۶ بیت تصادفی است، sha256 کافی است (نیازی به bcrypt نیست) |
| `KeyPrefix` | String(8) | ۶ کاراکتر اول برای شناسایی در UI |
| `Label` | String(255) | مثلاً «OxBot» یا «ربات دوست» |
| `Scopes` | JSON | لیست scope ها |
| `Configurations` | JSON | لیست نام اینترفیس‌ها یا `["*"]` |
| `AllowedIPs` | JSON | لیست CIDR، خالی = همه |
| `LastUsedAt` / `LastUsedIP` | DateTime / String | (به‌روزرسانی حداکثر هر ۶۰ ثانیه برای جلوگیری از نوشتن زیاد) |
| `CreatedBy` | String(64) | `admin` |

**مهاجرت کلیدهای موجود** (یک‌بار، در شروع برنامه):
```python
for row in rows_without_keyhash:
    kid = str(uuid4())
    UPDATE DashboardAPIKeys SET KeyID=kid, KeyHash=sha256(row.Key), KeyPrefix=row.Key[:6],
           Label='Legacy key', Scopes='["admin"]', Configurations='["*"]', Key=kid   -- متن ساده حذف می‌شود
     WHERE Key = row.Key
```
بعد از مهاجرت، ستون `Key` فقط `KeyID` را نگه می‌دارد (PK می‌ماند، ولی دیگر محرمانه نیست). کلید جدید: `secrets.token_urlsafe(32)` با پیشوند خوانا مثل `wgd_` (برای تشخیص در اسکن‌های نشت مثل gitleaks).

### ۴.۲ Scopeها

| Scope | اجازه می‌دهد |
|-------|---------------|
| `config:read` | خلاصه‌ی اینترفیس (v2)، لیست کانفیگ‌ها و `getWireguardConfigurationInfo` (v1) — **فقط اینترفیس‌های مجاز** |
| `peers:read` | v2 `peer`، `peers/usage`، `peer/limits` (GET)؛ v1 `getAvailableIPs`، `getNumberOfAvailableIPs` |
| `peers:config` | دانلود فایل کانفیگ (حاوی کلید خصوصی کاربر): v2 `peer/config`، v1 `downloadPeer`، `downloadAllPeers` |
| `peers:write` | ساخت/ویرایش/حذف/محدود/آزاد/ریست/محدودیت/job: v1 `addPeers`، `updatePeerSettings`، `deletePeers`، `restrictPeers`، `allowAccessPeers`، `resetPeerData`، `savePeerScheduleJob`، `deletePeerScheduleJob`؛ v2 معادل‌ها |
| `webhooks:manage` | ساخت/ویرایش webhook برای اینترفیس‌های مجاز خود ([FEAT-04](FEAT-04-Signed-Event-Webhooks.md)) |
| `metrics:read` | `/metrics` و health جزئی ([FEAT-08](FEAT-08-Health-Metrics-Alerts.md)) |
| `admin` | همه‌چیز (رفتار فعلی). برای قابلیت Cross-Server پنل لازم است |

**هرگز برای غیر admin:** تنظیمات داشبورد، ویرایش کانفیگ اینترفیس/Raw/PostUp، مدیریت API Key، بکاپ/ریستور، ایمیل، Clients، OIDC، ping/traceroute، system status، `authenticate` (ساخت session با کلید — [dashboard.py:511-518](../../src/dashboard.py#L511-L518)).

### ۴.۳ اعمال در `auth_req`
```python
@app.before_request
def auth_req():
    g.actor = None
    raw = request.headers.get("wg-dashboard-apikey")
    if raw:
        rec = APIKeyStore.lookup(sha256_hex(raw))           # dict در حافظه، O(1)
        if not rec or rec.expired(now_utc()) or not rec.ip_allowed(request.remote_addr):
            return error("UNAUTHORIZED", status=401)
        g.actor = rec
        need = required_scope(request)                      # جدول نگاشت؛ مسیر ناشناخته ← "admin"
        if not rec.has_scope(need):
            return error("FORBIDDEN_SCOPE", status=403)
        conf = resolve_configuration(request)               # از path/query/body
        if conf and not rec.allows_configuration(conf):
            return error("FORBIDDEN_SCOPE", "Configuration not allowed for this key", status=403)
        APIKeyStore.touch(rec, request.remote_addr)
        return
    ...  # منطق فعلی session ادمین
```
- `resolve_configuration` از این منابع می‌خواند: پارامتر مسیر (`/api/addPeers/<configName>`)، query (`configurationName`، `configuration`)، بدنه (`ConfigurationName`، `configuration`، `Job.Configuration`).
- endpointهای لیستی (`getWireguardConfigurations`، `clients/allConfigurationsPeers`) برای کلید محدود، خروجی را **فیلتر** می‌کنند.
- `APIKeyStore` هر ۶۰ ثانیه و بعد از هر تغییر از دیتابیس تازه می‌شود؛ انقضا در **هر درخواست** با ساعت فعلی چک می‌شود (اصلاح SEC-04).
- `request.remote_addr` بعد از `ProxyFix` ([SEC-07](../02-Security-Vulnerabilities.md#sec-07)) واقعی است؛ بدون آن، allowlist IP پشت nginx بی‌معناست.

### ۴.۴ نگاشت مسیرهای v1
یک جدول صریح در `src/modules/APIScopes.py`، مثلاً:
```python
V1_SCOPES = {
    ("GET",  "/api/getWireguardConfigurationInfo"): "config:read",
    ("GET",  "/api/getAvailableIPs/"):              "peers:read",
    ("GET",  "/api/downloadPeer/"):                 "peers:config",
    ("POST", "/api/addPeers/"):                     "peers:write",
    ("POST", "/api/deletePeers/"):                  "peers:write",
    ("POST", "/api/restrictPeers/"):                "peers:write",
    ("POST", "/api/allowAccessPeers/"):             "peers:write",
    ("POST", "/api/resetPeerData/"):                "peers:write",
    ("POST", "/api/updatePeerSettings/"):           "peers:write",
    ("POST", "/api/savePeerScheduleJob"):           "peers:write",
    ("POST", "/api/deletePeerScheduleJob"):         "peers:write",
    ("GET",  "/api/handshake"):                     None,       # بدون نیاز
}
# هر مسیر دیگری ← "admin"
```
همه‌ی endpointهایی که ربات فعلی صدا می‌زند ([سند ۰۶](../06-OxBot-Integration.md)) در جدول هستند، پس ربات با کلید `peers:*` بدون تغییر کد کار می‌کند.

## ۵. API (فقط با session ادمین)
مدیریت کلید با خود API Key ممکن نیست (جلوگیری از ارتقای دسترسی).
```http
GET  /api/v2/api-keys
POST /api/v2/api-keys
{ "label": "OxBot", "scopes": ["config:read","peers:read","peers:config","peers:write","webhooks:manage"],
  "configurations": ["wg0"], "allowed_ips": ["203.0.113.10/32"], "expires_at": null }
→ { "key_id": "…", "key": "wgd_3q…", "key_prefix": "wgd_3q", ... }      ← مقدار کامل فقط همین یک بار
POST /api/v2/api-keys/update   { "key_id": "…", "label": "…", "scopes": [...], "configurations": [...], "allowed_ips": [...] }
POST /api/v2/api-keys/revoke   { "key_id": "…" }
```
endpointهای قدیمی (`newDashboardAPIKey` و ...) برای سازگاری می‌مانند و کلید `admin` می‌سازند.

## ۶. امنیت
- **ارتقای دسترسی:** کلید غیر admin نمی‌تواند کلید بسازد، session بگیرد، تنظیمات را تغییر دهد یا PostUp بنویسد.
- **نشت:** پیشوند `wgd_` تشخیص خودکار در ریپوها را ممکن می‌کند؛ هش در دیتابیس یعنی بکاپ افشاشده کلید معتبر نمی‌دهد.
- **Timing:** جستجو با هش در dict؛ مقایسه‌ی رشته‌ی خام انجام نمی‌شود.
- **لاگ:** فقط `KeyPrefix` و `Label` لاگ می‌شوند ([FEAT-09](FEAT-09-Audit-Log.md)).
- محدودیت نرخ اختیاری برای هر کلید (مثلاً ۵۰ درخواست/ثانیه) تا یک کلید دزدیده‌شده پنل را از کار نیندازد.

## ۷. سازگاری و مهاجرت
- کلیدهای فعلی بعد از مهاجرت `admin` و `*` هستند ← هیچ قطعی. UI کنار آن‌ها برچسب «Legacy — full access» نشان می‌دهد.
- برنامه‌ی پیشنهادی: ساخت کلید جدید محدود برای OxBot و برای ربات دوستتان، جایگزینی در ربات‌ها، سپس revoke کلیدهای legacy. (با توجه به [سند ۰۱](../01-URGENT-Leaked-Secrets.md)، کلید فعلی OxBot در هر صورت باید عوض شود.)

## ۸. تغییرات ربات OxBot
فقط مقدار جدید در `marzban_panel.password_panel`. اگر IP سرور ربات ثابت است، آن را در `allowed_ips` بگذارید.

پیکربندی پیشنهادی:

| کلید | Scopes | Configurations | AllowedIPs |
|------|--------|----------------|------------|
| OxBot | `config:read, peers:read, peers:config, peers:write, webhooks:manage` | اینترفیس‌های شما | IP سرور ربات |
| ربات دوست | همان | **فقط** اینترفیس(های) او | IP سرور ربات او |
| مانیتورینگ | `metrics:read` | `*` | IP سرور مانیتورینگ |
| Cross-Server پنل (اگر استفاده می‌کنید) | `admin` | `*` | IP خودتان |

## ۹. تغییرات UI
صفحه‌ی API Keys ([dashboardAPIKeys.vue](../../src/static/app/src/components/settingsComponent/dashboardAPIKeys.vue)، [newDashboardAPIKey.vue](../../src/static/app/src/components/settingsComponent/dashboardAPIKeysComponents/newDashboardAPIKey.vue)):
- فرم ساخت: برچسب، قالب آماده («ربات فروش»، «فقط خواندن»، «ادمین کامل»)، چک‌باکس scopeها، انتخاب چندتایی اینترفیس، IPهای مجاز، تاریخ انقضا.
- نمایش کلید کامل فقط یک بار با دکمه‌ی کپی و هشدار.
- لیست: برچسب، پیشوند، scopeها، اینترفیس‌ها، آخرین استفاده (زمان و IP)، انقضا، دکمه‌ی revoke.

## ۱۰. تست
- ماتریس: هر scope × هر مسیر v1/v2 ← 200 یا 403 مورد انتظار.
- جداسازی: کلید `["wg1"]` روی `addPeers/wg0` ← 403؛ لیست کانفیگ‌ها فقط `wg1`.
- انقضا: کلید با `expires_at` یک دقیقه بعد ← بعد از یک دقیقه 401 **بدون** ری‌استارت.
- IP: درخواست از IP خارج از allowlist ← 401.
- مهاجرت: بعد از اجرا، هیچ مقدار متن ساده‌ای در جدول نماند و کلید قدیمی همچنان کار کند.
- `authenticate` با کلید غیر admin ← session ساخته نشود.

## ۱۱. استقرار و Rollback
قبل از استقرار Global Backup بگیرید (مهاجرت مقدار `Key` را بازنویسی می‌کند و برگشت‌ناپذیر است). Rollback کد: کلیدهای مهاجرت‌شده با نسخه‌ی قدیمی کار **نمی‌کنند** (چون متن ساده حذف شده)؛ پس rollback = ریستور بکاپ جدول یا ساخت کلید جدید با UI قدیمی.

## ۱۲. فایل‌های درگیر
[DashboardConfig.py](../../src/modules/DashboardConfig.py) (جدول و مهاجرت)، `src/modules/APIKeyStore.py` و `src/modules/APIScopes.py` (جدید)، [dashboard.py](../../src/dashboard.py) (`auth_req`، endpointهای کلید)، کامپوننت‌های API Key در فرانت، `tests/test_api_scopes.py`.
