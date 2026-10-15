# ۰۸ — یکپارچگی فرانت‌اند با بک‌اند

## جدول خلاصه

| ID | شدت | عنوان |
|----|------|-------|
| [FE-01](#fe-01) | 🟡 | `fetchGet`/`fetchPost` هر پاسخ غیر 2xx (به‌جز 401) را می‌بلعند ← UI گیر می‌کند |
| [FE-02](#fe-02) | 🟡 | peerهای محدودشده دو بار نمایش داده می‌شوند |
| [FE-03](#fe-03) | 🟡 | مشکلات صفحه‌ی Global Backup (گیر کردن، مسیرهای مطلق، پیام‌های نادرست) |
| [FE-04](#fe-04) | 🟢 | اسرار سرور در store فرانت |
| [FE-05](#fe-05) | 🟢 | خروجی build (`dist`) در ریپو و احتمال ناهمخوانی با `src` |
| [FE-06](#fe-06) | 🟢 | موارد ریز |

---

<a id="fe-01"></a>
## FE-01 🟡 پاسخ‌های خطا بلعیده می‌شوند

**محل:** [fetch.js:37-86](../src/static/app/src/utilities/fetch.js#L37-L86)

```js
if (!x.ok){
    if (x.status !== 200){
        if (x.status === 401){ ... }
        throw { status: x.status, statusText: x.statusText }   // ← callback هرگز صدا زده نمی‌شود
    }
}
```
بک‌اند در بسیاری از جاها خطا را با کد HTTP غیر 200 **و** بدنه‌ی JSON حاوی پیام برمی‌گرداند (400، 404، 409، 429، 500، 503). در همه‌ی این حالت‌ها callback اجرا نمی‌شود؛ پس `loading` هرگز false نمی‌شود و پیام خطا هم نمایش داده نمی‌شود. نمونه‌های واقعی:

| صفحه | وضعیت | نتیجه |
|------|--------|--------|
| ورود ([signin.vue:63-83](../src/static/app/src/views/signin.vue#L63-L83)) | 429 بعد از ۵ تلاش | دکمه‌ی ورود برای همیشه در حالت loading |
| حذف کانفیگ ([deleteConfiguration.vue:18](../src/static/app/src/components/configurationComponents/deleteConfiguration.vue#L18)) | 404 | بدون پیام |
| تغییر نام ([updateConfigurationName.vue:30](../src/static/app/src/components/configurationComponents/editConfigurationComponents/updateConfigurationName.vue#L30)) | 400/404 | بدون پیام |
| حذف بکاپ کانفیگ ([backup.vue:17](../src/static/app/src/components/configurationComponents/backupRestoreComponents/backup.vue#L17)) | 404 | بدون پیام |
| Global Backup (create/restore/delete) | 409/400/404/500 | FE-03 |
| هر صفحه در حین ریستور | 503 | بدون پیام «در حال نگهداری» |

**راه‌حل:**
```js
export const fetchPost = async (url, body, callback) => {
    try {
        const x = await fetch(getUrl(url), { headers: getHeaders(), method: "POST", body: JSON.stringify(body) })
        let data = null
        try { data = await x.json() } catch { data = { status: false, message: `HTTP ${x.status}` } }
        if (x.status === 401) {
            DashboardConfigurationStore().newMessage("WGDashboard", "Sign in session ended, please sign in again", "warning")
            router.push({ path: '/signin' })
        }
        if (x.status === 503) {
            DashboardConfigurationStore().newMessage("WGDashboard", data?.message || "Maintenance in progress", "warning")
        }
        if (callback) callback(data)
    } catch (e) {
        if (callback) callback({ status: false, message: "Network error" })
    }
}
```
(همین الگو برای `fetchGet`.)

---

<a id="fe-02"></a>
## FE-02 🟡 نمایش تکراری peerهای محدودشده

**محل:** [peerList.vue:91-109](../src/static/app/src/components/configurationComponents/peerList.vue#L91-L109) + [dashboard.py:1566](../src/dashboard.py#L1566)

بک‌اند peerهای محدود را هم در `configurationPeers` و هم در `configurationRestrictedPeers` می‌فرستد. فرانت اولی را «فعال» و دومی را «محدود» علامت می‌زند ← هر کاربر محدود دو بار، یک بار در فیلتر Active. شمارنده‌ی `connected / total` بالای لیست هم غلط است (کارت اینترفیس در صفحه‌ی اصلی درست است چون از `TotalPeers` بک‌اند می‌خواند). رفع در بک‌اند: [BUG-01](03-Backend-Bugs-and-Logic.md#bug-01). اگر می‌خواهید سمت فرانت هم ایمن باشد:
```js
const restrictedIds = new Set(res.data.configurationRestrictedPeers.map(x => x.id))
configurationPeers.value = res.data.configurationPeers.filter(p => !restrictedIds.has(p.id))
```

---

<a id="fe-03"></a>
## FE-03 🟡 صفحه‌ی Global Backup

**محل:** [globalBackup.vue](../src/static/app/src/components/settingsComponent/globalBackup.vue)

1. **گیر کردن ساخت بکاپ** ([globalBackup.vue:301-313](../src/static/app/src/components/settingsComponent/globalBackup.vue#L301-L313)): اگر بکاپ دیگری در جریان باشد، بک‌اند 409 می‌دهد ← FE-01 ← `creating` برای همیشه true و دکمه‌ها غیرفعال تا رفرش صفحه.
2. **گیر کردن ریستور از لیست** ([globalBackup.vue:543-545](../src/static/app/src/components/settingsComponent/globalBackup.vue#L543-L545)): `new Promise(resolve => fetchPost(..., r => resolve(r)))` در پاسخ 400/409/500 هرگز resolve نمی‌شود ← نوار ریستور برای همیشه روی صفحه، همه‌ی دکمه‌ها غیرفعال. (مسیر آپلود با XHR این مشکل را ندارد.)
3. **مسیر مطلق** ([globalBackup.vue:330](../src/static/app/src/components/settingsComponent/globalBackup.vue#L330)، [globalBackup.vue:468](../src/static/app/src/components/settingsComponent/globalBackup.vue#L468)): `window.open('/api/globalBackup/download?...')` و `fetch('/api/getDashboardConfiguration')` از `getUrl` استفاده نمی‌کنند ← با `app_prefix` یا پشت nginx در زیرمسیر (مثلاً `https://domain/panel/`) دانلود و انتظار ری‌استارت کار نمی‌کنند؛ در حالت Cross-Server هم به سرور اشتباه می‌روند.
4. **پیام موفقیت همیشگی** ([globalBackup.vue:273-289](../src/static/app/src/components/settingsComponent/globalBackup.vue#L273-L289)): «Auto Backup settings saved» بدون بررسی نتیجه‌ی دو درخواست نمایش داده می‌شود.
5. **انتظار ری‌استارت** ([globalBackup.vue:462-488](../src/static/app/src/components/settingsComponent/globalBackup.vue#L462-L488)): اگر بکاپ ریستورشده `flask_secret_key` متفاوتی داشته باشد، session فعلی نامعتبر است و poll تا ۲ دقیقه 401 می‌گیرد؛ از طرف دیگر اولین poll (۳ ثانیه) ممکن است هنوز به پروسه‌ی قدیمی برسد ([GB-05](05-Global-Backup-Review.md#gb-05)). بهتر است از endpoint بدون احراز `/api/handshake` استفاده شود و صبر کند تا یک بار قطع و دوباره وصل شود.
6. **متن retention** ([globalBackup.vue:143](../src/static/app/src/components/settingsComponent/globalBackup.vue#L143)) می‌گوید «۱۰ بکاپ خودکار» ولی بک‌اند بکاپ‌های دستی را هم پاک می‌کند ([GB-09](05-Global-Backup-Review.md#gb-09)).
7. برچسب مرحله‌ی `syncing_runtime` در لیست نیست ([globalBackup.vue:345-358](../src/static/app/src/components/settingsComponent/globalBackup.vue#L345-L358)) و متن خام نمایش داده می‌شود؛ گزینه‌های زمان‌بندی («3 Hours» و ...) ترجمه نمی‌شوند.
8. `EventSource` نمی‌تواند هدر API Key بفرستد ([globalBackup.vue:381](../src/static/app/src/components/settingsComponent/globalBackup.vue#L381)، [423](../src/static/app/src/components/settingsComponent/globalBackup.vue#L423)) ← در Cross-Server پیشرفت دیده نمی‌شود.

---

<a id="fe-04"></a>
## FE-04 🟢 اسرار سرور در store فرانت

فرانت هنگام بارگذاری کل `getDashboardConfiguration` را در store نگه می‌دارد که شامل `flask_secret_key` و پسوردهاست ([SEC-02](02-Security-Vulnerabilities.md#sec-02)). هر XSS یا افزونه‌ی مرورگر مخرب، این‌ها را می‌خواند. با رفع SEC-02 حل می‌شود. (بررسی شد: در سورس فرانت `v-html` یا `innerHTML` استفاده نشده که خوب است.)

---

<a id="fe-05"></a>
## FE-05 🟢 `dist` در ریپو

پوشه‌ی `src/static/dist/` (خروجی build) commit می‌شود و Flask مستقیماً از آن سرو می‌کند. هر تغییر در `src/static/app/src` بدون build مجدد، روی سرور اعمال نمی‌شود و برعکس. پیشنهاد: یک GitHub Action برای build و بررسی اینکه `dist` با `src` هم‌خوان است، یا build در زمان نصب (`wgd.sh install`).

---

<a id="fe-06"></a>
## FE-06 🟢 موارد ریز

- فایل بلااستفاده‌ی [peerList.old.vue](../src/static/app/src/components/configurationComponents/peerList.old.vue).
- بعد از رفع [BUG-01](03-Backend-Bugs-and-Logic.md#bug-01)، کامپوننت‌هایی که روی `restricted` فیلتر می‌کنند ([selectPeers.vue:31-53](../src/static/app/src/components/configurationComponents/selectPeers.vue#L31-L53)) بدون تغییر کار می‌کنند چون فلگ را خود فرانت ست می‌کند.
- تبدیل endpointهای GET تغییردهنده به POST ([SEC-06](02-Security-Vulnerabilities.md#sec-06)) نیاز به تغییر متناظر در فرانت دارد (toggle کانفیگ، OIDC، Clients).
