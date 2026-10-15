# FEAT-01 — API سبک برای ربات

| | |
|---|---|
| اولویت | ⭐⭐⭐ ضروری |
| حجم کار | S (حدود ۱ روز پنل + نیم روز ربات) |
| حل می‌کند | [BOT-01](../06-OxBot-Integration.md#bot-01)، [BOT-02](../06-OxBot-Integration.md#bot-02)، [BOT-08](../06-OxBot-Integration.md#bot-08)، [PERF-02](../07-Performance-and-Stability.md#perf-02)، بخشی از [SEC-05](../02-Security-Vulnerabilities.md#sec-05) |
| وابستگی | قراردادهای مشترک در [README](README.md) |

## ۱. مسئله
ربات برای دیدن وضعیت **یک** کاربر، `GET /api/getWireguardConfigurationInfo` را صدا می‌زند که:
- JSON همه‌ی peerها را برمی‌گرداند (با ۱۶۰۰ کاربر بیش از ۱.۵ مگابایت)؛ peerهای محدود هم دو بار در آن هستند ([BUG-01](../03-Backend-Bugs-and-Logic.md#bug-01))،
- برای ساخت آن، پنل برای هر peer یک شیء Python و فیلتر job/share link می‌سازد،
- کلید خصوصی اینترفیس را هم می‌دهد ([SEC-05](../02-Security-Vulnerabilities.md#sec-05)).

ربات بعد از دریافت، با `name` (نه public key) جستجو می‌کند. در کرون‌ها این کار برای هر کاربر تکرار می‌شود ← بار از مرتبه‌ی N².

## ۲. اهداف
1. گرفتن وضعیت یک peer با یک کوئری ایندکس‌دار (کمتر از ۵۰ میلی‌ثانیه با ۲۰۰۰ peer).
2. گرفتن مصرف **همه‌ی** peerهای یک اینترفیس در یک پاسخ فشرده (کمتر از ۲۵۰ کیلوبایت برای ۲۰۰۰ peer، بدون ساخت شیء Peer)، با پشتیبانی از `ETag`/`304` تا اگر چیزی عوض نشده، هیچ داده‌ای منتقل نشود.
3. فیلد صریح `restricted` و `restricted_reason` تا ابهام BOT-01 از بین برود.
4. مصرف همیشه به‌صورت «نهایی» (`cumu + total`) و به بایت.
5. زمان آخرین handshake به‌صورت timestamp مطلق (الان رشته‌ی «۰:۰۱:۲۳ پیش» ذخیره می‌شود).

## ۳. غیر اهداف
- تغییر endpointهای فعلی v1 (برای سازگاری می‌مانند).
- مدیریت محدودیت‌ها (FEAT-02) و ساخت/حذف (FEAT-03).

## ۴. طراحی

### ۴.۱ تغییر دیتابیس
روی جداول `<conf>` و `<conf>_restrict_access` یک ستون اضافه می‌شود (با الگوی `EnsureDatabaseIntegrity`):

| ستون | نوع | توضیح |
|------|------|--------|
| `latest_handshake_ts` | Integer, nullable | epoch ثانیه از `wg show dump` (ستون پنجم)؛ `0`/null یعنی هرگز |

و یک ایندکس اختیاری روی `name` برای جستجوی نام:
```sql
CREATE INDEX IF NOT EXISTS "ix_<conf>_name" ON "<conf>"(name);
CREATE INDEX IF NOT EXISTS "ix_<conf>_restrict_access_name" ON "<conf>_restrict_access"(name);
```

در `refreshPeersRuntimeStats` ([WireguardConfiguration.py:1320-1346](../../src/modules/WireguardConfiguration.py#L1320-L1346)) مقدار `last_hs_ts` در کنار رشته‌ی فعلی در `hs_updates` نوشته می‌شود.

### ۴.۲ شمارنده‌ی نسخه (Revision)
هر شیء کانفیگ یک عدد `revision` در حافظه دارد که با هر تغییر بالا می‌رود:
- در `refreshPeersRuntimeStats` اگر `any([hs_updates, ep_updates, cumu_updates, total_updates])` بود،
- در پایان موفق `addPeers`، `deletePeers`، `restrictPeers`، `allowAccessPeers`، `resetDataUsage`، `updatePeer`.

`ETag = W/"<conf>-<boot_id>-<revision>"` که `boot_id` یک مقدار تصادفی در هر شروع پروسه است (بعد از ری‌استارت، ETagهای قدیمی نامعتبر می‌شوند).

### ۴.۳ ماژول جدید
`src/modules/PeerQueries.py` — فقط کوئری‌های خواندنی، بدون ساخت شیء `Peer`:
```python
GB = 1024 ** 3

def _bytes(gb):
    return int(round((gb or 0.0) * GB))

def peer_row_to_dict(row, restricted: bool) -> dict:
    rx = _bytes((row["cumu_receive"] or 0) + (row["total_receive"] or 0))
    tx = _bytes((row["cumu_sent"] or 0) + (row["total_sent"] or 0))
    return {
        "id": row["id"], "name": row["name"], "allowed_ip": row["allowed_ip"],
        "restricted": restricted,
        "restricted_reason": row.get("restricted_reason") if restricted else None,
        "status": "stopped" if restricted else row["status"],
        "latest_handshake_at": iso_utc(row.get("latest_handshake_ts")),
        "endpoint": None if row["endpoint"] in (None, "N/A", "(none)") else row["endpoint"],
        "usage": {"rx_bytes": rx, "tx_bytes": tx, "total_bytes": rx + tx},
    }

def find_peer(conf, *, peer_id=None, name=None) -> tuple[dict | None, str | None]:
    """returns (peer, error_code)"""
    for table, restricted in ((conf.peersRestrictedTable, True), (conf.peersTable, False)):
        ...  # SELECT ... WHERE id = :id  (یا name = :name)
    # برای name: اگر مجموع نتایج > 1 ← (None, "AMBIGUOUS")
```
نکته: جدول محدودشده‌ها **اول** جستجو می‌شود؛ اگر یک id به هر دلیلی در هر دو جدول باشد، وضعیت امن‌تر (محدود) برگردانده می‌شود.

### ۴.۴ Blueprint
`src/api_v2.py` با `createApiV2Blueprint(WireguardConfigurations, DashboardConfig, AllPeerJobs)` و ثبت در [dashboard.py](../../src/dashboard.py) کنار `createClientBlueprint`.

## ۵. API

### ۵.۱ خلاصه‌ی اینترفیس
```http
GET /api/v2/configuration?configuration=wg0          (scope: config:read)
```
```json
{
  "status": true,
  "data": {
    "name": "wg0", "protocol": "wg", "status": "up",
    "address": "10.0.0.1/24", "listen_port": 51820, "public_key": "…",
    "peers": { "total": 1610, "active": 1420, "restricted": 190, "online": 812 },
    "usage": { "rx_bytes": 0, "tx_bytes": 0, "total_bytes": 0 },
    "override_peer_settings": { "DNS": "1.1.1.1", "MTU": "1360", "PeerRemoteEndpoint": "…", "ListenPort": "" },
    "revision": 48213
  }
}
```
بدون `PrivateKey`، `PreUp/PostUp`.

### ۵.۲ یک peer
```http
GET /api/v2/peer?configuration=wg0&id=<public_key_urlencoded>    (scope: peers:read)
GET /api/v2/peer?configuration=wg0&name=1721341_egproxy_100
```
```json
{
  "status": true,
  "data": {
    "configuration": "wg0",
    "id": "RvKt…31k=",
    "name": "1721341_egproxy_100",
    "allowed_ip": "10.0.0.178/32",
    "restricted": true,
    "restricted_reason": "Volume Limit Reached",
    "status": "stopped",
    "latest_handshake_at": "2026-10-14T17:59:12Z",
    "endpoint": null,
    "usage": { "rx_bytes": 4294967296, "tx_bytes": 17179869184, "total_bytes": 21474836480 },
    "limits": null,
    "jobs": [ { "JobID": "…", "Field": "date", "Operator": "lgt", "Value": "2026-11-01 00:00:00", "Action": "restrict" } ]
  }
}
```
- `limits` بعد از FEAT-02 پر می‌شود؛ `jobs` برای دوره‌ی گذار.
- خطاها: `NOT_FOUND` (404)، `AMBIGUOUS` (409، فقط جستجو با نام)، `VALIDATION_ERROR` اگر هر دو یا هیچ‌کدام از `id`/`name` داده شوند.

### ۵.۳ مصرف همه (فشرده)
```http
GET /api/v2/peers/usage?configuration=wg0      (scope: peers:read)
If-None-Match: W/"wg0-7f3a-48213"
```
```json
{
  "status": true,
  "data": {
    "configuration": "wg0",
    "generated_at": "2026-10-14T18:30:00Z",
    "revision": 48213,
    "fields": ["id", "name", "restricted", "reason", "rx_bytes", "tx_bytes", "total_bytes", "latest_handshake_ts"],
    "rows": [
      ["RvKt…31k=", "1721341_egproxy_100", 1, "Volume Limit Reached", 4294967296, 17179869184, 21474836480, 1792000000],
      ["Zi0p…FT0=", "2140061_egproxy_101", 0, null, 1048576, 5242880, 6291456, 1792001234]
    ]
  }
}
```
- پاسخ با هدر `ETag`. اگر `If-None-Match` برابر بود ← `304 Not Modified` بدون بدنه.
- پیاده‌سازی: دو `SELECT` ستون‌محدود روی دو جدول، بدون ساخت شیء. تخمین: ۲۰۰۰ ردیف ≈ ۲۰۰ کیلوبایت، با gzip در nginx (`gzip_types application/json;`) حدود ۵۰ کیلوبایت.

### ۵.۴ فایل کانفیگ
```http
GET /api/v2/peer/config?configuration=wg0&id=<pk>&format=json|conf    (scope: peers:config)
```
- `format=json` ← `{ "fileName": "…", "file": "[Interface]…" }`؛ `format=conf` ← خود فایل با `Content-Disposition: attachment`.
- بر پایه‌ی `Peer.downloadPeer` ولی **بدون** auto-heal ([BUG-03](../03-Backend-Bugs-and-Logic.md#bug-03)). اگر کلید خصوصی ذخیره نشده باشد ← `409 CONFLICT` با پیام صریح.
- scope جداگانه (`peers:config`) چون پاسخ شامل کلید خصوصی کاربر است.

## ۶. امنیت
- هیچ پاسخی `PrivateKey` اینترفیس، `PreUp/PostUp` یا `private_key`/`preshared_key` peer را ندارد، به‌جز `peer/config` با scope مخصوص.
- محدودیت اینترفیس در scope کلید ([FEAT-05](FEAT-05-Scoped-API-Keys.md)) اعمال می‌شود: کلید ربات دوستتان فقط اینترفیس خودش را می‌بیند.
- `id` فقط در query؛ مقدار با `CheckPeerKey` اعتبارسنجی و `' '`→`'+'` نرمال می‌شود (مثل کد فعلی).

## ۷. سازگاری و مهاجرت
- v1 دست نمی‌خورد. ستون جدید nullable است؛ کد قدیمی آن را نادیده می‌گیرد.
- ربات با تشخیص خودکار کار می‌کند: اگر `/api/v2/configuration` پاسخ 404 داد، به v1 برمی‌گردد.

## ۸. تغییرات ربات OxBot
فایل `WGDashboard.php` ربات:
```php
function wg_api($namepanel, $method, $path, $query = [], $body = null, $timeout = 15000) {
    $p = select("marzban_panel", "*", "name_panel", $namepanel, "select");
    $q = array_merge(['configuration' => $p['inboundid']], $query);
    $url = rtrim($p['url_panel'], '/') . $path . '?' . http_build_query($q);
    $req = new CurlRequest($url);
    $req->setHeaders(['Accept: application/json', 'Content-Type: application/json',
                      'wg-dashboard-apikey: ' . $p['password_panel']]);
    $req->setTimeout($timeout);
    $res = $method === 'GET' ? $req->get() : $req->post(json_encode($body));
    $json = json_decode($res['body'] ?? '', true);
    return ['http' => $res['status'] ?? 0, 'json' => $json, 'error' => $res['error'] ?? null];
}

/** وضعیت یک کاربر با public key (fallback به نام) */
function wg_get_peer($namepanel, $username) {
    $invoice = select("invoice", "user_info", "username", $username, "select");
    $info = $invoice ? json_decode($invoice['user_info'], true) : [];
    $pk = $info['public_key'] ?? $info['id'] ?? null;
    $r = $pk ? wg_api($namepanel, 'GET', '/api/v2/peer', ['id' => $pk])
             : wg_api($namepanel, 'GET', '/api/v2/peer', ['name' => $username]);
    if ($r['http'] === 404 && !$pk) return ['status' => false, 'msg' => 'User not found'];
    if (empty($r['json']['status'])) return ['status' => false, 'msg' => $r['json']['message'] ?? 'API error'];
    return wg_v2_to_legacy($r['json']['data']);
}

/** تبدیل به آرایه‌ای که DataUser فعلی انتظار دارد */
function wg_v2_to_legacy(array $p) {
    $gb = 1024 ** 3;
    return [
        'id' => $p['id'], 'name' => $p['name'], 'allowed_ip' => $p['allowed_ip'],
        'total_data' => $p['usage']['total_bytes'] / $gb, 'cumu_data' => 0,
        'total_receive' => $p['usage']['rx_bytes'] / $gb, 'total_sent' => $p['usage']['tx_bytes'] / $gb,
        'jobs' => $p['jobs'] ?? [], 'limits' => $p['limits'] ?? null,
        'restricted_reason' => $p['restricted_reason'],
        'configuration' => ['Status' => !$p['restricted']],
    ];
}

/** کرون: یک بار در هر دور برای هر پنل */
function wg_usage_snapshot($namepanel) {
    static $cache = [];
    if (isset($cache[$namepanel])) return $cache[$namepanel];
    $r = wg_api($namepanel, 'GET', '/api/v2/peers/usage', [], null, 30000);
    $map = [];
    foreach ($r['json']['data']['rows'] ?? [] as $row) {
        $map[$row[0]] = array_combine($r['json']['data']['fields'], $row);
    }
    return $cache[$namepanel] = $map;     // کلید: public key
}
```
- در `DataUser` (`panels.php`)، `get_userwg` با `wg_get_peer` جایگزین می‌شود.
- در کرون‌های هشدار حجم/زمان، به‌جای `DataUser` برای هر کاربر، از `wg_usage_snapshot` با public key ذخیره‌شده‌ی کاربر استفاده شود.

## ۹. تغییرات UI
نیازی نیست. (اختیاری: فرانت هم می‌تواند برای polling صفحه‌ی peerها از `peers/usage` با ETag استفاده کند و بار را کم کند.)

## ۱۰. تست
- **واحد:** جستجو با id در جدول فعال/محدود؛ id با فاصله به‌جای `+`؛ نام تکراری ← `AMBIGUOUS`؛ peer ناموجود ← 404؛ تبدیل GB→بایت با مقادیر منفی `cumu` (بعد از ریست) که نتیجه‌ی نهایی صفر یا مثبت می‌دهد.
- **ETag:** دو درخواست پشت‌سرهم بدون تغییر ← دومی 304؛ بعد از `restrictPeers` ← 200 با revision جدید.
- **کارایی:** ۲۰۰۰ ردیف مصنوعی: `peer` < ۵۰ms، `peers/usage` < ۲۰۰ms و < ۲۵۰KB.
- **امنیت:** پاسخ‌ها شامل `PrivateKey`/`private_key` نباشند (تست با جستجوی رشته).

## ۱۱. استقرار و Rollback
1. استقرار پنل (v1 بی‌تغییر است، پس ریسک برای ربات فعلی صفر است).
2. آپدیت ربات با fallback.
3. Rollback: برگرداندن نسخه‌ی ربات؛ پنل نیازی به برگشت ندارد.

## ۱۲. فایل‌های درگیر
`src/api_v2.py` (جدید)، `src/modules/PeerQueries.py` (جدید)، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (ستون، revision)، [DashboardConfig.py](../../src/modules/DashboardConfig.py) (`expected_columns`)، [dashboard.py](../../src/dashboard.py) (ثبت blueprint)، `tests/test_api_v2_peer.py` (جدید).
