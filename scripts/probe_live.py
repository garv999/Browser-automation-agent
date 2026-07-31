"""Probe a live platform with an existing logged-in profile and report what the
page actually contains.

This exists to turn guessed selectors into verified ones. It only reads: it
navigates, screenshots, and prints what it found. It never clicks a control that
could place, cancel or modify an order.

    python scripts/probe_live.py --url https://www.flipkart.com/orders
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agent.browser import BrowserSession  # noqa: E402
from agent.config import BrowserConfig  # noqa: E402

#: Candidate selectors to test, grouped by the role they would play.
CANDIDATES = {
    "logged_in_marker": [
        "text=My Account", "text=My Orders", "a[href*='/account']",
        "div._1psGvi", "[class*='account']", "text=Logout",
    ],
    "login_prompt": [
        "text=Enter Email/Mobile number", "input[type='text']",
        "button:has-text('Request OTP')", "text=Login",
    ],
    "order_card": [
        "div._1OwMU0", "div[class*='order']", "a[href*='order_id']",
        "a[href*='/orders/']", "div._3fPTfd",
    ],
    "return_control": [
        "button:has-text('Return')", "a:has-text('Return')",
        "text=/Return|Exchange/i",
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://www.flipkart.com/orders")
    parser.add_argument("--out", default=str(REPO_ROOT / "logs" / "probe"))
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    config = BrowserConfig()
    config.headless = args.headless

    with BrowserSession(config) as session:
        page = session.page
        print(f"navigating to {args.url}")
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(4000)

        print(f"\nfinal url : {page.url}")
        print(f"title     : {page.title()}")

        print("\n--- candidate selectors ---")
        for role, selectors in CANDIDATES.items():
            print(f"\n{role}:")
            for selector in selectors:
                try:
                    count = page.locator(selector).count()
                except Exception as exc:
                    print(f"  {'ERR':>5}  {selector}   ({type(exc).__name__})")
                    continue
                mark = "HIT " if count else "  . "
                print(f"  {mark}{count:>4}  {selector}")

        body = page.locator("body").inner_text()[:1500]
        print("\n--- visible text (first 1500 chars) ---")
        print(body)

        (out / "page.html").write_text(page.content())
        page.screenshot(path=str(out / "page.png"), full_page=True)
        print(f"\nwrote {out/'page.html'} and {out/'page.png'}")


if __name__ == "__main__":
    main()
