"""البوت التفاعلي: فحص مستمر + إشعارات بأزرار + حوار تقديم العرض.

التشغيل:
    python bot_interactive.py           # البوت الكامل (polling + فحص دوري)
    python bot_interactive.py --once    # دورة فحص واحدة بدون polling (للاختبار)

المتطلبات:
    pip install "python-telegram-bot[job-queue]>=20.7"

ملفات المشروع:
    common.py             — Job + fetch_html + أدوات الاستخراج
    database.py           — SQLite: المشاريع اللي اتشافت + العروض
    scrapers.py           — واجهة موحدة للمنصات
    scraper_*.py          — منصة لكل ملف
    ai_writer.py          — توليد نص العرض (LLM أو قالب)
    proposal_submitter.py — إرسال العرض على الموقع (http أو playwright)
    bot.py                — get_config فقط
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from typing import TYPE_CHECKING

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from ai_writer import generate_proposal
from bot import get_config
from database import (
    count_seen_jobs,
    has_sent_application,
    init_db,
    is_job_seen,
    mark_job_seen,
    save_application,
    set_application_status,
    stats,
)
from scrapers import enabled_platforms, get as get_platform

if TYPE_CHECKING:
    from telegram.ext import Context

    from common import Job

# --------------------------------------------------------------------------
# إعدادات
# --------------------------------------------------------------------------
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_MINUTES", "5")) * 60
MAX_JOBS_PER_PLATFORM = int(os.getenv("MAX_JOBS_PER_PLATFORM", "5"))
DELAY_BETWEEN_JOBS_SECONDS = 3
MAX_DETAILS_IN_MESSAGE = 600
TELEGRAM_LIMIT = 4000
JOB_CACHE_TTL_SECONDS = 7 * 24 * 3600
CONVERSATION_TIMEOUT_SECONDS = 15 * 60

# المشاريع اللي مفيهاش أي كلمة من دي هتتجاهل (للمنصات اللي فلترتها شغالة).
KEYWORDS = [
    "برمج", "موقع", "مواقع", "تطبيق", "ويب", "متجر", "سكريبت", "بوت",
    "قاعدة بيانات", "لارافيل", "ووردبريس", "فرونت", "باك اند", "html",
    "api", "python", "react", "node", "laravel", "wordpress", "next.js",
]

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
# مهم: لوج httpx بيطبع رابط الطلب وفيه توكن البوت
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("freelance-bot")


# --------------------------------------------------------------------------
# كاش المشاريع (عشان الـ callback يرجع الـ Job بعد الإشعار)
# --------------------------------------------------------------------------
class JobCache:
    """كاش في الذاكرة: job.id -> Job، مع صلاحية زمنية."""

    def __init__(self, ttl_seconds: int = JOB_CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._items: dict[str, tuple[float, "Job"]] = {}

    def put(self, job: "Job") -> None:
        self._items[job.id] = (time.monotonic(), job)

    def get(self, job_id: str) -> "Job | None":
        item = self._items.get(job_id)
        if item is None:
            return None
        stored_at, job = item
        if time.monotonic() - stored_at > self._ttl:
            self._items.pop(job_id, None)
            return None
        return job

    def prune(self) -> None:
        now = time.monotonic()
        for key in [k for k, (ts, _) in self._items.items() if now - ts > self._ttl]:
            self._items.pop(key, None)


# --------------------------------------------------------------------------
# أدوات مساعدة
# --------------------------------------------------------------------------
def is_relevant(job: "Job", module) -> bool:
    """هل المشروع في مجالك؟ (فلترة بالكلمات المفتاحية)."""
    if not module.FILTER_BY_KEYWORDS or not KEYWORDS:
        return True
    text = f"{job.title} {job.details}".lower()
    for keyword in KEYWORDS:
        if keyword.isascii():
            # الكلمات الإنجليزية لازم تكون كلمة كاملة (عشان api ما تطابقش capital)
            if re.search(rf"(?<![a-z]){re.escape(keyword)}(?![a-z])", text):
                return True
        elif keyword in text:
            return True
    return False


def format_message(job: "Job", proposal: str, label: str) -> str:
    """تجهيز نص الإشعار."""
    lines = [
        f"🔔 مشروع جديد على {label}",
        "",
        f"📌 العنوان: {job.title}",
    ]
    if job.budget:
        lines.append(f"💰 الميزانية: {job.budget}")
    if job.duration:
        lines.append(f"⏱ المدة: {job.duration}")
    lines.append(f"🌐 المنصة: {label}")
    if job.details:
        lines += ["", f"📝 التفاصيل:\n{job.details[:MAX_DETAILS_IN_MESSAGE]}"]
    lines += ["", f"✍️ عرض مقترح:\n{proposal}", "", "👇 اضغط «قدم على الوظيفة» عشان تحدد سعرك ومدة التسليم."]
    return "\n".join(lines)[:TELEGRAM_LIMIT]


def build_keyboard(job: "Job") -> InlineKeyboardMarkup:
    """زرارين: فتح المشروع + بدء حوار التقديم."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 افتح المشروع", url=job.link)],
            [InlineKeyboardButton("📝 قدم على الوظيفة", callback_data=f"apply:{job.id}")],
        ]
    )


