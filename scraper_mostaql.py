"""مستقل: قسم البرمجة والتطوير.

الإصلاحات مقارنة بالنسخة القديمة:
  * `duration or ""` قبل `isdigit()` — كان بيموت لو الحقل مش موجود.
  * `details or ""` قبل `re.sub` — نفس الحكاية.
  * الـ regex بقى مثبت على نهاية المسار (ما يمسكش /apply).
  * جمع "أيام" صح مع 11+.
  * دالة submit_proposal لتقديم العرض من البوت.
"""
import logging
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from common import (
    MAX_DETAILS_LENGTH,
    Job,
    fetch_html,
    meta_description,
    page_lines,
    section_after,
    value_after,
)

PLATFORM = "mostaql"
LABEL = "مستقل"
# القسم نفسه برمجة، فمش محتاجين فلترة بالكلمات المفتاحية
FILTER_BY_KEYWORDS = False

BASE_URL = "https://mostaql.com"
LIST_URLS = ["https://mostaql.com/projects/development"]
JOB_PATH_PATTERN = re.compile(r"^/project/(\d+)/?$")

DETAILS_HEADING = "تفاصيل المشروع"
STOP_LABELS = {
    "حالة المشروع",
    "تاريخ النشر",
    "الميزانية",
    "مدة التنفيذ",
    "المهارات",
    "صاحب المشروع",
    "تقدم للمشروع",
    "العروض المقدمة",
}

logger = logging.getLogger("freelance-bot.mostaql")


def _path_of(href: str) -> str:
    path = urlparse(href).path
    if path and not path.startswith("/"):
        path = "/" + path
    return path


def parse_listing(html: str) -> list[Job]:
    """استخراج (id, title, link) من صفحة قائمة المشاريع."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, Job] = {}

    for anchor in soup.find_all("a", href=True):
        path = _path_of(anchor["href"])
        match = JOB_PATH_PATTERN.match(path)
        title = anchor.get_text(" ", strip=True)
        # نتجاهل الروابط اللي نصها مقتطف من التفاصيل مش العنوان
        if not match or not title or title.startswith(DETAILS_HEADING):
            continue

        job_id = match.group(1)
        if job_id not in jobs or len(title) > len(jobs[job_id].title):
            jobs[job_id] = Job(
                platform=PLATFORM,
                raw_id=job_id,
                title=title,
                link=urljoin(BASE_URL, path),
            )

    return list(jobs.values())


def fetch_jobs() -> list[Job]:
    jobs: dict[str, Job] = {}
    for url in LIST_URLS:
        parsed = parse_listing(fetch_html(url))
        if not parsed:
            logger.warning("[%s] القائمة رجعت 0 مشاريع (%s) — راجع الصفحة", LABEL, url)
        for job in parsed:
            jobs.setdefault(job.raw_id, job)
    return list(jobs.values())


def _format_duration(raw: str) -> str:
    """مستقل بيحسب المدة بالأيام، فنضيف كلمة اليوم بالشكل الصحيح."""
    value = (raw or "").strip()
    if not value.isdigit():
        return value

    days = int(value)
    if days == 1:
        word = "يوم"
    elif 2 <= days <= 10:
        word = "أيام"
    else:
        word = "يوم"
    return f"{days} {word}"


def parse_details(html: str) -> dict[str, str]:
    """استخراج العنوان والميزانية والمدة والتفاصيل من صفحة المشروع."""
    lines = page_lines(html)
    details = section_after(lines, DETAILS_HEADING, STOP_LABELS) or meta_description(html) or ""
    details = re.sub(rf"^{DETAILS_HEADING}\s*:?\s*", "", details).strip()

    h1 = BeautifulSoup(html, "html.parser").find("h1")
    return {
        "title": h1.get_text(" ", strip=True) if h1 else "",
        "details": details[:MAX_DETAILS_LENGTH],
        "budget": value_after(lines, "الميزانية"),
        "duration": _format_duration(value_after(lines, "مدة التنفيذ")),
    }


def enrich(job: Job) -> None:
    data = parse_details(fetch_html(job.link))
    if data["title"]:
        job.title = data["title"]
    job.details = data["details"]
    job.budget = data["budget"]
    job.duration = data["duration"]


def submit_proposal(job: Job, price: str, duration: str, proposal: str) -> str:
    """إرسال العرض على الموقع (محتاج جلسة محفوظة — شوف proposal_submitter)."""
    from proposal_submitter import submit

    return submit(PLATFORM, job.link, price, duration, proposal)
