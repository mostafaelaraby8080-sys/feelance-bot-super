"""نفذلي: القراءة من قناة تيليجرام العامة (بدون حظر).

الإصلاحات مقارنة بالنسخة القديمة:
  * اللينك بيتبني مطلق دايماً (كان ممكن يفضل نسبي فيفشل زرار تيليجرام للأبد).
  * تطبيع المسافات قبل الـ regex (كان سطر جديد واحد بيكسر المطابقة ويرمي المنشور).
  * لوج لما منشور فيه رابط مشروع بس العنوان مش مفهوم — عشان ما تفقدش مشاريع بصمت.
  * القناة من متغير بيئة NAFEZLY_CHANNEL.
"""
import logging
import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from common import MAX_DETAILS_LENGTH, Job, fetch_html

PLATFORM = "nafezly"
LABEL = "نفذلي"
FILTER_BY_KEYWORDS = True

BASE_URL = "https://nafezly.com"
CHANNEL_URL = os.getenv("NAFEZLY_CHANNEL", "https://t.me/s/Nafezly")
LIST_URLS = [CHANNEL_URL]

JOB_PATH_PATTERN = re.compile(r"/project/(\d+)")
TITLE_PATTERN = re.compile(r"عنوان المشروع\s*:\s*(.*?)\s*تفاصيل المشروع\s*:")
DETAILS_PATTERN = re.compile(
    r"تفاصيل المشروع\s*:\s*(.*?)"
    r"(?:\s*الميزانية\s*:|\s*مدة التنفيذ\s*:|\s*رابط المشروع\s*:|$)"
)
BUDGET_PATTERN = re.compile(
    r"الميزانية\s*:\s*(.*?)(?:\s*مدة التنفيذ\s*:|\s*رابط المشروع\s*:|$)"
)
DURATION_PATTERN = re.compile(r"مدة التنفيذ\s*:\s*(.*?)(?:\s*رابط المشروع\s*:|$)")

logger = logging.getLogger("freelance-bot.nafezly")


def _first_group(pattern: re.Pattern, text: str) -> str:
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _absolute_link(href: str) -> str:
    """لينك مطلق مضمون، أو "" لو مش صالح (عشان الزرار ما يرميش استثناء)."""
    candidate = urljoin(f"{BASE_URL}/", href.strip())
    if urlparse(candidate).scheme not in ("http", "https"):
        return ""
    if not urlparse(candidate).netloc:
        return ""
    return candidate


def parse_jobs(html: str) -> list[Job]:
    """استخراج المشاريع من منشورات القناة."""
    soup = BeautifulSoup(html, "html.parser")
    jobs: dict[str, Job] = {}

    for message in soup.select(".tgme_widget_message_wrap"):
        text_element = message.select_one(".tgme_widget_message_text")
        if not text_element:
            continue

        # توحيد المسافات: أي سطر جديد جوه المنشور كان بيكسر الـ regex
        text = re.sub(r"\s+", " ", text_element.get_text(" ", strip=True))

        job_id, link = None, ""
        for anchor in message.select("a[href]"):
            href = anchor.get("href", "")
            match = JOB_PATH_PATTERN.search(href)
            if not match:
                continue
            candidate = _absolute_link(href)
            if not candidate:
                continue
            job_id, link = match.group(1), candidate
            break

        if not job_id:
            match = JOB_PATH_PATTERN.search(text)
            if not match:
                continue
            job_id = match.group(1)
            link = f"{BASE_URL}/project/{job_id}"

        title = _first_group(TITLE_PATTERN, text)
        if not title:
            logger.warning(
                "[%s] منشور فيه مشروع %s بس العنوان مش مفهوم: %s",
                LABEL, job_id, text[:100],
            )
            continue

        jobs[job_id] = Job(
            platform=PLATFORM,
            raw_id=job_id,
            title=title,
            link=link,
            details=_first_group(DETAILS_PATTERN, text)[:MAX_DETAILS_LENGTH],
            budget=_first_group(BUDGET_PATTERN, text),
            duration=_first_group(DURATION_PATTERN, text),
        )

    return list(jobs.values())


def fetch_jobs() -> list[Job]:
    parsed = parse_jobs(fetch_html(CHANNEL_URL))
    if not parsed:
        logger.warning("[%s] مفيش مشاريع في القناة (%s) — راجع الرابط", LABEL, CHANNEL_URL)
    return parsed


def enrich(job: Job) -> None:
    """تفاصيل المنشور كاملة في القناة أصلاً، فمفيش حاجة إضافية نجيبها."""
    return None


def submit_proposal(job: Job, price: str, duration: str, proposal: str) -> str:
    """إرسال العرض على الموقع (محتاج جلسة محفوظة — شوف proposal_submitter)."""
    from proposal_submitter import submit

    return submit(PLATFORM, job.link, price, duration, proposal)
