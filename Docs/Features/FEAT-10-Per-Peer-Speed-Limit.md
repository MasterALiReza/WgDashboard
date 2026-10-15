# FEAT-10 — محدودیت سرعت هر کاربر (Per-Peer Speed Limit)

| | |
|---|---|
| اولویت | ⭐ خوب است داشته باشیم |
| حجم کار | M-L (۲-۳ روز با تست روی سرور واقعی) |
| حل می‌کند | فروش پلن‌های سرعتی؛ جلوگیری از اشغال کل پهنای باند توسط چند کاربر |
| وابستگی | اختیاری: [FEAT-02](FEAT-02-Peer-Limits.md) (سرعت به‌عنوان بخشی از پلن) |

## ۱. مسئله
همه‌ی کاربران یک اینترفیس پهنای باند را مشترک و بدون محدودیت استفاده می‌کنند. چند کاربر دانلودکننده می‌توانند پینگ و کیفیت بازی بقیه را خراب کنند — برای سرویس‌های «گیمینگ کالاف» شما این مهم است. `tcconfig` در [requirements.txt](../../src/requirements.txt) هست ولی هیچ‌جا استفاده نشده.

## ۲. اهداف
1. سقف سرعت دانلود و آپلود جداگانه برای هر peer (kbit/s).
2. کارایی با هزاران peer (طبقه‌بندی O(1)).
3. ماندگاری بعد از ری‌استارت اینترفیس/سرور.
4. شکست ایمن: اگر اعمال `tc` خطا داد، ترافیک **قطع نشود** (برگشت به بدون محدودیت + هشدار).

## ۳. غیر اهداف
QoS پیشرفته (اولویت‌بندی پورت بازی‌ها). اگرچه با همین زیرساخت بعداً ممکن است.

## ۴. طراحی

### ۴.۱ مدل داده — `PeerSpeedLimits` (دیتابیس `wgdashboard`)
| ستون | توضیح |
|------|--------|
| `Configuration`, `PeerID` | کلید |
| `DownKbit`, `UpKbit` | `NULL` = بدون محدودیت |
| `ClassMinor` | شناسه‌ی کلاس HTB (۱ تا 0xfffe)، یکتا در هر اینترفیس |

### ۴.۲ ساختار `tc`
- **دانلود کاربر** = ترافیکی که از `wg0` **خارج** می‌شود ← HTB روی خود `wg0`، طبقه‌بندی با IP مقصد.
- **آپلود کاربر** = ترافیک **ورودی** `wg0` ← هدایت به یک دستگاه IFB و HTB روی آن، طبقه‌بندی با IP مبدأ.
```bash
# یک بار برای هر اینترفیس
tc qdisc replace dev wg0 root handle 1: htb default ffff
tc class add dev wg0 parent 1:  classid 1:1    htb rate 10gbit
tc class add dev wg0 parent 1:1 classid 1:ffff htb rate 10gbit          # پیش‌فرض: بدون محدودیت
ip link add ifbwg0 type ifb && ip link set ifbwg0 up                     # نام ≤ 15 کاراکتر
tc qdisc add dev wg0 handle ffff: ingress
tc filter add dev wg0 parent ffff: matchall action mirred egress redirect dev ifbwg0
tc qdisc replace dev ifbwg0 root handle 1: htb default ffff
tc class add dev ifbwg0 parent 1:  classid 1:1    htb rate 10gbit
tc class add dev ifbwg0 parent 1:1 classid 1:ffff htb rate 10gbit

# برای هر peer محدود (مثال: 10.0.3.17، دانلود ۲۰ مگ، آپلود ۵ مگ، minor = 0x1a)
tc class  add dev wg0    parent 1:1 classid 1:1a htb rate 20mbit ceil 20mbit
tc qdisc  add dev wg0    parent 1:1a fq_codel
tc filter add dev wg0    parent 1: protocol ip prio 10 flower dst_ip 10.0.3.17/32 classid 1:1a
tc class  add dev ifbwg0 parent 1:1 classid 1:1a htb rate 5mbit ceil 5mbit
tc qdisc  add dev ifbwg0 parent 1:1a fq_codel
tc filter add dev ifbwg0 parent 1: protocol ip prio 10 flower src_ip 10.0.3.17/32 classid 1:1a
```
- **`flower`** طبقه‌بندی را با جدول هش انجام می‌دهد (برخلاف `u32` خطی) ← مناسب برای هزاران peer. در شروع، پشتیبانی کرنل (`cls_flower`، `ifb`، `sch_htb`) بررسی می‌شود؛ در صورت نبود، فیچر غیرفعال و در Health گزارش می‌شود.
- peer با چند Allowed IP یا IPv6: برای هر CIDR یک فیلتر (`protocol ipv6` برای v6).
- `fq_codel` داخل هر کلاس تأخیر (bufferbloat) را پایین نگه می‌دارد — مهم برای بازی.
- همه‌ی دستورها در یک فایل و با `tc -batch` اعمال می‌شوند (سریع و تقریباً اتمیک).

