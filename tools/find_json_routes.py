"""
Setup (once):  uv add --dev playwright && uv run playwright install chromium
Usage:         uv run python -m tools.find_json_routes https://example.com/collections/lips
"""
import sys

from playwright.sync_api import sync_playwright

from scraper.fetch import USER_AGENT


def find_json_routes(url):
    found = []

    def on_response(resp):
        ctype = resp.headers.get("content-type", "")
        if "json" in ctype and resp.request.resource_type in ("xhr", "fetch"):
            found.append((resp.status, resp.request.method, resp.url))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(user_agent=USER_AGENT)
        page.on("response", on_response)
        page.goto(url, wait_until="networkidle")
        page.mouse.wheel(0, 4000)      # scroll once: many sites load more products on scroll
        page.wait_for_timeout(2000)
        browser.close()
    return found


if __name__ == "__main__":
    for status, method, url in find_json_routes(sys.argv[1]):
        print(status, method, url)