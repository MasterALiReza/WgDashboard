# FEAT-14 — چرخش کلید اینترفیس و صدور مجدد کانفیگ‌ها

| | |
|---|---|
| اولویت | ⭐⭐ (به‌خاطر افشای کلید سرور در [سند ۰۱](../01-URGENT-Leaked-Secrets.md)) |
| حجم کار | M (۲ روز پنل، ۱ روز ربات) |
| حل می‌کند | کلید خصوصی سرور و کلیدهای خصوصی همه‌ی کاربران افشا شده‌اند |
| وابستگی | [FEAT-03](FEAT-03-Atomic-Idempotent-Operations.md) (ساخت اتمیک)، [FEAT-04](FEAT-04-Signed-Event-Webhooks.md) (اطلاع به ربات)، [FEAT-02](FEAT-02-Peer-Limits.md) (انتقال محدودیت‌ها) |

## ۱. مسئله
- با کلید خصوصی اینترفیس، مهاجمی که در مسیر شبکه است می‌تواند خودش را سرور جا بزند.
- با کلید خصوصی هر کاربر، هر کسی می‌تواند با اکانت او وصل شود (حجم او را مصرف کند).

عوض کردن کلید اینترفیس یعنی **همه‌ی** کاربران کانفیگ جدید لازم دارند. انجام دستی برای ۱۶۰۰ کاربر عملی نیست.

## ۲. اهداف
1. دو روش: **Hard Rotate** (سریع، با قطعی) و **Parallel Migration** (بدون قطعی، توصیه‌شده).
2. تولید کلید جدید برای اینترفیس **و** همه‌ی peerها.
3. انتقال نام، محدودیت‌ها، مصرف و یادداشت‌ها.
4. اطلاع خودکار به ربات تا کانفیگ جدید را برای هر کاربر بفرستد.
5. داشبورد پیشرفت مهاجرت (چند درصد کاربران با کانفیگ جدید وصل شده‌اند).

## ۳. طراحی

### ۳.۱ روش A — Hard Rotate (برای اینترفیس‌های کوچک یا اضطراری)
1. بکاپ pre-rotate.
2. کلید جدید اینترفیس + کلید جدید برای همه‌ی peerها (IPها ثابت).
3. `wg set` گروهی ← همه‌ی کاربران فعلی فوراً قطع می‌شوند.
4. رویداد `peer.rekeyed` برای هر peer ← ربات کانفیگ جدید را برای کاربر می‌فرستد.

### ۳.۲ روش B — Parallel Migration (توصیه‌شده)
```
wg0 (قدیمی، کلید افشاشده)  ─────────────►  تا تاریخ قطع فعال می‌ماند
        │  کپی peerها با کلید جدید، IP جدید
        ▼
wg1 (جدید، پورت جدید، زیرشبکه‌ی جدید، کلید جدید)  ◄── فروش‌های جدید از همین لحظه
```
1. **Plan:** پورت آزاد، زیرشبکه‌ی آزاد (مثلاً `10.1.0.0/16`)، بررسی فایروال، تعداد peer.
2. **ساخت `wg1`:** با کپی `PreUp/PostUp/...` از `wg0` و جایگزینی نام اینترفیس (برای قواعد NAT/MASQUERADE)، و پورت جدید. **باز کردن پورت UDP جدید در فایروال/پنل ابری** (پنل فقط هشدار می‌دهد؛ این کار خارج از سرور ممکن است لازم باشد).
3. **کلون peerها** (در یک تراکنش برای هر دسته‌ی ۲۰۰تایی): کلید جدید، IP جدید، همان نام/یادداشت، محدودیت‌های FEAT-02 (همان `ExpireAt` و `DataLimitBytes`)، و مصرف فعلی به‌عنوان مقدار شروع (`cumu = usage` روی peer جدید) تا سقف حجم درست ادامه یابد.
4. **ثبت نگاشت** در `PeerMigrations`:

| ستون | |
|------|---|
| `OldConfiguration`, `OldPeerID` | |
| `NewConfiguration`, `NewPeerID`, `NewAllowedIP` | |
| `NotifiedAt` | زمان ارسال webhook |
| `FirstHandshakeOnNewAt` | اولین اتصال با کانفیگ جدید |
| `State` | `pending` · `notified` · `migrated` · `cutoff` |

