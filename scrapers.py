"""واجهة موحّدة لكل المنصات.

البوت بيتعامل مع الموديولات دي من خلال المواصفات التالية:
    PLATFORM, LABEL, FILTER_BY_KEYWORDS, fetch_jobs(), enrich(job)
    واختياري: submit_proposal(job, price, duration, proposal) -> str

عشان تضيف منصة جديدة: اعمل ملف بنفس الواجهة، وضيفه في ALL_PLATFORMS.
"""

from __future__ import annotations

import logging
import os

import scraper_khamsat
import scraper_mostaql
import scraper_nafezly

logger = logging.getLogger("freelance-bot.scrapers")

ALL_PLATFORMS = {
    module.PLATFORM: module
    for module in (scraper_nafezly, scraper_mostaql, scraper_khamsat)
}


def enabled_platforms() -> list:
    """المنصات المفعّلة من متغير PLATFORMS (الافتراضي: الكل)."""
    raw = os.getenv("PLATFORMS") or ",".join(ALL_PLATFORMS)
    names = [name.strip() for name in raw.split(",") if name.strip()]

    selected = []
    for name in names:
        module = ALL_PLATFORMS.get(name)
        if module is None:
            logger.warning("منصة غير معروفة في PLATFORMS واتجاهلت: %s", name)
            continue
        selected.append(module)

    if not selected:
        logger.error("مفيش منصة مفعّلة! راجع PLATFORMS في .env")
    return selected


def get(platform: str):
    """الموديول بتاع منصة، أو None."""
    return ALL_PLATFORMS.get(platform)


def has_submitter(platform: str) -> bool:
    module = ALL_PLATFORMS.get(platform)
    return callable(getattr(module, "submit_proposal", None))
