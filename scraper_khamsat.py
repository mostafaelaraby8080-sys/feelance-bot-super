"""خمسات: طلبات المجتمع (محاولة قدر الإمكان، الموقع محمي ضد الأدوات الآلية).

الإصلاحات مقارنة بالنسخة القديمة:
  * الـ regex بقى مثبت على نهاية المسار (ما يمسكش /reply و /edit).
  * التفاصيل بتتاخد من نص الصفحة الأول، والميتا كـ fallback فقط
    (وصف الميتا عادة عام ونفسه لكل المشاريع، وده بيبوّظ فلتر الكلمات).
  * `or ""` عشان ما يحصلش TypeError لو الصفحة رجعت فاضية.
  * لوج واضح لما القائمة ترجع صفر مشاريع (غالباً بلوك أو تغيير في الصفحة).
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
    first_text_block_after_title,
    meta_description,
    page_lines,
    value_after,
)

PLATFORM = "khamsat"
LABEL = "خمسات"
FILTER_BY_KEYWORDS = True

BASE_URL = "https://khamsat.com"
LIST_URLS = [f"{BASE_URL}/community/requests"]
JOB_PATH_PATTERN = re.compile(r"^/community/requests/(\d+)/?$")

logger = logging.getLogger("freelance-bot.khamsat")


def _path_of(href: str) -> str:
    """مسار الرابط، مع ضمان إنه يبدأ بـ / عشان الـ regex يشتغل."""
    path = urlparse(href).path
    if path and not path.startswith("/"):
        path = "/" + path
    return path


def parse_listing(html: str) -> list[Job]:
    """استخراج (id, title, link) من صفحة طلبات المجتمع."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, Job] = {}

    for anchor in soup.find_all("a", href=True):
        path = _path_of(anchor["href"])
        match = JOB_PATH_PATTERN.match(path)
        title = anchor.get_text(" ", strip=True)
        if not match or not title:
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
            logger.warning(
                "[%s] القائمة رجعت 0 مشاريع (%s) — غالباً حجب أو تغيير في الصفحة",
                LABEL,
                url,
            )
        for job in parsed:
            jobs.setdefault(job.raw_id, job)
    return list(jobs.values())


def enrich(job: Job) -> None:
    html = fetch_html(job.link)
    lines = page_lines(html)
    # نص الصفحة الأول، والميتا fallback بس
    details = first_text_block_after_title(html) or meta_description(html) or ""
    job.details = details[:MAX_DETAILS_LENGTH]
    job.budget = value_after(lines, "الميزانية")
    job.duration = value_after(lines, "مدة التنفيذ")


def submit_proposal(job: Job, price: str, duration: str, proposal: str) -> str:
    """إرسال العرض على الموقع (محتاج جلسة محفوظة — شوف proposal_submitter)."""
    from proposal_submitter import submit

    return submit(PLATFORM, job.link, price, duration, proposal)