5. **همگام‌سازی محدودیت در دوره‌ی گذار:** موتور محدودیت، مصرف peer قدیمی و جدید را **جمع** می‌کند (برای اینکه کاربر با استفاده از هر دو کانفیگ حجم دوبرابر نگیرد) و اگر سقف رد شد، هر دو را محدود می‌کند.
6. **اطلاع‌رسانی:** رویداد `peer.migrated` برای هر peer (با سرعت محدود، مثلاً ۲۰ در ثانیه).
7. **پیشرفت:** وقتی اولین handshake روی peer جدید دیده شد ← `migrated`.
8. **تاریخ قطع:** peerهای قدیمی که هنوز مهاجرت نکرده‌اند با دلیل `Migrated — please update your config` محدود می‌شوند؛ چند روز بعد اینترفیس قدیمی خاموش و حذف می‌شود.

## ۴. API (فقط session ادمین)
```http
POST /api/v2/interfaces/rotate/plan
{ "source": "wg0", "mode": "parallel", "target_name": "wg1", "listen_port": 51821,
  "address": "10.1.0.1/16", "rotate_peer_keys": true, "cutoff_at": "2026-11-01T00:00:00Z" }
→ { "plan": { "peers": 1610, "port_free": true, "subnet_free": true, "postup_rewritten": "...",
              "firewall_warning": "Open UDP 51821 in your provider firewall" }, "confirm_token": "…" }

POST /api/v2/interfaces/rotate/start   { "confirm_token": "…" }     → { "job_id": "…" }
GET  /api/v2/interfaces/rotate/status?source=wg0
→ { "state": "running", "total": 1610, "notified": 1610, "migrated": 1204, "cutoff_at": "…" }
POST /api/v2/interfaces/rotate/cutoff  { "source": "wg0" }          (اجرای زودتر از موعد)
```

## ۵. امنیت
- کلیدهای جدید هرگز در لاگ یا Audit نمی‌آیند.
- رویداد `peer.migrated` فقط شناسه‌ها را دارد؛ ربات کانفیگ را با `GET /api/v2/peer/config` (scope `peers:config`) می‌گیرد.
- بعد از cutoff و حذف `wg0`، کلید افشاشده دیگر هیچ کاربردی ندارد.

## ۶. تغییرات ربات OxBot
1. در شروع مهاجرت: `marzban_panel.inboundid` پنل مربوطه از `wg0` به `wg1` تغییر کند (فروش جدید روی wg1).
2. گیرنده‌ی webhook:
```php
case 'peer.migrated':
    $newPk = $evt['data']['new_peer_id'];
    $cfg = wg_api($inv['Service_location'], 'GET', '/api/v2/peer/config', ['id' => $newPk, 'format' => 'json']);
    update("invoice", "wg_public_key", $newPk, "id_invoice", $inv['id_invoice']);
    update("invoice", "user_info", json_encode(['public_key' => $newPk,
           'allowed_ip' => $evt['data']['new_allowed_ip'], 'name' => $inv['username']]), "id_invoice", $inv['id_invoice']);
    sendDocument($inv['id_user'], $cfg['json']['data']['file'],
        "🔐 برای امنیت بیشتر، کانفیگ سرویس شما به‌روزرسانی شد. لطفاً کانفیگ جدید را جایگزین کنید. کانفیگ قدیمی از تاریخ … کار نمی‌کند.");
    break;
```
3. یادآوری خودکار برای کاربرانی که تا ۳ روز قبل از cutoff مهاجرت نکرده‌اند (از `rotate/status` یا webhook جداگانه).

## ۷. تغییرات UI
ویزارد «Rotate Keys» در صفحه‌ی کانفیگ: انتخاب روش، پورت/زیرشبکه، تاریخ قطع، نمایش Plan، تأیید؛ صفحه‌ی پیشرفت با نمودار درصد مهاجرت.

## ۸. تست
- روی اینترفیس آزمایشی با ۵۰ peer: کلون، محدودیت‌ها، مصرف شروع، جمع مصرف در دوره‌ی گذار، cutoff.
- PostUp با `-o wg0` ← در wg1 به `-o …`/`%i` درست بازنویسی شود.
- کرش وسط کلون ← ادامه از دسته‌ی بعدی بدون تکرار (idempotent با `PeerMigrations`).

## ۹. استقرار
1. ابتدا یک اینترفیس آزمایشی.
2. اعلام قبلی به کاربران در کانال.
3. پنجره‌ی گذار پیشنهادی: ۱۰ تا ۱۴ روز.
4. پس از cutoff، حذف اینترفیس قدیمی و پاک کردن کلید آن از بکاپ‌های جدید.

## ۱۰. فایل‌های درگیر
`src/modules/InterfaceRotation.py` (جدید)، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (ساخت اینترفیس از الگو)، `PeerLimits.py` (جمع مصرف در گذار)، `src/api_v2.py`، ویزارد Vue، `tests/test_rotation.py`.
