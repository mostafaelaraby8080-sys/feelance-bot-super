# بوت العروض التلقائي (تيليجرام)

بوت يفحص **نفذلي / مستقل / خمسات** كل فترة، يبعتلك إشعار فيه زرار
«📝 قدم على الوظيفة»، يسألك السعر والمدة، يجهّز العرض، ويقدر يبعته للموقع.

## الملفات

| الملف | الوظيفة |
|---|---|
| `bot_interactive.py` | **نقطة التشغيل** — Application + JobQueue + ConversationHandler |
| `bot.py` | قراءة `get_config` من `.env` فقط (مش بيشغّل البوت) |
| `common.py` | `Job` + `fetch_html` (retry/timeout) + أدوات استخراج النص |
| `database.py` | SQLite: `seen_jobs` (منع التكرار) + `applications` (حالة كل عرض) |
| `scrapers.py` | واجهة موحدة: `enabled_platforms()`, `get(platform)` |
| `scraper_nafezly.py` | القراءة من قناة تيليجرام `t.me/s/Nafezly` |
| `scraper_mostaql.py` | قسم البرمجة في مستقل |
| `scraper_khamsat.py` | طلبات المجتمع في خمسات |
| `ai_writer.py` | توليد نص العرض (LLM اختياري + قالب احتياطي) |
| `proposal_submitter.py` | إرسال العرض: `http` API أو `playwright` متصفح |
| `smoke_test.py` | 40+ اختبار بدون شبكة (بارسرز + DB + محاكاة الحوار) |

## التشغيل

```bash
pip install -r requirements.txt
copy .env.example .env      # واملأ التوكن ومعرّف الشات
python smoke_test.py        # تتأكد إن كل حاجة سليمة
python bot_interactive.py   # البوت
```

تجربة سريعة لدورة فحص واحدة بدون polling:

```bash
python bot_interactive.py --once
```

## الحوار

1. البوت يلاقي مشروع → يبعت رسالة فيها العنوان/الميزانية/التفاصيل/عرض مقترح.
2. تحت الرسالة: `🔗 افتح المشروع` و `📝 قدم على الوظيفة`.
3. الضغط على «قدم» → يسأل: **العرض المالي** (رقم فقط).
4. → يسأل: **كم يوماً**.
5. → يجهّز العرض ويحفظه كـ `draft` ويعرضه مع:
   `🚀 إرسال للموقع الآن` / `🔗 افتح والصق` / `🔄 عرض تاني` / `❌ إلغاء`.
6. عند الإرسال: `sent` أو `failed` (ونص العرض يفضل معروض عشان تنسخه يدوي).

## الإرسال التلقائي

افتراضياً `SUBMIT_BACKEND=playwright` ومحتاج **مرة واحدة لكل منصة**:

```bash
pip install playwright
playwright install chromium
python proposal_submitter.py --login khamsat
```

قبل أي إرسال حقيقي، اتأكد من الـ selectors:

```bash
python proposal_submitter.py --inspect "https://khamsat.com/community/requests/123"
python proposal_submitter.py --dry-run khamsat "https://khamsat.com/community/requests/123" 150 "5 أيام"
```

`--dry-run` بيملأ الفورم ويصوّر من غير ما يدوس إرسال.

لو عندك `API` جاهز بدل المتصفح:

```env
SUBMIT_BACKEND=http
SUBMIT_API_URL=https://your-server/apply
SUBMIT_API_TOKEN=...
```

## تحذيرات

- التقديم الآلي على منصات العمل الحر **ممكن يخالف شروط الاستخدام** ويعرّض حسابك للحظر.
  البوت بيسجّل كل محاولة في `applications` ويقدر يعمل إرسال بـ `--dry-run` الأول.
- الـ selectors في `proposal_submitter.py` مبنية على أفضل تقدير — استخدم `--inspect` وظبطها.
- `disable_web_page_preview` مهجور (deprecated) في PTB 21+ وموسوم بـ `TODO`.

## افتراضات

- `Job.id` = `f"{platform}:{raw_id}"` — يمنع تعارض الأرقام بين المنصات.
- `generate_proposal(title, details, platform_label, budget, duration)`.
- من غير `OPENAI_API_KEY` البوت شغال تمام بالقالب الجاهز.