# --------------------------------------------------------------------------
# الفحص الدوري
# --------------------------------------------------------------------------
def seed_platform(module) -> None:
    """أول تشغيل لمنصة: نسجل المشاريع الحالية بدون إرسال (عشان ما نغرقكش)."""
    jobs = module.fetch_jobs()
    for job in jobs:
        mark_job_seen(job.id, job.title, module.PLATFORM)
    logger.info("[%s] أول تشغيل: تم تسجيل %d مشروع بدون إرسال", module.LABEL, len(jobs))


def collect_new_jobs(module) -> list:
    """المشاريع الجديدة فقط (بحد أقصى لكل دورة)."""
    jobs = module.fetch_jobs()
    new_jobs = [job for job in jobs if not is_job_seen(job.id)]
    return new_jobs[:MAX_JOBS_PER_PLATFORM]


async def process_job(
    bot: Bot, chat_id: str, module, job: "Job", cache: JobCache
) -> None:
    """معالجة مشروع واحد: تفاصيل، فلترة، عرض، إرسال، تسجيل."""
    try:
        await asyncio.to_thread(module.enrich, job)
    except Exception as error:
        logger.warning("[%s] تعذر جلب التفاصيل (%s)، هكمل بالمتاح", module.LABEL, error)

    if not is_relevant(job, module):
        mark_job_seen(job.id, job.title, module.PLATFORM)
        logger.info("[%s] تجاهل (خارج المجال): %s", module.LABEL, job.title)
        return

    proposal = await asyncio.to_thread(
        generate_proposal,
        job.title,
        job.details,
        module.LABEL,
        job.budget,
        job.duration,
    )

    # نحفظ المشروع قبل الإرسال عشان الزرار يلاقيه
    cache.put(job)

    await bot.send_message(
        chat_id=chat_id,
        text=format_message(job, proposal, module.LABEL),
        reply_markup=build_keyboard(job),
        disable_web_page_preview=True,  # TODO: deprecated في PTB 21+
    )
    # التسجيل بعد نجاح الإرسال فقط، عشان لو فشل نحاول تاني في الدورة الجاية
    mark_job_seen(job.id, job.title, module.PLATFORM)
    logger.info("[%s] تم الإرسال: %s", module.LABEL, job.title)


