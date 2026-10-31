"""إرسال العرض على الموقع.

فيه باكندين، تختار بينهم بـ SUBMIT_BACKEND في .env:

    SUBMIT_BACKEND=http         → requests.post على API جاهز (سريع وبسيط)
    SUBMIT_BACKEND=playwright   → متصفح حقيقي (بيتعامل مع JavaScript و CSRF)

http:
    SUBMIT_API_URL=https://example.com/api/apply
    SUBMIT_API_TOKEN=...

playwright (مرة واحدة لكل منصة):
    pip install playwright
    playwright install chromium
    python proposal_submitter.py --login khamsat

للتأكد من الـ selectors قبل أي إرسال حقيقي:
    python proposal_submitter.py --inspect "https://khamsat.com/community/requests/123"
    python proposal_submitter.py --dry-run khamsat "https://khamsat.com/community/requests/123" 150 "5 أيام"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

SESSIONS_DIR = Path(os.getenv("SESSIONS_DIR", ".sessions"))
SCREENSHOTS_DIR = Path(os.getenv("SCREENSHOTS_DIR", "screenshots"))

SUBMIT_BACKEND = os.getenv("SUBMIT_BACKEND", "playwright").strip().lower()
SUBMIT_API_URL = os.getenv("SUBMIT_API_URL", "").strip()
SUBMIT_API_TOKEN = os.getenv("SUBMIT_API_TOKEN", "").strip()
SUBMIT_HTTP_TIMEOUT = int(os.getenv("SUBMIT_HTTP_TIMEOUT", "45"))
SUBMIT_HEADLESS = os.getenv("SUBMIT_HEADLESS", "1") == "1"
NAVIGATION_TIMEOUT_MS = int(os.getenv("SUBMIT_NAV_TIMEOUT_MS", "60000"))

# لكل منصة: صفحة الدخول، الحقول (بأكثر من احتمال)، وزرار الإرسال.
# أي قائمة بتتجرب بالترتيب لحد ما أول عنصر ظاهر يشتغل.
PLATFORMS: dict[str, dict[str, Any]] = {
    "khamsat": {
        "login_url": "https://khamsat.com/",
        "open_form": [("text", "أضف عرض"), ("text", "تقديم عرض")],
        "price": [("css", "#price"), ("name", "price"), ("label", "السعر")],
        "duration": [("css", "#days"), ("name", "days"), ("label", "المدة")],
        "proposal": [
            ("css", "textarea[name='description']"),
            ("name", "description"),
            ("css", "textarea"),
        ],
        "submit": [("role", "button", "إرسال"), ("css", "button[type='submit']")],
        "success_text": ["تم إرسال عرضك", "تم إضافة عرضك", "عرضك"],
    },
    "mostaql": {
        "login_url": "https://mostaql.com/",
        "open_form": [("text", "تقديم عرض"), ("text", "قدّم عرض")],
        "price": [("name", "amount"), ("css", "#amount"), ("label", "المبلغ")],
        "duration": [("name", "period"), ("css", "#period"), ("label", "المدة")],
        "proposal": [("name", "comment"), ("css", "textarea"), ("label", "العرض")],
        "submit": [("role", "button", "تقديم"), ("css", "button[type='submit']")],
        "success_text": ["تم تقديم عرضك", "تم إرسال عرضك"],
    },
    "nafezly": {
        "login_url": "https://nafezly.com/",
        "open_form": [("text", "قدم عرض"), ("text", "تقديم عرض")],
        "price": [("name", "price"), ("label", "السعر")],
        "duration": [("name", "duration"), ("label", "المدة")],
        "proposal": [("css", "textarea"), ("name", "details")],
        "submit": [("role", "button", "إرسال"), ("css", "button[type='submit']")],
        "success_text": ["تم إرسال", "تم تقديم"],
    },
}


class SubmissionError(RuntimeError):
    """خطأ مفهوم للمستخدم (بيتعرض له في تيليجرام)."""


# --------------------------------------------------------------------------
# الباكند: HTTP
# --------------------------------------------------------------------------
def _http_submit(platform: str, job_url: str, price: str, duration: str, proposal: str) -> str:
    if not SUBMIT_API_URL:
        raise SubmissionError(
            "SUBMIT_BACKEND=http بس SUBMIT_API_URL فاضي في .env — حط رابط الـ API."
        )

    payload = {
        "platform": platform,
        "job_url": job_url,
        "price": price,
        "duration": duration,
        "proposal": proposal,
    }
    headers = {"Accept": "application/json"}
    if SUBMIT_API_TOKEN:
        headers["Authorization"] = f"Bearer {SUBMIT_API_TOKEN}"

    try:
        response = requests.post(
            SUBMIT_API_URL, json=payload, headers=headers, timeout=SUBMIT_HTTP_TIMEOUT
        )
    except requests.RequestException as error:
        raise SubmissionError(f"تعذر الوصول للـ API: {error}") from error

    if response.status_code >= 400:
        raise SubmissionError(
            f"الـ API رد {response.status_code}: {response.text[:300]}"
        )

    try:
        data = response.json()
    except ValueError:
        return f"تم الإرسال (رد غير JSON): {response.text[:200]}"

    if isinstance(data, dict) and data.get("url"):
        return f"تم الإرسال ✅ — {data['url']}"
    return "تم الإرسال ✅"


# --------------------------------------------------------------------------
# الباكند: المتصفح
# --------------------------------------------------------------------------
def _session_path(platform: str) -> Path:
    return SESSIONS_DIR / f"{platform}.json"


def _screenshot(page, name: str) -> str:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = SCREENSHOTS_DIR / f"{name}-{stamp}.png"
    page.screenshot(path=str(path), full_page=True)
    return str(path)


def _resolve(page, candidate: tuple):
    kind = candidate[0]
    if kind == "css":
        return page.locator(candidate[1])
    if kind == "id":
        return page.locator(f"#{candidate[1]}")
    if kind == "name":
        return page.locator(f"[name='{candidate[1]}']")
    if kind == "label":
        return page.get_by_label(candidate[1])
    if kind == "placeholder":
        return page.get_by_placeholder(candidate[1])
    if kind == "text":
        return page.get_by_text(candidate[1])
    if kind == "role":
        return page.get_by_role(candidate[1], name=candidate[2])
    raise SubmissionError(f"نوع selector غير معروف: {kind}")


def _first_visible(page, candidates: list, timeout: int = 4000):
    for candidate in candidates:
        try:
            locator = _resolve(page, candidate).first
            locator.wait_for(state="visible", timeout=timeout)
            return locator
        except Exception:
            continue
    return None


def _fill(page, candidates: list, value: str, field_name: str) -> None:
    locator = _first_visible(page, candidates)
    if locator is None:
        raise SubmissionError(
            f"مش لاقي حقل «{field_name}». جرّب: python proposal_submitter.py --inspect <رابط المشروع>"
        )
    locator.fill(str(value))


def _click(page, candidates: list, name: str, required: bool = True) -> bool:
    locator = _first_visible(page, candidates)
    if locator is None:
        if required:
            raise SubmissionError(
                f"مش لاقي «{name}». جرّب: python proposal_submitter.py --inspect <رابط المشروع>"
            )
        return False
    locator.click()
    return True


def _browser_submit(platform: str, job_url: str, price: str, duration: str, proposal: str) -> str:
    config = PLATFORMS.get(platform)
    if config is None:
        raise SubmissionError(f"منصة غير مدعومة للإرسال: {platform}")

    session_file = _session_path(platform)
    if not session_file.exists():
        raise SubmissionError(
            f"مفيش جلسة محفوظة لـ {platform}.\n"
            f"شغّل مرة واحدة: python proposal_submitter.py --login {platform}"
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SubmissionError(
            "Playwright مش متثبت. شغّل: pip install playwright && playwright install chromium"
        ) from error

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=SUBMIT_HEADLESS)
        context = browser.new_context(
            storage_state=str(session_file),
            locale="ar-EG",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        page.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)

        try:
            page.goto(job_url, wait_until="domcontentloaded")
            _click(page, config["open_form"], "زرار فتح فورم التقديم", required=False)
            _fill(page, config["price"], price, "السعر")
            _fill(page, config["duration"], duration, "المدة")
            _fill(page, config["proposal"], proposal, "نص العرض")
            _click(page, config["submit"], "زرار الإرسال", required=True)
            page.wait_for_timeout(5000)

            shot = _screenshot(page, f"sent-{platform}")
            for marker in config.get("success_text", []):
                try:
                    if page.get_by_text(marker, exact=False).count() > 0:
                        return f"تم الإرسال ✅ (تأكيد الصفحة: {marker})\nصورة: {shot}"
                except Exception:
                    continue

            return (
                "دوّست على «إرسال» بس مش لاقي رسالة تأكيد على الصفحة — "
                f"راجع بنفسك قبل ما تعتمد عليه.\nصورة: {shot}"
            )
        finally:
            context.close()
            browser.close()


# --------------------------------------------------------------------------
# الواجهة
# --------------------------------------------------------------------------
def submit(
    platform: str,
    job_url: str,
    price: str,
    duration: str,
    proposal: str,
    *,
    dry_run: bool = False,
) -> str:
    """إرسال العرض. بيرفع SubmissionError برسالة عربية مفهومة."""
    if not job_url.startswith(("http://", "https://")):
        raise SubmissionError(f"رابط المشروع غير صالح: {job_url!r}")

    if SUBMIT_BACKEND == "http":
        if dry_run:
            return "dry-run: الباكند http — مبعتتش حاجة."
        return _http_submit(platform, job_url, price, duration, proposal)

    if dry_run:
        return _dry_run_browser(platform, job_url, price, duration, proposal)
    return _browser_submit(platform, job_url, price, duration, proposal)


def _dry_run_browser(platform: str, job_url: str, price: str, duration: str, proposal: str) -> str:
    """يملأ الفورم ويصوّر من غير ما يدوس إرسال."""
    config = PLATFORMS.get(platform)
    if config is None:
        raise SubmissionError(f"منصة غير مدعومة: {platform}")

    session_file = _session_path(platform)
    if not session_file.exists():
        raise SubmissionError(
            f"مفيش جلسة لـ {platform}. شغّل: python proposal_submitter.py --login {platform}"
        )

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(storage_state=str(session_file), locale="ar-EG")
        page = context.new_page()
        try:
            page.goto(job_url, wait_until="domcontentloaded")
            _click(page, config["open_form"], "زرار فتح فورم التقديم", required=False)
            _fill(page, config["price"], price, "السعر")
            _fill(page, config["duration"], duration, "المدة")
            _fill(page, config["proposal"], proposal, "نص العرض")
            shot = _screenshot(page, f"dryrun-{platform}")
            return f"تجربة بدون إرسال — الفورم اتعبّى. صورة: {shot}"
        finally:
            context.close()
            browser.close()


def login(platform: str) -> None:
    """فتح متصفح لتسجيل الدخول يدوياً وحفظ الجلسة."""
    config = PLATFORMS.get(platform)
    if config is None:
        raise SubmissionError(f"منصة غير معروفة: {platform}. اختر من: {', '.join(PLATFORMS)}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale="ar-EG", viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.goto(config["login_url"])

        print("\n" + "=" * 60)
        print("سجّل دخولك في المتصفح اللي فتح دلوقتي.")
        print("لما تخلص وتتأكد إنك داخل، ارجع هنا ودوس Enter.")
        print("=" * 60)
        input()

        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = _session_path(platform)
        context.storage_state(path=str(path))
        print(f"✅ اتحفظت الجلسة: {path}")
        context.close()
        browser.close()


def inspect(url: str) -> None:
    """طباعة كل الحقول والأزرار في صفحة — عشان تظبط الـ selectors."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context(locale="ar-EG", viewport={"width": 1366, "height": 900})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        print("\nتنقّل في الصفحة (اضغط زرار التقديم لو لازم)، وبعدين دوس Enter هنا.")
        input()

        data = page.evaluate(
            """
            () => {
                const rows = [];
                document.querySelectorAll('input, textarea, select, button, a.button, [role=button]')
                    .forEach(el => {
                        const label = el.labels && el.labels.length ? el.labels[0].innerText.trim() : '';
                        rows.push({
                            tag: el.tagName.toLowerCase(),
                            type: el.getAttribute('type') || '',
                            id: el.id || '',
                            name: el.getAttribute('name') || '',
                            placeholder: el.getAttribute('placeholder') || '',
                            label: label,
                            text: (el.innerText || '').trim().slice(0, 60),
                            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                        });
                    });
                return rows;
            }
            """
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))
        context.close()
        browser.close()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="إرسال العروض / إدارة الجلسات")
    parser.add_argument("--login", metavar="PLATFORM", help="تسجيل دخول وحفظ جلسة")
    parser.add_argument("--inspect", metavar="URL", help="طباعة حقول وأزرار الصفحة")
    parser.add_argument("--dry-run", nargs=4, metavar=("PLATFORM", "URL", "PRICE", "DURATION"))
    parser.add_argument("--send", nargs=4, metavar=("PLATFORM", "URL", "PRICE", "DURATION"))
    parser.add_argument("--proposal", default="", help="نص العرض (مع --send)")
    args = parser.parse_args(argv)

    try:
        if args.login:
            login(args.login)
        elif args.inspect:
            inspect(args.inspect)
        elif args.dry_run:
            platform, url, price, duration = args.dry_run
            print(_dry_run_browser(platform, url, price, duration, args.proposal or "نص تجريبي للعرض"))
        elif args.send:
            platform, url, price, duration = args.send
            print(submit(platform, url, price, duration, args.proposal))
        else:
            parser.print_help()
    except SubmissionError as error:
        print(f"❌ {error}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
