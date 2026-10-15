# FEAT-03 — عملیات اتمیک و Idempotent (خرید، تمدید، گروهی)

| | |
|---|---|
| اولویت | ⭐⭐⭐ ضروری |
| حجم کار | M (۲ روز پنل، نیم روز ربات) |
| حل می‌کند | [BOT-05](../06-OxBot-Integration.md#bot-05)، [BOT-09](../06-OxBot-Integration.md#bot-09)، [BOT-10](../06-OxBot-Integration.md#bot-10)، [BOT-11](../06-OxBot-Integration.md#bot-11)، [BUG-04](../03-Backend-Bugs-and-Logic.md#bug-04)، [BUG-18](../03-Backend-Bugs-and-Logic.md#bug-18)، [PERF-06](../07-Performance-and-Stability.md#perf-06) |
| وابستگی | [FEAT-01](FEAT-01-Lightweight-Bot-API.md)، [FEAT-02](FEAT-02-Peer-Limits.md)، [FEAT-13](FEAT-13-Panel-IP-Allocation.md) |

## ۱. مسئله
- **خرید** الان ۴ درخواست جدا است (`addPeers` ← `savePeerScheduleJob` ×۲ ← `downloadPeer` با تا ۲۰ تلاش). اگر وسطش قطع شود، سرویس بدون محدودیت تحویل داده می‌شود.
- اگر `addPeers` timeout بخورد و ربات دوباره تلاش کند، ممکن است **دو peer** ساخته شود (ربات برای جلوگیری، با نام جستجو می‌کند که خودش شکننده است).
- **تمدید** حدود ۱۰ درخواست و ۳-۴ بار `wg-quick save` است. ریست مصرف در v1 یک restrict + allow انجام می‌دهد که کاربر را لحظه‌ای قطع می‌کند ([dashboard.py:1237-1239](../../src/dashboard.py#L1237-L1239)).
- **حذف** برای peer ناموجود «موفق» برمی‌گرداند (BUG-04) و ربات قبل از حذف، دسترسی را باز می‌کند (BOT-09).
- `updatePeerSettings` همه‌ی فیلدها را اجباری می‌خواهد و اگر یکی نباشد 500 می‌دهد.

## ۲. اهداف
1. هر عملیات کسب‌وکاری (خرید، تمدید، حجم/زمان اضافه، حذف) = **یک درخواست** و **یک تراکنش**.
2. **Idempotency:** تکرار یک درخواست با همان `Idempotency-Key` نتیجه‌ی قبلی را برمی‌گرداند و کار را دوباره انجام نمی‌دهد ← retry امن.
3. عملیات گروهی با نتیجه‌ی جداگانه برای هر id و فقط یک `wg-quick save`.
4. به‌روزرسانی جزئی peer (فقط فیلدهای ارسال‌شده).

## ۳. غیر اهداف
- تراکنش توزیع‌شده بین پنل و دیتابیس ربات (ربات خودش باید نتیجه را ذخیره کند؛ idempotency همین را امن می‌کند).

## ۴. طراحی

### ۴.۱ جدول‌ها (دیتابیس `wgdashboard`)
**`IdempotencyKeys`**

| ستون | نوع | توضیح |
|------|------|--------|
| `Key` | String(128), PK | مقدار هدر `Idempotency-Key` |
| `Actor` | String(64), PK | شناسه‌ی API Key (FEAT-05) یا `admin` — هر کلید فضای نام خودش را دارد |
| `Endpoint` | String(128) | مثل `POST /api/v2/peers` |
| `RequestHash` | String(64) | sha256 بدنه‌ی JSON نرمال‌شده (کلیدهای مرتب) |
| `State` | String(16) | `in_progress` · `done` |
| `StatusCode` | Integer | |
| `ResponseJSON` | Text | |
| `ResourceRef` | String(255), NULL | مثلاً `wg0:<public_key>` برای بازیابی بعد از کرش |
| `CreatedAt` / `ExpiresAt` | DateTime (UTC) | پیش‌فرض نگهداری ۷ روز |

پاک‌سازی ردیف‌های منقضی در همان هرس ۶ ساعته‌ی [dashboard.py:147-223](../../src/dashboard.py#L147-L223).

### ۴.۲ میان‌افزار Idempotency
```python
def idempotent(endpoint_name):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            key = request.headers.get("Idempotency-Key")
            if not key:
                return fn(*a, **kw)                      # اختیاری؛ ربات همیشه می‌فرستد
            if len(key) > 128: return error("VALIDATION_ERROR", "Idempotency-Key too long")
            h = sha256(canonical_json(request.get_json(silent=True)))
            with idem_lock(key, g.actor):                 # قفل درون‌پروسه‌ای برای همان کلید
                row = store.get(key, g.actor)
                if row:
                    if row.RequestHash != h: return error("IDEMPOTENCY_MISMATCH", status=422)
                    if row.State == "in_progress":
                        if not row.stale(): return error("IN_PROGRESS", status=409, retry_after=2)
                        return recover(row)               # بخش ۴.۵
                    return replay(row)                    # هدر Idempotent-Replayed: true
                store.begin(key, g.actor, endpoint_name, h)
            resp = fn(*a, **kw)
            store.finish(key, g.actor, resp)
            return resp
        return wrapper
    return deco
```

### ۴.۳ ساخت peer — مراحل و مرز تراکنش
```
1. اعتبارسنجی بدنه + scope peers:write روی configuration
2. گرفتن config.lock (حداکثر ۱۰ ثانیه، وگرنه BUSY)
3. کلیدها: private/public داده نشده ← تولید؛ PSK: "auto" ← تولید، "" ← بدون PSK
4. IP: allowed_ip داده شده ← نرمال‌سازی و بررسی آزاد بودن با ipaddress.overlaps
        داده نشده ← تخصیص (FEAT-13)
5. نام: اگر unique_name=true و نام تکراری ← CONFLICT
6. kernel:   wg set <conf> peer <pk> allowed-ips <ip> preshared-key <tmp>
7. DB (یک تراکنش):  INSERT peer  +  INSERT PeerLimits  +  UPDATE IdempotencyKeys.ResourceRef
      ↳ خطا ← wg set <conf> peer <pk> remove  (rollback کرنل، الگوی فعلی addPeers)
8. wg-quick save  (یک بار)
9. ساخت پاسخ: peer (قالب FEAT-01) + limits + فایل کانفیگ
10. رها کردن قفل، ارسال webhook peer.created (FEAT-04)
```

### ۴.۴ تمدید — ریست بدون قطع اتصال
ریست مصرف در طراحی فعلی با «offset» کار می‌کند: `cumu = -شمارنده‌ی فعلی kernel` تا مصرف نهایی صفر شود ([Peer.py:545-587](../../src/modules/Peer.py#L545-L587)). پس **نیازی به restrict + allow برای صفر کردن شمارنده‌ی kernel نیست**؛ v2 این کار را حذف می‌کند و کاربر هنگام تمدید قطع نمی‌شود.
```
1. قفل
2. اگر reset_usage: snapshot += مصرف فعلی ؛ cumu = -total   (همان منطق فعلی، بدون restrict/allow)
3. محدودیت‌ها: replace یا add (FEAT-02)
4. اگر peer محدود است و resume=true و هیچ محدودیتی رد نشده ← allow (یک wg set)
5. یک تراکنش DB + یک wg-quick save (فقط اگر کرنل تغییر کرد)
```

### ۴.۵ بازیابی بعد از کرش
اگر پروسه بین مرحله‌ی ۷ و ثبت پاسخ از کار بیفتد، ردیف `in_progress` می‌ماند. بعد از ۵ دقیقه «کهنه» حساب می‌شود و درخواست تکراری:
- اگر `ResourceRef` پر است و peer وجود دارد ← پاسخ را از روی وضعیت فعلی peer **بازسازی** و ذخیره می‌کند (کار تکراری انجام نمی‌شود)؛
- اگر خالی است (کرش قبل از commit) ← kernel را برای کلید احتمالی پاک می‌کند و عملیات را از نو اجرا می‌کند.

### ۴.۶ جدول خطا

| مرحله‌ای که شکست می‌خورد | نتیجه برای ربات | وضعیت سیستم |
|--------------------------|-----------------|-------------|
| ۱-۵ | 400/403/404/409 با `error_code` | بدون تغییر |
| ۶ (`wg set`) | 500 `INTERNAL` | بدون تغییر |
| ۷ (DB) | 500 `INTERNAL` | کرنل rollback شده؛ بدون تغییر |
| ۸ (`wg-quick save`) | 200 با `warnings: ["config_not_saved"]` | DB و کرنل درست؛ reconcile بعدی فایل را درست می‌کند |
| کرش بعد از ۷ | retry ← پاسخ بازسازی‌شده | سازگار |

## ۵. API

### ۵.۱ ساخت
```http
POST /api/v2/peers                          (peers:write, peers:config برای فایل)
Idempotency-Key: buy-00023119
Content-Type: application/json

{ "configuration": "wg0",
  "name": "1721341_egproxy_100",
  "unique_name": true,
  "allowed_ip": null,                 // null = تخصیص توسط پنل (FEAT-13)
  "private_key": null,                // null = تولید توسط پنل
  "preshared_key": "auto",
  "dns": null, "mtu": null, "keepalive": null, "endpoint_allowed_ip": null,   // null = پیش‌فرض/Override پنل
  "notes": "invoice 00023119",
  "limits": { "data_limit_bytes": 10737418240, "expire_at": "2026-11-13T20:30:00Z",
              "on_hold_seconds": null, "plan_name": "گیمینگ کالاف ۱۰ گیگ" },
  "include_config": true }
```
```json
{ "status": true, "data": {
    "peer":   { "id": "…", "name": "…", "allowed_ip": "10.0.3.17/32", "restricted": false, "usage": { "total_bytes": 0 } },
    "limits": { "state": "active", "data_limit_bytes": 10737418240, "expire_at": "2026-11-13T20:30:00Z" },
    "config": { "fileName": "1721341_egproxy_100", "file": "[Interface]\nPrivateKey = …" },
    "warnings": [] } }
```

### ۵.۲ تمدید
```http
POST /api/v2/peer/renew                   (peers:write)
Idempotency-Key: renew-00023119-1760464200
{ "configuration": "wg0", "id": "<pk>",
  "reset_usage": true,
  "limits": { "data_limit_bytes": 21474836480, "expire_at": "2026-12-13T20:30:00Z" },
  "resume": true }
```
به‌جای `limits` می‌توان `add` فرستاد (`{ "add_data_bytes": …, "add_seconds": …, "time_base": "auto" }`) — همان معنای `POST /api/v2/peer/limits/add` در [FEAT-02](FEAT-02-Peer-Limits.md) (بخش ۵.۳).

### ۵.۳ به‌روزرسانی جزئی
```http
POST /api/v2/peer/update                  (peers:write)
{ "configuration": "wg0", "id": "<pk>", "name": "new_name", "notes": "…" }
```
فقط فیلدهای ارسال‌شده تغییر می‌کنند. DNS با `ValidateDNSAddress(...)[0]` بررسی می‌شود (اصلاح [BUG-10](../03-Backend-Bugs-and-Logic.md#bug-10)).

### ۵.۴ گروهی
```http
POST /api/v2/peers/bulk                   (peers:write)
{ "configuration": "wg0", "action": "restrict", "ids": ["…", "…"], "reason": "Disabled by admin" }
```
`action` ∈ `restrict` · `allow` · `delete` · `reset_usage`. حداکثر ۵۰۰ id. برای `allow` اگر محدودیت FEAT-02 هنوز رد شده ← نتیجه‌ی آن id `LIMIT_EXCEEDED` (مگر `force: true`).
```json
{ "status": true, "data": {
  "results": [ { "id": "…", "ok": true }, { "id": "…", "ok": false, "error_code": "NOT_FOUND" } ],
  "summary": { "ok": 1, "failed": 1 } } }
```
`status` کل پاسخ `true` است حتی اگر بعضی idها شکست بخورند؛ ربات باید `results` را بخواند. حذف در v2 قبل از حذف، دسترسی را باز **نمی‌کند** و peer ناموجود را `NOT_FOUND` گزارش می‌دهد.

## ۶. امنیت
- `Idempotency-Key` در فضای نام هر API Key جداست؛ یک کلید نمی‌تواند پاسخ ذخیره‌شده‌ی کلید دیگر (که شامل فایل کانفیگ و کلید خصوصی است) را بخواند.
- `ResponseJSON` شامل کلید خصوصی کاربر است؛ ۷ روز نگهداری و سپس حذف. اگر نمی‌خواهید ذخیره شود، گزینه‌ی `include_config: false` و گرفتن فایل جداگانه با `GET /peer/config`.
- سقف اندازه‌ی بدنه (مثلاً ۶۴ کیلوبایت) و تعداد id در bulk.

## ۷. سازگاری
endpointهای v1 بدون تغییر می‌مانند. ربات قدیمی بدون مشکل کار می‌کند.

## ۸. تغییرات ربات OxBot
```php
// خرید — جایگزین addpear + setjob×2 + downloadconfig
function wg_create_service($namepanel, $username, $invoiceId, $volumeBytes, $expireTs, $planName) {
    $p = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $body = [
        'configuration' => $p['inboundid'], 'name' => $username, 'unique_name' => true,
        'preshared_key' => 'auto', 'include_config' => true,
        'limits' => [
            'data_limit_bytes' => $volumeBytes ?: null,
            'expire_at' => $expireTs ? gmdate('Y-m-d\TH:i:s\Z', $expireTs) : null,
            'plan_name' => $planName,
        ],
    ];
    for ($try = 1; $try <= 3; $try++) {
        $r = wg_api_post($namepanel, '/api/v2/peers', $body, ['Idempotency-Key: buy-' . $invoiceId], 30000);
        if (!empty($r['json']['status'])) {
            $d = $r['json']['data'];
            // فقط چیزهای لازم ذخیره شود (BOT-13)
            update("invoice", "user_info", json_encode([
                'public_key' => $d['peer']['id'], 'allowed_ip' => $d['peer']['allowed_ip'], 'name' => $d['peer']['name'],
            ]), "username", $username);
            return ['status' => true, 'config' => $d['config']['file']];
        }
        $code = $r['json']['error_code'] ?? null;
        if (!in_array($code, ['BUSY', 'IN_PROGRESS', 'MAINTENANCE', null], true)) break; // خطای قطعی
        sleep($try);                                   // backoff: 1, 2, 3 ثانیه
    }
    return ['status' => false, 'msg' => $r['json']['message'] ?? 'panel unavailable'];
}
```
- `addpear` و منطق انتخاب IP و `GET_LOCK` حذف می‌شوند ([FEAT-13](FEAT-13-Panel-IP-Allocation.md)).
- تمدید: یک فراخوانی `peer/renew` با کلید `renew-<invoice>-<payment_id>`.
- حذف: `peers/bulk {action: delete}` و بررسی `results[0].ok`.

## ۹. تغییرات UI
UI فعلی بدون تغییر کار می‌کند. (اختیاری: دکمه‌های گروهی Select Peers می‌توانند از `peers/bulk` استفاده کنند تا یک `wg-quick save` انجام شود.)

## ۱۰. تست
- **Idempotency:** دو درخواست یکسان پشت‌سرهم ← یک peer، پاسخ دوم با `Idempotent-Replayed: true`؛ همان کلید با بدنه‌ی متفاوت ← 422؛ دو درخواست هم‌زمان ← یکی 200، دیگری 409 `IN_PROGRESS` یا replay.
- **Rollback:** شبیه‌سازی خطای DB ← `wg set ... remove` صدا زده شود و peer در کرنل نماند.
- **کرش:** ردیف `in_progress` کهنه با `ResourceRef` ← پاسخ بازسازی‌شده بدون ساخت مجدد.
- **تمدید:** بعد از `renew`، کاربر آنلاین قطع نشود (handshake ادامه دارد) و مصرف صفر باشد.
- **Bulk:** ترکیب idهای موجود/ناموجود؛ فقط یک `wg-quick save` (با شمارش فراخوانی‌های fake).

## ۱۱. استقرار و Rollback
endpointهای جدید هستند و چیزی را تغییر نمی‌دهند؛ rollback = برگرداندن ربات به توابع قبلی.

## ۱۲. فایل‌های درگیر
`src/api_v2.py`، `src/modules/Idempotency.py` (جدید)، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (هسته‌ی مشترک create/delete/bulk)، [Peer.py](../../src/modules/Peer.py) (ریست بدون restrict/allow)، `tests/test_v2_create_renew.py`.