async def check_platform(bot: Bot, chat_id: str, module, cache: JobCache) -> None:
    """فحص منصة واحدة."""
    if count_seen_jobs(module.PLATFORM) == 0:
        await asyncio.to_thread(seed_platform, module)
        return

    jobs = await asyncio.to_thread(collect_new_jobs, module)
    logger.info("[%s] مشاريع جديدة: %d", module.LABEL, len(jobs))

    for index, job in enumerate(jobs):
        try:
            await process_job(bot, chat_id, module, job, cache)
        except RetryAfter as error:
            # تيليجرام طالب استنى: منسجّلش المشروع فهيتعاد في الدورة الجاية
            wait_for = int(error.retry_after) + 1
            logger.warning("[%s] rate limit، هستنى %d ثانية", module.LABEL, wait_for)
            await asyncio.sleep(wait_for)
        except TelegramError as error:
            logger.error("[%s] فشل الإرسال على تيليجرام: %s", module.LABEL, error)
        except Exception as error:
            logger.error("[%s] فشلت معالجة %s: %s", module.LABEL, job.id, error)

        if index < len(jobs) - 1:
            await asyncio.sleep(DELAY_BETWEEN_JOBS_SECONDS)


async def run_cycle(context: "Context") -> None:
    """دورة فحص لكل المنصات (فشل منصة ما بيوقفش الباقي)."""
    bot = context.bot
    chat_id = context.bot_data["chat_id"]
    cache: JobCache = context.bot_data["jobs"]
    cache.prune()

    for module in enabled_platforms():
        try:
            await check_platform(bot, chat_id, module, cache)
        except Exception as error:
            logger.error("[%s] فشل الفحص: %s", module.LABEL, error, exc_info=True)


# --------------------------------------------------------------------------
# حوار التقديم: سعر -> مدة -> معاينة وتأكيد
# --------------------------------------------------------------------------
ASK_PRICE, ASK_DURATION, CONFIRM = range(3)

_PRICE_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _chat_is_authorized(update: Update, expected_chat_id: str) -> bool:
    """البوت يرد بس على الشات المتفق عليه في .env."""
    chat = update.effective_chat
    if chat is None:
        return False
    if str(chat.id) == str(expected_chat_id):
        return True
    if chat.username and f"@{chat.username}".lower() == str(expected_chat_id).lower():
        return True
    return False


def _short_title(job: "Job") -> str:
    title = (job.title or "").strip()
    return title if len(title) <= 120 else title[:117] + "..."


async def apply_start(update: Update, context: "Context") -> int:
    """الزرار اتضغط: نبدأ الحوار."""
    query = update.callback_query
    await query.answer()

    chat_id = context.bot_data["chat_id"]
    if not _chat_is_authorized(update, chat_id):
        return ConversationHandler.END

    job_id = (query.data or "").split(":", 1)[1]
    job = context.bot_data["jobs"].get(job_id)
    if job is None:
        await query.message.reply_text(
            "⚠️ المشروع ده انتهت صلاحيته أو البوت اتعمل له restart.\n"
            "استنى الإشعار الجديد."
        )
        return ConversationHandler.END

    if await asyncio.to_thread(has_sent_application, job.id):
        await query.message.reply_text("✅ إنت قدمت على المشروع ده قبل كده.")
        return ConversationHandler.END

    context.user_data["job"] = job
    context.user_data.pop("price", None)
    context.user_data.pop("duration", None)
    context.user_data.pop("proposal", None)

    await query.message.reply_text(
        f"💰 ما هو العرض المالي اللي عايز تقدمه؟\n«{_short_title(job)}»\n\n"
        "اكتب رقم بس (مثلاً: 150).\nللإلغاء: /cancel"
    )
    return ASK_PRICE


async def got_price(update: Update, context: "Context") -> int:
    text = (update.message.text or "").strip()
    if not _PRICE_RE.fullmatch(text):
        await update.message.reply_text("اكتب رقم بس من غير حروف، مثلاً: 150\nأو /cancel")
        return ASK_PRICE

    context.user_data["price"] = text
    await update.message.reply_text("⏳ كم يوماً هتحتاج لتنفيذ المشروع؟\nمثلاً: 5 أيام\nأو /cancel")
    return ASK_DURATION


async def got_duration(update: Update, context: "Context") -> int:
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("اكتب المدة، مثلاً: 5 أيام\nأو /cancel")
        return ASK_DURATION

    context.user_data["duration"] = text
    await update.message.reply_text("⏳ بجهّز العرض...")
    await send_preview(update, context)
    return CONFIRM


