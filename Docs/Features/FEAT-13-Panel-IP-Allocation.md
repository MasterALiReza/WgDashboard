# FEAT-13 — تخصیص IP توسط خود پنل

| | |
|---|---|
| اولویت | ⭐⭐ ارزش بالا (quick win) |
| حجم کار | S — مرحله‌ی ۱: فقط ربات (چند ساعت)؛ مرحله‌ی ۲: بهبود پنل (نیم روز) |
| حل می‌کند | پیچیدگی و شکنندگی منطق IP در ربات، قفل MySQL، دو بار کشیدن کل اینترفیس در هر خرید ([BOT-02](../06-OxBot-Integration.md#bot-02)) |
| وابستگی | ندارد (مرحله‌ی ۲ در [FEAT-03](FEAT-03-Atomic-Idempotent-Operations.md) استفاده می‌شود) |

## ۱. مسئله
تابع `addpear` ربات برای انتخاب IP: زیرشبکه را کش می‌کند، با `GET_LOCK` در MySQL قفل می‌گیرد، IPهای مصرف‌شده را از دیتابیس ربات **و** از پنل (کل `getWireguardConfigurationInfo`) جمع می‌کند، یک IP تصادفی آزاد انتخاب و سپس به پنل می‌فرستد. این کار:
- در هر خرید یک بار کل اینترفیس را می‌کشد،
- با هر ابزار دیگری که peer می‌سازد (UI پنل، ربات دوستتان) هماهنگ نیست؛ قفل MySQL فقط بین فرایندهای همان ربات است،
- اگر قفل در ۱۵ ثانیه گرفته نشود، خرید شکست می‌خورد.

در حالی که **پنل خودش** وقتی `allowed_ips` خالی باشد، اولین IP آزاد را زیر قفل همان اینترفیس انتخاب و در پاسخ برمی‌گرداند ([dashboard.py:1465-1473](../../src/dashboard.py#L1465-L1473)، [WireguardConfiguration.py:1897-1931](../../src/modules/WireguardConfiguration.py#L1897-L1931)). قفل پنل بین **همه‌ی** فراخواننده‌ها مشترک است.

## ۲. اهداف
1. حذف کامل منطق IP از ربات.
2. تخصیص اتمیک زیر قفل اینترفیس.
3. (مرحله‌ی ۲) قابلیت‌هایی که ربات الان دارد در پنل: رد کردن آدرس‌های `.0`/`.255`، رزرو بازه، و جلوگیری از استفاده‌ی سریع دوباره از IP آزادشده.

## ۳. طراحی

### مرحله‌ی ۱ — فقط تغییر ربات (بدون تغییر پنل)
در بدنه‌ی `addPeers`، فیلدهای `allowed_ips` و `allowed_ip` **فرستاده نشوند**. IP تخصیص‌یافته از پاسخ خوانده می‌شود:
```php
$peerConfig = [
    'name' => $usernameac,
    'private_key' => $keys['private_key'], 'public_key' => $keys['public_key'],
    'preshared_key' => $keys['preshared_key'],
    'mtu' => 1420, 'keepalive' => 21,
    // allowed_ips حذف شد ← پنل زیر قفل انتخاب می‌کند
];
$res = /* POST /api/addPeers/<inboundid> مثل قبل */;
$body = json_decode($res['body'], true);
$allowedIp = $body['data'][0]['allowed_ip'] ?? null;     // مثل "10.0.3.17/32"
```
توابع قابل حذف در `WGDashboard.php` ربات: `getCachedSubnet`، `getUsedIPs`، `getUsedIPsFromDb`، `getNextAvailableIP`، `isSubnetFull`، و بخش `GET_LOCK`/`RELEASE_LOCK` در `addpear`. ستون `subnet_cache` جدول `marzban_panel` هم دیگر لازم نیست.

**نکته:** پنل فعلی به ترتیب از اولین IP آزاد استفاده می‌کند و در زیرشبکه‌های بزرگ‌تر از ‎/24‎ (مثلاً ‎/16‎) آدرس‌هایی مثل `10.0.1.0` یا `10.0.1.255` را هم می‌دهد. این آدرس‌ها در WireGuard کاملاً معتبرند، ولی ربات عمداً از آن‌ها پرهیز می‌کرد (بعضی سیستم‌عامل‌ها/روترها با آن‌ها مشکل دارند). اگر این برایتان مهم است، تا مرحله‌ی ۲ صبر کنید.

### مرحله‌ی ۲ — بهبود پنل
تنظیمات هر اینترفیس (در `ConfigurationsInfo`):
```json
"IPAllocation": {
  "strategy": "sequential",          // sequential | random
  "skip_octet_boundaries": true,     // رد کردن x.x.x.0 و x.x.x.255
  "reserved": ["10.0.0.2-10.0.0.20"],
  "reuse_cooldown_hours": 24
}
```
- **Cooldown:** جدول `ReleasedIPs(Configuration, IP, ReleasedAt)` در `deletePeers` پر می‌شود؛ IP تا X ساعت دوباره داده نمی‌شود (جلوگیری از رسیدن ترافیک کاربر قبلی که هنوز کانفیگ قدیمی را دارد به کاربر جدید).
- **کارایی:** مجموعه‌ی IPهای مصرف‌شده از یک `SELECT allowed_ip` روی دو جدول ساخته می‌شود (نه از اشیای Peer).
- **ظرفیت:** اگر IP آزاد نماند ← `409` با `error_code: "NO_FREE_IP"` و `data: {capacity, used}` (همان اطلاعات دیباگی که ربات الان خودش می‌سازد).
- **IPv6:** اگر اینترفیس آدرس v6 هم دارد، به‌طور اختیاری از هر خانواده یک آدرس.

## ۴. API
مرحله‌ی ۱ از همان `POST /api/addPeers/<conf>` استفاده می‌کند. در v2 (FEAT-03) `allowed_ip: null` یعنی تخصیص پنل. یک endpoint کمکی برای گزارش:
```http
GET /api/v2/ip-pool?configuration=wg0      (config:read)
→ { "subnets": [{ "cidr": "10.0.0.0/16", "capacity": 65020, "used": 1610, "reserved": 19, "cooling_down": 12 }] }
```

## ۵. امنیت
تغییری در سطح دسترسی نیست. اعتبارسنجی `allowed_ip` ارسالی (اگر فرستاده شود) با `ipaddress` و بررسی تداخل (`overlaps`) به‌جای مقایسه‌ی رشته‌ای ([BUG-21](../03-Backend-Bugs-and-Logic.md#bug-21)).

## ۶. سازگاری
مرحله‌ی ۱ هیچ تغییری در پنل ندارد. اگر ربات در آینده دوباره `allowed_ips` بفرستد، رفتار قبلی حفظ است.

## ۷. تست
- دو خرید هم‌زمان ← دو IP متفاوت (قفل پنل).
- ساخت هم‌زمان از UI و ربات ← بدون تداخل.
- `skip_octet_boundaries` در ‎/16‎ ← هرگز `.0`/`.255`.
- حذف و ساخت فوری ← IP آزادشده تا پایان cooldown داده نشود.
- پر شدن زیرشبکه ← `NO_FREE_IP`.

## ۸. استقرار
مرحله‌ی ۱ را می‌توان همین الان در ربات انجام داد. Rollback = برگرداندن تابع `addpear` قبلی.

## ۹. فایل‌های درگیر
ربات: `WGDashboard.php`. پنل (مرحله‌ی ۲): [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (`getAvailableIP`، `deletePeers`)، [WireguardConfigurationInfo.py](../../src/modules/WireguardConfigurationInfo.py)، `tests/test_ip_allocation.py`.
