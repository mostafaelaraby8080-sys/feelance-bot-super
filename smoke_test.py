"""اختبار سريع بدون شبكة ولا تيليجرام.

بيتحقق من: قاعدة البيانات، أدوات الاستخراج، البارسرز التلاتة، وقالب العرض.

    python smoke_test.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# كوندول ويندوز العربي بيبقى cp1256 ومش بيرمّز الإيموجي
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# لازم قبل استيراد database — بنستخدم ملف مؤقت جوه فولدر المشروع
_TEST_DB = Path(__file__).resolve().parent / ".smoke_test.db"
for _suffix in ("", "-wal", "-shm"):
    Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)
os.environ["DATABASE_PATH"] = str(_TEST_DB)
os.environ.pop("OPENAI_API_KEY", None)  # نتأكد إننا بنستخدم القالب

import database  # noqa: E402
from ai_writer import generate_proposal  # noqa: E402
from common import Job, page_lines, section_after, value_after  # noqa: E402
from scraper_khamsat import parse_listing as khamsat_listing  # noqa: E402
from scraper_mostaql import parse_details as mostaql_details  # noqa: E402
from scraper_nafezly import parse_jobs as nafezly_jobs  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  ✅ {name}")
    else:
        failures.append(f"{name} {detail}".strip())
        print(f"  ❌ {name} {detail}".strip())


def test_database() -> None:
    print("\n[1] database")
    database.init_db()
    check("جديد مش متشاف", database.is_job_seen("khamsat:1") is False)

    database.mark_job_seen("khamsat:1", "مشروع تجريبي")
    check("اتسجل", database.is_job_seen("khamsat:1") is True)
    check("العدّ للمنصة", database.count_seen_jobs("khamsat") == 1)
    check("عدّ الكل", database.count_seen_jobs() == 1)

    database.mark_job_seen("khamsat:1", "مشروع تجريبي")  # تكرار
    check("مفيش تكرار", database.count_seen_jobs() == 1)

    check("مفيش عرض مرسل", database.has_sent_application("khamsat:1") is False)
    database.save_application(
        "khamsat:1", title="مشروع", link="https://x/1", price="150", duration="5 أيام"
    )
    database.set_application_status("khamsat:1", "sent")
    check("اتسجل كمرسل", database.has_sent_application("khamsat:1") is True)

    info = database.stats()
    check("إحصائيات", info["seen"] == 1 and info["applications"].get("sent") == 1, str(info))


def test_common() -> None:
    print("\n[2] common")
    lines = ["الميزانية", "500 دولار", "مدة التنفيذ", "5", "المهارات", "بايثون"]
    check("value_after (سطر بعده)", value_after(lines, "الميزانية") == "500 دولار")
    check("value_after (سطر واحد)", value_after(["السعر: 200"], "السعر") == "200")
    check(
        "value_after مع NBSP",
        value_after(["الميزانية\u00a0750 جنيه"], "الميزانية") == "750 جنيه",
    )
    check("value_after مش موجود", value_after(lines, "غير موجود") == "")

    section = section_after(lines, "الميزانية", {"مدة التنفيذ"})
    check("section_after", section == "500 دولار", repr(section))


def test_khamsat() -> None:
    print("\n[3] khamsat.parse_listing")
    html = """
    <html><body>
      <a href="/community/requests/111">مشروع برمجة موقع</a>
      <a href="/community/requests/111/reply">رد</a>
      <a href="https://khamsat.com/community/requests/222">تطبيق موبايل</a>
      <a href="/community/requests/abc">مش مشروع</a>
    </body></html>
    """
    jobs = khamsat_listing(html)
    ids = sorted(job.raw_id for job in jobs)
    check("عدد المشاريع", len(jobs) == 2, str(ids))
    check("الـ id صح", ids == ["111", "222"], str(ids))
    check("اللينك مطلق", all(job.link.startswith("https://khamsat.com/") for job in jobs))
    check("عنوان 111 هو العنوان مش 'رد'", jobs[0].title == "مشروع برمجة موقع", jobs[0].title)


def test_mostaql() -> None:
    print("\n[4] mostaql.parse_details")
    html = """
    <html><body>
      <h1>مطلوب مبرمج بايثون</h1>
      <div>تفاصيل المشروع</div>
      <div>عايز سكريبت يجمع البيانات من موقع معين ويحفظها في قاعدة بيانات.</div>
      <div>الميزانية</div><div>500 دولار</div>
      <div>مدة التنفيذ</div><div>15</div>
      <div>المهارات</div><div>Python</div>
    </body></html>
    """
    data = mostaql_details(html)
    check("العنوان من h1", data["title"] == "مطلوب مبرمج بايثون", data["title"])
    check("التفاصيل", "سكريبت" in data["details"], data["details"])
    check("الميزانية", data["budget"] == "500 دولار", data["budget"])
    check("المدة 15 -> يوم", data["duration"] == "15 يوم", data["duration"])

    data5 = mostaql_details(html.replace("<div>15</div>", "<div>5</div>"))
    check("المدة 5 -> أيام", data5["duration"] == "5 أيام", data5["duration"])

    empty = mostaql_details("<html><body><h1>x</h1></body></html>")
    check("صفحة فاضية ما ترميش", empty["duration"] == "" and empty["details"] == "")


def test_nafezly() -> None:
    print("\n[5] nafezly.parse_jobs")
    html = """
    <div class="tgme_widget_message_wrap">
      <div class="tgme_widget_message_text">
        مشروع جديد<br/>
        عنوان المشروع : متجر إلكتروني بلارافيل<br/>
        تفاصيل المشروع : مطلوب متجر كامل مع لوحة تحكم<br/>
        الميزانية : 300 دولار<br/>
        مدة التنفيذ : 10 أيام<br/>
      </div>
      <a href="https://nafezly.com/project/987">رابط المشروع</a>
    </div>
    """
    jobs = nafezly_jobs(html)
    check("مشروع واحد", len(jobs) == 1, str(len(jobs)))
    if jobs:
        job = jobs[0]
        check("العنوان", job.title == "متجر إلكتروني بلارافيل", job.title)
        check("الميزانية", job.budget == "300 دولار", job.budget)
        check("المدة", job.duration == "10 أيام", job.duration)
        check("التفاصيل", "لوحة تحكم" in job.details, job.details)
        check("اللينك", job.link == "https://nafezly.com/project/987", job.link)
        check("الـ id الموحد", job.id == "nafezly:987", job.id)

    relative = nafezly_jobs(
        '<div class="tgme_widget_message_wrap">'
        '<div class="tgme_widget_message_text">'
        "عنوان المشروع : تطبيق تفاصيل المشروع : تفاصيل الميزانية : 100</div>"
        '<a href="/project/55">x</a></div>'
    )
    check("لينك نسبي بقى مطلق", bool(relative) and relative[0].link.startswith("https://"), 
          relative[0].link if relative else "لا يوجد")


def test_writer() -> None:
    print("\n[6] ai_writer (قالب)")
    proposal = generate_proposal(
        "متجر إلكتروني", "مطلوب متجر بلارافيل", "مستقل", "150", "5 أيام"
    )
    check("مش فاضي", bool(proposal.strip()))
    check("فيه السعر", "150" in proposal)
    check("فيه المدة", "5 أيام" in proposal)
    check("طول معقول", 100 < len(proposal) < 2000, str(len(proposal)))


def test_flow() -> None:
    """محاكاة حوار التقديم كامل من غير تيليجرام."""
    print("\n[7] حوار التقديم (محاكاة)")
    import asyncio

    import bot_interactive as bi

    class FakeMessage:
        def __init__(self, text: str = "") -> None:
            self.text = text
            self.sent: list[str] = []

        async def reply_text(self, text, **kwargs):
            self.sent.append(text)
            return self

    class FakeChat:
        id = 12345
        username = None

    class FakeQuery:
        def __init__(self, data: str) -> None:
            self.data = data
            self.message = FakeMessage()
            self.answered = False

        async def answer(self, *args, **kwargs):
            self.answered = True

    class FakeUpdate:
        def __init__(self, *, callback_query=None, message=None) -> None:
            self.callback_query = callback_query
            self.message = message
            self.effective_chat = FakeChat()
            self.effective_message = message or (callback_query.message if callback_query else None)

    class FakeContext:
        def __init__(self, chat_id: str, job) -> None:
            self.bot_data = {"chat_id": chat_id, "jobs": bi.JobCache()}
            self.bot_data["jobs"].put(job)
            self.user_data: dict = {}

    job = Job(
        platform="nafezly",
        raw_id="111",
        title="موقع لارافيل",
        link="https://nafezly.com/project/111",
        details="مطلوب موقع كامل بلارافيل",
        budget="100",
    )

    ctx = FakeContext("12345", job)
    query = FakeQuery("apply:nafezly:111")

    state = asyncio.run(bi.apply_start(FakeUpdate(callback_query=query), ctx))
    check("apply_start -> ASK_PRICE", state == bi.ASK_PRICE, str(state))
    check("البوت سأل عن السعر", "السعر" in " ".join(query.message.sent) or "العرض المالي" in " ".join(query.message.sent))

    state = asyncio.run(bi.got_price(FakeUpdate(message=FakeMessage("abc")), ctx))
    check("سعر غير رقمي -> إعادة السؤال", state == bi.ASK_PRICE, str(state))

    state = asyncio.run(bi.got_price(FakeUpdate(message=FakeMessage("150")), ctx))
    check("سعر -> ASK_DURATION", state == bi.ASK_DURATION, str(state))

    duration_message = FakeMessage("5 أيام")
    state = asyncio.run(bi.got_duration(FakeUpdate(message=duration_message), ctx))
    check("مدة -> CONFIRM", state == bi.CONFIRM, str(state))
    check("العرض اتولّد", bool(ctx.user_data.get("proposal")))
    check("رسالة المعاينة اتبعتت", any("السعر" in text for text in duration_message.sent))

    draft = database.get_application(job.id)
    check("مسودة محفوظة في الدي بي", draft is not None and draft["status"] == "draft", str(draft and draft["status"]))

    submit_query = FakeQuery("submit")
    state = asyncio.run(bi.submit_now(FakeUpdate(callback_query=submit_query), ctx))
    check("الإرسال بيرجع لـ CONFIRM مش crash", state == bi.CONFIRM, str(state))
    failed = database.get_application(job.id)
    check("الحالة بقت failed", failed is not None and failed["status"] == "failed", str(failed and failed["status"]))
    check("رسالة الخطأ فيها نص العرض لسه", any("انسخ" in text for text in submit_query.message.sent))


def main() -> int:
    test_database()
    test_common()
    test_khamsat()
    test_mostaql()
    test_nafezly()
    test_writer()
    test_flow()

    print("\n" + "=" * 50)
    for _suffix in ("", "-wal", "-shm"):
        Path(str(_TEST_DB) + _suffix).unlink(missing_ok=True)

    if failures:
        print(f"❌ فشل {len(failures)} اختبار:")
        for item in failures:
            print(f"   - {item}")
        return 1
    print("✅ كل الاختبارات نجحت")
    return 0


if __name__ == "__main__":
    sys.exit(main())