async def send_preview(update: Update, context: "Context") -> None:
    """توليد العرض وعرضه مع أزرار التأكيد."""
    message = update.effective_message
    job = context.user_data.get("job")
    price = context.user_data.get("price")
    duration = context.user_data.get("duration")

    if message is None or job is None or not price or not duration:
        if message is not None:
            await message.reply_text("⚠️ ضاع السياق. ابدأ من جديد بالزرار في الإشعار.")
        return

    module = get_platform(job.platform)
    label = module.LABEL if module else job.platform

    try:
        proposal = await asyncio.to_thread(
            generate_proposal, job.title, job.details or "", label, price, duration
        )
    except Exception as error:
        logger.exception("فشل توليد العرض")
        await message.reply_text(f"⚠️ حصل خطأ وأنا بجهّز العرض: {error}\nجرّب /cancel وابدأ تاني.")
        return

    context.user_data["proposal"] = proposal

    # نسجّل مسودة العرض في الداتابيز قبل أي إرسال
    await asyncio.to_thread(
        save_application,
        job.id,
        platform=job.platform,
        title=job.title,
        link=job.link,
        price=price,
        duration=duration,
        proposal=proposal,
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🚀 إرسال للموقع الآن", callback_data="submit")],
            [InlineKeyboardButton("🔗 افتح المشروع والصق العرض", url=job.link)],
            [
                InlineKeyboardButton("🔄 عرض تاني", callback_data="regen"),
                InlineKeyboardButton("❌ إلغاء", callback_data="cancel"),
            ],
        ]
    )
    text = (
        f"✍️ العرض — {label}\n\n"
        f"{proposal}\n\n"
        f"💰 السعر: {price}\n"
        f"⏳ المدة: {duration}\n\n"
        "هل تريد إرساله للموقع الآن؟"
    )
    await message.reply_text(
        text[:TELEGRAM_LIMIT],
        reply_markup=keyboard,
        disable_web_page_preview=True,  # TODO: deprecated في PTB 21+
    )


async def regen(update: Update, context: "Context") -> int:
    query = update.callback_query
    await query.answer("بجهّز نسخة تانية...")
    await send_preview(update, context)
    return CONFIRM


async def submit_now(update: Update, context: "Context") -> int:
    """إرسال العرض للموقع."""
    query = update.callback_query
    await query.answer("بحاول أرسل...")

    job = context.user_data.get("job")
    price = context.user_data.get("price")
    duration = context.user_data.get("duration")
    proposal = context.user_data.get("proposal")

    if job is None or not price or not duration or not proposal:
        await query.message.reply_text("⚠️ ضاع السياق. ابدأ من جديد بالزرار في الإشعار.")
        return ConversationHandler.END

    module = get_platform(job.platform)
    submitter = getattr(module, "submit_proposal", None)
    if submitter is None:
        await query.message.reply_text("المنصة دي مش بتدعم الإرسال التلقائي حالياً.")
        return CONFIRM

    await query.message.reply_text("🚀 بحاول أرسل العرض على الموقع...")

    try:
        result = await asyncio.to_thread(submitter, job, price, duration, proposal)
    except Exception as error:
        logger.exception("[%s] فشل إرسال العرض %s", job.platform, job.id)
        await asyncio.to_thread(set_application_status, job.id, "failed", str(error)[:500])
        await query.message.reply_text(
            f"❌ فشل الإرسال: {error}\n\n"
            "العرض لسه فوق — انسخه وقدم بيه يدوي عشان ما يضيعش المشروع."
        )
        return CONFIRM

    await asyncio.to_thread(set_application_status, job.id, "sent")
    await query.message.reply_text(f"✅ {result}")
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: "Context") -> int:
    context.user_data.clear()
    query = update.callback_query
    if query is not None:
        await query.answer()
        if query.message is not None:
            await query.message.reply_text("❌ اتلغى.")
    elif update.message is not None:
        await update.message.reply_text("❌ اتلغى.")
    return ConversationHandler.END


