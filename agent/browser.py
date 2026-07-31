"""Browser session lifecycle.

One persistent Chromium profile is launched per run and reused for every task.
That is both the least bot-like arrangement (a real user has one browser, with
cookies, that stays logged in) and the most practical one — Flipkart's OTP is
delivered to a phone the agent cannot read, so a session that survives across
runs means a human types an OTP once a fortnight rather than once a task.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Optional

from .config import BrowserConfig
from .humanize import stealth_script

# Chromium flags that remove the remaining automation tells not reachable from
# JavaScript. `AutomationControlled` is the one that actually matters — without
# it the CDP banner sets navigator.webdriver before our init script can run.
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
    "--no-default-browser-check",
    "--no-first-run",
    "--password-store=basic",
    "--start-maximized",
]

IGNORE_ARGS = ["--enable-automation", "--enable-blink-features=IdleDetection"]


class BrowserSession:
    """A launched browser context plus a page, with stealth already applied."""

    def __init__(self, config: Optional[BrowserConfig] = None):
        self.config = config or BrowserConfig()
        self._playwright = None
        self.context = None
        self.page = None

    def __enter__(self) -> "BrowserSession":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    def start(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()

        user_data_dir = Path(self.config.user_data_dir)
        user_data_dir.mkdir(parents=True, exist_ok=True)

        width, height = self.config.viewport
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(user_data_dir),
            headless=self.config.headless,
            args=LAUNCH_ARGS,
            ignore_default_args=IGNORE_ARGS,
            user_agent=self.config.user_agent,
            locale=self.config.locale,
            timezone_id=self.config.timezone_id,
            viewport={"width": width, "height": height},
            # A real Indian retail session has these; their absence is checkable.
            geolocation={"latitude": 28.4595, "longitude": 77.0266},  # Gurgaon
            permissions=["geolocation"],
            color_scheme="light",
        )
        self.context.set_default_timeout(self.config.default_timeout_ms)
        self.context.add_init_script(stealth_script())

        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self

    def new_tab(self):
        """Open a fresh tab — the spec's step 2, one tab per platform task."""
        if self.context is None:
            raise RuntimeError("session not started")
        return self.context.new_page()

    def screenshot(self, path: Path) -> Optional[Path]:
        if self.page is None:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            self.page.screenshot(path=str(path), full_page=True)
            return path
        return None

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self.context is not None:
                self.context.close()
        with contextlib.suppress(Exception):
            if self._playwright is not None:
                self._playwright.stop()
        self.context = None
        self.page = None
        self._playwright = None
