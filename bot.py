"""الإعدادات: قراءة التوكن ومعرّف الشات من .env.

ملاحظة: الملف ده مش بيشغّل البوت — التشغيل في bot_interactive.py.
الاسم محتفظ بيه زي مشروعك الأصلي عشان `from bot import get_config` تفضل شغالة.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

# نحمّل .env من جنب الملف نفسه، مش من الـ cwd
load_dotenv(Path(__file__).resolve().parent / ".env")

logger = logging.getLogger("freelance-bot.config")

_TOKEN_PATTERN_HINT = r"^\d{5,}:[A-Za-z0-9_-]{20,}$"


def get_config() -> tuple[str, str]:
    """قراءة الإعدادات من متغيرات البيئة والتحقق من وجودها."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        raise ValueError(
            "تأكد من وجود TELEGRAM_BOT_TOKEN و TELEGRAM_CHAT_ID في ملف .env"
        )

    # تحقق شكلي من شكل التوكن — بيمنع أخطاء نسخ/لصق بدري
    import re

    if not re.fullmatch(_TOKEN_PATTERN_HINT, token):
        raise ValueError(
            "شكل TELEGRAM_BOT_TOKEN غلط. لازم يكون بالشكل: 123456789:AA..."
        )

    return token, chat_id
