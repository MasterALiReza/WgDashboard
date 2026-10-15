# FEAT-07 — تاریخچه‌ی مصرف اینترفیس (ساعتی/روزانه/دوره‌ای)

| | |
|---|---|
| اولویت | ⭐⭐ ارزش بالا |
| حجم کار | S-M (۱ روز پنل، نیم روز UI) |
| حل می‌کند | [TU-03](../04-Total-Usage-Review.md#tu-03)، [TU-05](../04-Total-Usage-Review.md#tu-05)؛ گزارش مصرف ماهانه‌ی سرور |
| وابستگی | رفع [TU-01](../04-Total-Usage-Review.md#tu-01) (تا داده‌ی ورودی درست باشد) |

## ۱. مسئله
Total Usage الان فقط یک عدد تجمعی است که از جمع peerها و یک ردیف snapshot ساخته می‌شود. نمی‌توان پرسید «امروز/این ماه چقدر مصرف شد؟»، روند را دید یا با سقف ترافیک دیتاسنتر مقایسه کرد. اگر ردیف snapshot خراب یا دستی تغییر کند، هیچ سابقه‌ای برای بازسازی نیست.

## ۲. اهداف
1. ثبت مصرف هر اینترفیس در سطل‌های **ساعتی** و **روزانه** (Rx/Tx جدا).
2. خلاصه‌ی «امروز، دیروز، دوره‌ی جاری، دوره‌ی قبل، کل» با روز شروع دوره‌ی قابل تنظیم (مثلاً هر ماه روز ۱ یا روز تمدید سرور).
3. ثبت «اصلاحیه» با دلیل و انجام‌دهنده (برای TU-03) به‌جای ویرایش دستی دیتابیس.
4. فاز ۲: مصرف روزانه‌ی هر peer برای «پرمصرف‌ترین کاربران».

## ۳. غیر اهداف
مصرف لحظه‌ای (Realtime) — همین الان در UI وجود دارد.

## ۴. طراحی

### ۴.۱ جداول (دیتابیس `wgdashboard`، تا در بکاپ‌ها باشند)

| جدول | ستون‌ها | کلید |
|------|---------|------|
| `InterfaceUsageHourly` | `Configuration`, `HourStart` (UTC), `RxBytes`, `TxBytes` | (`Configuration`, `HourStart`) |
| `InterfaceUsageDaily` | `Configuration`, `Day` (تاریخ در منطقه‌ی زمانی تنظیم‌شده), `RxBytes`, `TxBytes` | (`Configuration`, `Day`) |
| `InterfaceUsageCursor` | `Configuration`, `LastRxBytes`, `LastTxBytes`, `SampledAt` | `Configuration` |
| `InterfaceUsageAdjustments` | `ID`, `Configuration`, `At`, `RxBytes`, `TxBytes`, `Reason`, `Actor`, `AppliedToTotal` | `ID` |
| (فاز ۲) `PeerUsageDaily` | `Configuration`, `PeerID`, `Day`, `RxBytes`, `TxBytes` | (`Configuration`, `PeerID`, `Day`) |

### ۴.۲ جمع‌آوری (هر ۵ دقیقه در thread شماره ۳)
```python
def sample(conf):
    cur = conf._compute_data_usage(fresh=True)            # بدون کش ۳ ثانیه‌ای
    rx, tx = to_bytes(cur["Receive"]), to_bytes(cur["Sent"])
    c = cursor.get(conf.Name)
    if c is None:                                          # اولین اجرا: فقط مقداردهی، بدون backfill
        cursor.set(conf.Name, rx, tx); return
    d_rx, d_tx = rx - c.LastRxBytes, tx - c.LastTxBytes
    if d_rx < 0 or d_tx < 0:                               # فقط با ریستور بکاپ یا ویرایش دستی ممکن است
        log.warning(...); emit("interface.usage_anomaly", ...)
        d_rx, d_tx = max(d_rx, 0), max(d_tx, 0)
    upsert_hourly(conf.Name, hour_floor_utc(now), d_rx, d_tx)
    upsert_daily(conf.Name, local_day(now, tz), d_rx, d_tx)
    cursor.set(conf.Name, rx, tx)
```
- چون ورودی همان Total Usage است، حذف/ریست/restrict peerها روی تاریخچه اثری ندارد (همان منطق snapshot).
- `upsert` با `INSERT ... ON CONFLICT(Configuration, HourStart) DO UPDATE SET RxBytes = RxBytes + :rx` (SQLite ≥ 3.24).
- هرس: سطل‌های ساعتی قدیمی‌تر از ۹۰ روز در هرس ۶ ساعته پاک می‌شوند؛ روزانه‌ها ماندگارند (هر اینترفیس ~۳۶۵ ردیف در سال).

### ۴.۳ تنظیمات
```ini
[Usage]
timezone = Asia/Tehran
billing_cycle_day = 1
```

### ۴.۴ Backfill (اختیاری)
اگر `peer_tracking` قبلاً روشن بوده، اسکریپت `tools/backfill_usage.py` اختلاف‌های پشت‌سرهم جدول `<conf>_transfer` را جمع و در سطل‌ها می‌ریزد. در غیر این صورت تاریخچه از روز استقرار شروع می‌شود.

## ۵. API
```http
GET /api/v2/interfaces/usage?configuration=wg0&granularity=hour|day|month&from=2026-09-01&to=2026-10-14   (config:read)
→ { "series": [ { "t": "2026-10-13", "rx_bytes": …, "tx_bytes": … }, … ] }

GET /api/v2/interfaces/usage/summary?configuration=wg0      (config:read)
→ { "today": {...}, "yesterday": {...}, "this_cycle": {...}, "last_cycle": {...}, "all_time": {...},
    "cycle_start": "2026-10-01", "timezone": "Asia/Tehran" }

POST /api/v2/interfaces/usage/adjust                         (فقط session ادمین)
{ "configuration": "wg0", "rx_bytes": 1325598720000, "tx_bytes": 4639456116736,
  "reason": "مصرف کاربران حذف‌شده قبل از اصلاحیه‌ی snapshot", "apply_to_total": true }
```
`apply_to_total = true` همان مقدار را به جدول `<conf>_traffic_snapshot` هم اضافه می‌کند (با به‌روزرسانی اتمیک [TU-04](../04-Total-Usage-Review.md#tu-04)) و در [Audit Log](FEAT-09-Audit-Log.md) ثبت می‌شود — جایگزین امن کوئری دستی TU-03.

## ۶. امنیت
خواندن با `config:read` روی همان اینترفیس؛ اصلاحیه فقط با session ادمین.

## ۷. سازگاری
فقط جداول جدید. چیزی از رفتار فعلی تغییر نمی‌کند.

## ۸. تغییرات ربات OxBot
اختیاری: پنل وب ادمین ربات می‌تواند `usage/summary` را برای هر سرور نشان دهد («مصرف این ماه سرور ترکیه: X ترابایت»).

## ۹. تغییرات UI
- صفحه‌ی هر کانفیگ: کاشی‌های «امروز / این دوره / دوره‌ی قبل» و نمودار میله‌ای ۳۰ روز اخیر و ۱۲ ماه (Chart.js همین الان در پروژه استفاده می‌شود).
- تنظیمات: منطقه‌ی زمانی و روز شروع دوره.
- دیالوگ «اصلاح مصرف» با فیلد دلیل.

## ۱۰. تست
- delta با مقادیر عادی، صفر و منفی (anomaly)؛ مرز ساعت و نیمه‌شب در منطقه‌ی زمانی تهران؛ ری‌استارت پروسه (cursor در دیتابیس است ← بدون شکاف یا دوباره‌شماری)؛ اصلاحیه با `apply_to_total`.

## ۱۱. استقرار
بی‌خطر؛ جمع‌آوری از اولین ۵ دقیقه شروع می‌شود.

## ۱۲. فایل‌های درگیر
`src/modules/UsageHistory.py` (جدید)، [dashboard.py](../../src/dashboard.py) (thread ۳)، [WireguardConfiguration.py](../../src/modules/WireguardConfiguration.py) (`_compute_data_usage(fresh=True)`)، `src/api_v2.py`، کامپوننت جدید نمودار در صفحه‌ی کانفیگ، `tests/test_usage_history.py`.
