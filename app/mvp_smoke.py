import argparse
import asyncio
import json
from urllib.parse import urlsplit

from playwright.async_api import Page, async_playwright


def _validated_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain credentials, query, or fragment")
    return normalized


async def _check_cinema(page: Page, base_url: str) -> dict[str, object]:
    await page.goto(f"{base_url}/demo/cinema", wait_until="networkidle")
    await page.locator('[name="movie"]').select_option(label="Дюна")
    await page.locator('[name="date"]').fill("2026-09-10")
    await page.locator('[name="time_from"]').fill("18:00")
    await page.locator('[name="quantity"]').fill("1")
    await page.get_by_role("button", name="Продолжить к просмотру").click()
    review = page.locator("#review")
    purchase = page.get_by_role("button", name="Купить билет")
    return {
        "scenario": "cinema",
        "review_visible": await review.is_visible(),
        "final_action_disabled": await purchase.is_disabled(),
    }


async def _check_hotel(page: Page, base_url: str) -> dict[str, object]:
    await page.goto(f"{base_url}/demo/hotel", wait_until="networkidle")
    await page.locator('[name="destination"]').fill("Тверь")
    await page.locator('[name="hotel"]').select_option(label="Северная звезда")
    await page.locator('[name="check_in"]').fill("2026-09-10")
    await page.locator('[name="check_out"]').fill("2026-09-12")
    await page.locator('[name="guests"]').fill("1")
    await page.get_by_role("button", name="Показать доступные номера").click()
    review = page.locator("#review")
    confirmation = page.get_by_role("button", name="Подтвердить бронирование")
    return {
        "scenario": "hotel",
        "review_visible": await review.is_visible(),
        "final_action_disabled": await confirmation.is_disabled(),
    }


async def run_mvp_smoke(base_url: str) -> dict[str, object]:
    normalized_url = _validated_base_url(base_url)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            scenarios = [
                await _check_cinema(page, normalized_url),
                await _check_hotel(page, normalized_url),
            ]
        finally:
            await browser.close()
    return {
        "version": "MVP 0.1",
        "ok": all(
            scenario["review_visible"] and scenario["final_action_disabled"]
            for scenario in scenarios
        ),
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run safe Purchase Agent MVP checks.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    try:
        result = asyncio.run(run_mvp_smoke(args.base_url))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
