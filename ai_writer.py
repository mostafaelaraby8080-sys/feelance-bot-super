"""توليد نص العرض.

السلوك:
  * لو OPENAI_API_KEY موجود → بيستخدم أي API متوافق مع OpenAI (OPENAI_BASE_URL).
  * لو مفيش مفتاح أو حصل خطأ → بيرجع قالب جاهز (فالبوت يشتغل من غير أي مفتاح).

بيتندّه من asyncio.to_thread، فكل الدوال هنا متزامنة (sync).
"""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger("freelance-bot.writer")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
REQUEST_TIMEOUT_SECONDS = int(os.getenv("AI_TIMEOUT_SECONDS", "45"))

SYSTEM_PROMPT = (
    "أنت مستقل عربي محترف في البرمجة وتطوير المواقع. "
    "اكتب عرضاً (proposal) قصيراً ومقنعاً لصاحب مشروع. "
    "الشروط: بالعربية الفصحى، من 80 إلى 140 كلمة، بدون مقدمات عامة، "
    "اذكر فهمك للمطلوب، وخطة تنفيذ مختصرة، والسعر والمدة المذكورين، "
    "واختم بسؤال واحد يفتح الحوار. ممنوع أي حشو أو مبالغة."
)


def _template(
    title: str,
    details: str,
    platform_label: str,
    budget: str = "",
    duration: str = "",
) -> str:
    """قالب احتياطي مقبول — بيتستخدم لو مفيش API أو فشل الطلب."""
    problem = (details or "").strip()
    if problem:
        problem = problem[:280].rsplit(" ", 1)[0] + ("…" if len(details) > 280 else "")
        summary = f"قرأت تفاصيل المشروع ({problem})، وفاهم المطلوب كويس."
    else:
        summary = "قرأت تفاصيل المشروع وفاهم المطلوب كويس."

    terms: list[str] = []
    if budget:
        terms.append(f"السعر: {budget}")
    if duration:
        terms.append(f"مدة التنفيذ: {duration}")
    terms_line = ("\n" + " | ".join(terms)) if terms else ""

    return (
        f"السلام عليكم،\n\n"
        f"بخصوص «{title}» على {platform_label}:\n"
        f"{summary}\n\n"
        f"خطة التنفيذ:\n"
        f"1) تحليل المتطلبات وتثبيت النطاق قبل البدء.\n"
        f"2) تنفيذ المرحلة الأولى وتسليمها للمراجعة.\n"
        f"3) التعديلات النهائية والتسليم مع شرح طريقة الاستخدام.\n"
        f"{terms_line}\n\n"
        f"جاهز أبدأ فوراً، وأقدر أديك موعد تسليم مرحلة أولى خلال أول يومين.\n"
        f"هل تحب نبدأ بتفاصيل النطاق؟"
    )


def _via_llm(
    title: str,
    details: str,
    platform_label: str,
    budget: str = "",
    duration: str = "",
) -> str:
    user_prompt = (
        f"المنصة: {platform_label}\n"
        f"عنوان المشروع: {title}\n"
        f"السعر المقترح: {budget or 'غير محدد'}\n"
        f"المدة المقترحة: {duration or 'غير محدد'}\n\n"
        f"تفاصيل المشروع:\n{(details or '')[:1500]}"
    )

    response = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": OPENAI_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.6,
            "max_tokens": 400,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"].strip()
    if not content:
        raise ValueError("رد فارغ من الموديل")
    return content


def generate_proposal(
    title: str,
    details: str,
    platform_label: str,
    budget: str = "",
    duration: str = "",
) -> str:
    """توليد العرض، مع رجوع آمن للقالب عند أي فشل."""
    if OPENAI_API_KEY:
        try:
            return _via_llm(title, details, platform_label, budget, duration)
        except Exception as error:
            logger.warning("فشل توليد العرض بالموديل (%s) — هستخدم القالب", error)

    return _template(title, details, platform_label, budget, duration)
