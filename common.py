"""أدوات مشتركة: تعريف Job، جلب HTML مع إعادة المحاولة، واستخراج نصوص من الصفحات.

ملاحظة: لو عندك نسخة شغالة من الملف ده في مشروعك، راجع الفرق قبل ما تستبدلها.
الواجهة متوافقة مع اللي السكرابرز بتستورده:
    Job, MAX_DETAILS_LENGTH, fetch_html, page_lines, value_after,
    section_after, meta_description, first_text_block_after_title
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

MAX_DETAILS_LENGTH = int(os.getenv("MAX_DETAILS_LENGTH", "2000"))
REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))
REQUEST_MAX_RETRIES = int(os.getenv("REQUEST_MAX_RETRIES", "3"))
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

DEFAULT_USER_AGENT = os.getenv(
    "HTTP_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
)

logger = logging.getLogger("freelance-bot.http")

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept-Language": "ar,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
)


@dataclass
class Job:
    """مشروع واحد من أي منصة."""

    platform: str
    raw_id: str
    title: str
    link: str
    details: str = ""
    budget: str = ""
    duration: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        """معرّف موحّد: كده مستحيل يحصل تعارض بين منصتين عندهم نفس الرقم."""
        return f"{self.platform}:{self.raw_id}"


def fetch_html(url: str) -> str:
    """جلب صفحة HTML مع إعادة محاولة على أخطاء الشبكة و 429/5xx."""
    last_error: object = None

    for attempt in range(1, REQUEST_MAX_RETRIES + 1):
        try:
            response = _session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        except requests.RequestException as error:
            last_error = error
            logger.warning("فشل طلب %s (محاولة %d/%d): %s", url, attempt, REQUEST_MAX_RETRIES, error)
        else:
            if response.status_code in RETRY_STATUS_CODES:
                last_error = RuntimeError(f"HTTP {response.status_code}")
                logger.warning(
                    "الموقع رد %d على %s (محاولة %d/%d)",
                    response.status_code, url, attempt, REQUEST_MAX_RETRIES,
                )
            elif response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code} على {url}")
            else:
                response.encoding = response.apparent_encoding or response.encoding
                return response.text

        if attempt < REQUEST_MAX_RETRIES:
            time.sleep(min(2 ** attempt, 10))

    raise RuntimeError(f"تعذر جلب {url}: {last_error}")


def _normalize(text: str) -> str:
    """توحيد المسافات (بما فيها المسافة غير الفاصلة اللي بتكسر المطابقة العربية)."""
    return re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()


def _clean_value(text: str) -> str:
    """تنضيف القيمة من النقطتين والشرطات اللي الموقع بيحطها بعد اللابل."""
    return _normalize(text).strip(" :\u00a0-—–|").strip()


def page_lines(html: str) -> list[str]:
    """كل سطور النص في الصفحة، سطر لكل عنصر، من غير سكريبتات."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    lines: list[str] = []
    for raw in soup.get_text("\n").splitlines():
        line = _normalize(raw)
        if line:
            lines.append(line)
    return lines


def value_after(lines: list[str], label: str) -> str:
    """القيمة اللي بعد اللابل: نفس السطر أو السطر اللي بعده."""
    for index, line in enumerate(lines):
        position = line.find(label)
        if position == -1:
            continue

        rest = _clean_value(line[position + len(label):])
        if rest:
            return rest

        for following in lines[index + 1: index + 3]:
            cleaned = _clean_value(following)
            if cleaned:
                return cleaned

    return ""


def section_after(lines: list[str], heading: str, stop_labels=()) -> str:
    """محتوى قسم بيبدأ بعنوان وينتهي عند أول لابل من stop_labels."""
    normalized_stop = {_clean_value(label) for label in stop_labels}
    start = None

    for index, line in enumerate(lines):
        cleaned = _clean_value(line)
        if cleaned == heading or line.startswith(heading):
            start = index
            break

    if start is None:
        return ""

    collected: list[str] = []
    for line in lines[start + 1:]:
        if _clean_value(line) in normalized_stop:
            break
        collected.append(line)
        if len(" ".join(collected)) > MAX_DETAILS_LENGTH * 2:
            break

    return "\n".join(collected).strip()


def meta_description(html: str) -> str:
    """وصف الصفحة من الـ meta (احتياطي فقط — عادة وصف عام لكل الصفحات)."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    return _normalize(tag.get("content", "")) if tag else ""


def first_text_block_after_title(html: str, min_length: int = 40) -> str:
    """أول بلوك نصي معتبر بعد العنوان — أدق من meta_description."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template"]):
        tag.decompose()

    heading = soup.find("h1")
    if heading is None:
        return ""

    for element in heading.find_all_next(["p", "div", "article", "section"]):
        text = _normalize(element.get_text(" ", strip=True))
        if len(text) >= min_length:
            return text

    return ""