def build_proposal_flow() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(apply_start, pattern=r"^apply:")],
        states={
            ASK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_price)],
            ASK_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, got_duration)],
            CONFIRM: [
                CallbackQueryHandler(submit_now, pattern=r"^submit$"),
                CallbackQueryHandler(regen, pattern=r"^regen$"),
                CallbackQueryHandler(cancel, pattern=r"^cancel$"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        conversation_timeout=CONVERSATION_TIMEOUT_SECONDS,
    )


# --------------------------------------------------------------------------
# أوامر
# --------------------------------------------------------------------------
async def cmd_start(update: Update, context: "Context") -> None:
    chat_id = context.bot_data["chat_id"]
    if not _chat_is_authorized(update, chat_id):
        return
    await update.message.reply_text(
        "👋 البوت شغال.\n\n"
        "هيبعتلك إشعار لكل مشروع جديد، وفيه زرارين:\n"
        "🔗 افتح المشروع\n"
        "📝 قدم على الوظيفة — يسألك السعر والمدة ويجهّز العرض\n\n"
        "/status — حالة المنصات والعروض\n"
        "/cancel — إلغاء الحوار الحالي"
    )


async def cmd_status(update: Update, context: "Context") -> None:
    chat_id = context.bot_data["chat_id"]
    if not _chat_is_authorized(update, chat_id):
        return

    lines = ["📊 الحالة:"]
    for module in enabled_platforms():
        try:
            count = await asyncio.to_thread(count_seen_jobs, module.PLATFORM)
        except Exception as error:
            count = f"? ({error})"
        lines.append(f"• {module.LABEL}: {count} مشروع مسجّل")

    try:
        data = await asyncio.to_thread(stats)
        applications = data.get("applications", {})
        lines.append("")
        lines.append(f"📨 عروض: {applications.get('sent', 0)} مرسل، "
                     f"{applications.get('failed', 0)} فشل، "
                     f"{applications.get('draft', 0)} مسودة")
    except Exception as error:
        lines.append(f"⚠️ تعذر قراءة الإحصائيات: {error}")

    await update.message.reply_text("\n".join(lines))


async def on_error(update: object, context: "Context") -> None:
    logger.error("خطأ غير متوقع", exc_info=context.error)


async def on_startup(app: Application) -> None:
    chat_id = app.bot_data["chat_id"]
    names = "، ".join(module.LABEL for module in enabled_platforms())
    await app.bot.send_message(chat_id=chat_id, text=f"🚀 بدأت مراقبة: {names}")


# --------------------------------------------------------------------------
# نقطة التشغيل
# --------------------------------------------------------------------------
def main() -> None:
    token, chat_id = get_config()
    init_db()

    app = ApplicationBuilder().token(token).build()
    app.bot_data["chat_id"] = chat_id
    app.bot_data["jobs"] = JobCache()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(build_proposal_flow())
    app.add_error_handler(on_error)
    app.post_init = on_startup

    if app.job_queue is None:
        raise RuntimeError(
            "job_queue مش متاح. ثبّت: pip install \"python-telegram-bot[job-queue]\""
        )
    app.job_queue.run_repeating(
        run_cycle, interval=CHECK_INTERVAL_SECONDS, first=5, name="scan"
    )

    logger.info("البوت شغال... (فحص كل %d ثانية)", CHECK_INTERVAL_SECONDS)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


async def run_once() -> None:
    """دورة فحص واحدة بدون polling — للتجربة."""
    token, chat_id = get_config()
    init_db()
    async with Bot(token=token) as bot:
        cache = JobCache()
        for module in enabled_platforms():
            try:
                await check_platform(bot, chat_id, module, cache)
            except Exception as error:
                logger.error("[%s] فشل الفحص: %s", module.LABEL, error, exc_info=True)


if __name__ == "__main__":
    try:
        if "--once" in sys.argv:
            asyncio.run(run_once())
        else:
            main()
    except KeyboardInterrupt:
        logger.info("تم إيقاف البوت")