### ۴.۳ چرخه‌ی عمر
| رویداد | کار |
|--------|-----|
| روشن شدن اینترفیس (`toggleConfiguration` و autostart) | ساخت ساختار پایه + همه‌ی کلاس‌های peer |
| شروع برنامه | اگر اینترفیس up است، همگام‌سازی |
| تغییر/حذف محدودیت، حذف peer، تغییر Allowed IP | افزودن/حذف کلاس و فیلتر همان peer |
| restrict | کلاس می‌ماند (بی‌اثر) |
| Reconcile ۵ دقیقه‌ای | مقایسه‌ی `tc -j class show` با دیتابیس و اصلاح اختلاف |
| خطای `tc` | حذف qdisc ریشه (برگشت به بدون محدودیت) + هشدار ([FEAT-08](FEAT-08-Health-Metrics-Alerts.md)) |

## ۵. API
```http
PUT /api/v2/peer/speed    (peers:write)   { "configuration": "wg0", "id": "<pk>", "down_kbit": 20000, "up_kbit": 5000 }
GET /api/v2/peer/speed?configuration=wg0&id=<pk>
POST /api/v2/peers/speed/bulk { "configuration": "wg0", "ids": [...], "down_kbit": …, "up_kbit": … }
```
در FEAT-02، فیلد اختیاری `speed: {down_kbit, up_kbit}` در `limits` (و ساخت peer در FEAT-03) پذیرفته می‌شود.

## ۶. امنیت
فقط با `peers:write`. مقادیر عددی و محدوده‌دار؛ هیچ ورودی کاربر مستقیم وارد رشته‌ی فرمان نمی‌شود (IPها با `ipaddress` اعتبارسنجی و بازسازی می‌شوند).

## ۷. سازگاری
پیش‌فرض خاموش (`[Features] speed_limits = false`). تا وقتی محدودیتی تعریف نشده، هیچ qdiscی ساخته نمی‌شود.

## ۸. تغییرات ربات OxBot
محصول‌ها می‌توانند فیلد سرعت داشته باشند؛ در ساخت سرویس `limits.speed` فرستاده شود. (مثلاً «گیمینگ — بدون محدودیت» در برابر «اقتصادی — ۱۰ مگ».)

## ۹. تغییرات UI
فیلد سرعت دانلود/آپلود در تب Limits مودال peer؛ نشان «⚡ 20/5 Mbps» روی کارت.

## ۱۰. تست
- روی VM با `iperf3` از پشت یک کلاینت WireGuard: سقف دانلود/آپلود رعایت شود، کاربر بدون محدودیت تحت تأثیر نباشد.
- ۲۰۰۰ کلاس: مصرف CPU سرور و تأخیر (ping) قبل/بعد.
- ری‌استارت اینترفیس ← محدودیت‌ها برمی‌گردند.
- خطای عمدی `tc` ← ترافیک برقرار و هشدار ارسال شود.

## ۱۱. استقرار
ابتدا روی یک اینترفیس آزمایشی با چند کاربر داخلی؛ سپس اینترفیس اصلی در ساعت کم‌ترافیک.

## ۱۲. ریسک‌ها
- سربار CPU روی VPSهای ضعیف با پهنای باند بالا (HTB تک‌هسته‌ای است). اگر لازم شد، فقط دانلود را محدود کنید.
- تداخل با `PostUp`هایی که خودشان `tc` دارند — قبل از فعال‌سازی بررسی شود.

## ۱۳. فایل‌های درگیر
`src/modules/SpeedLimits.py` (جدید)، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (hook در toggle/reconcile/delete)، `src/api_v2.py`، UI تب Limits، `tests/test_speed_limits.py` (تولید batch بدون اجرای واقعی).
