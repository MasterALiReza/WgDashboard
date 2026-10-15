# FEAT-11 — تشخیص اشتراک‌گذاری اکانت

| | |
|---|---|
| اولویت | ⭐ خوب است داشته باشیم |
| حجم کار | S-M (۱-۱.۵ روز) |
| حل می‌کند | استفاده‌ی هم‌زمان چند نفر از یک سرویس (درآمد از دست رفته) |
| وابستگی | [FEAT-04](FEAT-04-Signed-Event-Webhooks.md) برای اطلاع به ربات |

## ۱. مسئله
در WireGuard هر peer در هر لحظه فقط **یک endpoint** دارد و سرور به آخرین آدرسی که بسته‌ی معتبر از آن آمده «roam» می‌کند. اگر دو دستگاه با یک کانفیگ هم‌زمان وصل شوند، endpoint مدام بین دو IP جابه‌جا می‌شود. الان این الگو دیده نمی‌شود.

## ۲. اهداف
1. تشخیص الگوی «رفت‌وبرگشت» endpoint بین چند IP در بازه‌ی کوتاه.
2. ثبت شواهد (IPها، تعداد جابه‌جایی، بازه) و نمایش در UI.
3. اطلاع به ربات با webhook؛ اقدام خودکار **اختیاری و پیش‌فرض خاموش**.
4. حداقل نگهداری IP کاربران (حریم خصوصی).

## ۳. غیر اهداف
تشخیص قطعی؛ این فقط یک سیگنال است.

## ۴. طراحی

### ۴.۱ منبع داده
`refreshPeersRuntimeStats` هر ~۱۰ ثانیه endpoint هر peer را از `wg show dump` می‌خواند ([WireguardConfiguration.py:1348-1354](../../src/modules/WireguardConfiguration.py#L1348-L1354)). در همان‌جا وقتی **IP** (نه پورت) عوض شد، یک نمونه به بافر حلقوی درون‌حافظه‌ی همان peer اضافه می‌شود: `deque(maxlen=50)` از `(ts, ip)`.

### ۴.۲ الگوریتم (هر ۶۰ ثانیه)
برای هر peer با تغییر در پنجره‌ی `W` (پیش‌فرض ۱۰ دقیقه):
- `C` = تعداد تغییر IP در پنجره
- `D` = تعداد IPهای متمایز
- `A` = تعداد «برگشت» (IP فعلی = IP دو تغییر قبل؛ الگوی A→B→A)

```
مشکوک اگر:  (D ≥ 2 و A ≥ 3)   یا   D ≥ 4
امتیاز = A × 2 + (D − 1)
```
- تغییر شبکه‌ی عادی موبایل (Wi-Fi ← 4G) یک یا دو تغییر یک‌طرفه است و `A` را بالا نمی‌برد.
- فقط IP مقایسه می‌شود؛ تغییر پورت در CGNAT نادیده گرفته می‌شود.
- آستانه‌ها قابل تنظیم‌اند:
```ini
[SharingDetection]
enabled = false
window_seconds = 600
min_alternations = 3
min_distinct_ips = 4
auto_restrict_after_flags_24h = 0      ; 0 = هرگز خودکار قطع نکن
retention_days = 7
```

### ۴.۳ ذخیره
| جدول | ستون‌ها |
|------|---------|
| `PeerEndpointEvents` (تجمیعی) | `Configuration`, `PeerID`, `IP`, `FirstSeen`, `LastSeen`, `Count` |
| `SharingFlags` | `Configuration`, `PeerID`, `FlaggedAt`, `Score`, `Evidence` (JSON: IPها، C، D، A، پنجره), `Acknowledged` |

هر دو با نگهداری ۷ روزه. (جدول `_history_endpoint` فعلی هم وجود دارد ولی فقط وقتی tracking روشن است پر می‌شود و برای این کار مناسب نیست.)

### ۴.۴ اقدام
- همیشه: ثبت flag و رویداد `peer.sharing_suspected` ([FEAT-04](FEAT-04-Signed-Event-Webhooks.md)) — حداکثر یک بار در ساعت برای هر peer.
- اختیاری: اگر تعداد flag در ۲۴ ساعت ≥ `auto_restrict_after_flags_24h` ← restrict با دلیل `Account sharing detected` (ربات می‌تواند به کاربر هشدار بدهد و بعد از توضیح، دوباره فعال کند).

## ۵. API
```http
GET  /api/v2/sharing/flags?configuration=wg0&since=…      (peers:read)
POST /api/v2/sharing/flags/ack { "configuration": "wg0", "id": "<pk>" }   (peers:write)
```

## ۶. امنیت و حریم خصوصی
- IPها فقط ۷ روز و فقط برای peerهایی که تغییر داشته‌اند نگه داشته می‌شوند.
- geolocation به سرویس خارجی ارسال نمی‌شود ([SEC-12](../02-Security-Vulnerabilities.md#sec-12)). اگر لازم شد، دیتابیس آفلاین GeoLite2.

## ۷. سازگاری
پیش‌فرض خاموش؛ فقط حافظه و دو جدول کوچک.

## ۸. تغییرات ربات OxBot
در گیرنده‌ی webhook (FEAT-04):
```php
case 'peer.sharing_suspected':
    sendmessage($inv['id_user'], "⚠️ سرویس شما هم‌زمان از چند مکان استفاده می‌شود. هر سرویس فقط برای یک دستگاه است.", null, 'HTML');
    // اختیاری: گزارش به ادمین
    break;
```

## ۹. تغییرات UI
نشان «مشکوک به اشتراک» روی کارت peer، فیلتر مربوط، و مودال شواهد (IPها، زمان‌ها، امتیاز) با دکمه‌ی «بررسی شد».

## ۱۰. تست
- توالی مصنوعی A,B,A,B,A در ۵ دقیقه ← flag.
- Wi-Fi ← 4G ← Wi-Fi در ۲ ساعت ← بدون flag.
- تغییر فقط پورت ← بدون نمونه.
- عدم ارسال webhook تکراری در یک ساعت.

## ۱۱. استقرار
یک هفته در حالت فقط-ثبت (بدون webhook به کاربر) اجرا شود تا آستانه‌ها با داده‌ی واقعی تنظیم شوند.

## ۱۲. فایل‌های درگیر
`src/modules/SharingDetection.py` (جدید)، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (hook در refresh)، [dashboard.py](../../src/dashboard.py) (اجرا در thread)، UI کارت peer، `tests/test_sharing_detection.py`.
