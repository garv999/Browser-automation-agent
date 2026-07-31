"""Platform adapter contract.

The runner knows nothing about any particular retailer. It asks an adapter four
things — log in, open an order, list its line items, return one item — and the
adapter decides how. That boundary is what lets Flipkart's sequential flow and
Amazon's batch flow share one orchestration loop, and what lets the whole agent
be tested against a local mock storefront without a single change to the runner.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import AgentConfig
from ..humanize import Humanizer
from ..models import FlowModel, LineItem, Platform, ReturnOutcome, ReturnTask


class LoginRequired(RuntimeError):
    """Raised when a session is not authenticated and cannot be recovered
    without a human (an OTP has to be typed by the person holding the phone)."""


class OrderNotFound(RuntimeError):
    pass


@dataclass
class Selectors:
    """A selector profile.

    Two profiles exist per platform: one for the mock storefront used in tests
    and one for the live site. Keeping them as data — rather than as literals
    scattered through the flow code — means the *logic* is verified by the test
    suite even though the live site's DOM cannot be reached from here, and a
    selector that drifts is a one-line data change, not a code change.
    """

    login_url: str
    orders_url: str
    order_url_template: str

    phone_input: str
    phone_submit: str
    otp_input: str
    otp_submit: str
    logged_in_marker: str

    order_container: str
    line_item: str
    line_item_title: str
    line_item_amount: str
    return_button: str
    return_unavailable: str

    reason_select: str
    reason_option_template: str
    refund_mode: str
    comment_box: str
    confirm_button: str

    confirmation_marker: str
    return_id_field: str
    refund_amount_field: str

    #: Present only where a platform offers a true batch flow (Amazon).
    batch_entry: Optional[str] = None
    batch_item_checkbox: Optional[str] = None


@dataclass
class AdapterContext:
    """Everything an adapter needs from the run, passed in rather than global."""

    page: object
    config: AgentConfig
    humanizer: Humanizer
    logger: Callable[[str], None] = print
    #: Supplies an OTP when the platform asks for one. The default blocks on
    #: stdin; tests inject a canned value.
    otp_provider: Optional[Callable[[str], str]] = None
    extras: dict = field(default_factory=dict)


class PlatformAdapter(ABC):
    platform: Platform
    #: Flow the adapter uses when it cannot determine one from the page.
    default_flow: FlowModel = FlowModel.SEQUENTIAL

    def __init__(self, ctx: AdapterContext, selectors: Selectors, base_url: str):
        self.ctx = ctx
        self.sel = selectors
        self.base_url = base_url.rstrip("/")

    # -- convenience ------------------------------------------------------

    @property
    def page(self):
        return self.ctx.page

    @property
    def human(self) -> Humanizer:
        return self.ctx.humanizer

    def log(self, message: str) -> None:
        self.ctx.logger(f"[{self.platform.value}] {message}")

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}" if path.startswith("/") else path

    # -- contract ---------------------------------------------------------

    @abstractmethod
    def is_logged_in(self) -> bool: ...

    @abstractmethod
    def login(self) -> None:
        """Authenticate, prompting a human for an OTP if the platform demands one."""

    @abstractmethod
    def open_order(self, order_id: str) -> None:
        """Navigate to the order page. Raises `OrderNotFound` if it is not there."""

    @abstractmethod
    def list_line_items(self, order_id: str) -> list[LineItem]:
        """Read the order's line items as the platform itself presents them."""

    @abstractmethod
    def place_return(self, task: ReturnTask, item: LineItem) -> ReturnOutcome:
        """Run the return micro-flow for exactly one line item."""

    def detect_flow_model(self, order_id: str) -> FlowModel:
        """Decide whether this order supports a batch return.

        Default: whatever the adapter declares. Amazon overrides this by probing
        for its multi-item return entry point, which is present on some orders
        and absent on others — which is precisely why this is detected per order
        rather than hardcoded per platform.
        """
        return self.default_flow

    def place_batch_return(self, tasks: list[ReturnTask], items: list[LineItem]) -> dict[str, ReturnOutcome]:
        """Return several line items in one flow.

        Keyed by each task's `label` so the runner can still write back one row
        per line item — a batch flow on the platform must not become a batch
        verdict in the sheet.
        """
        raise NotImplementedError(f"{self.platform.value} has no batch return flow")

    # -- shared helpers ---------------------------------------------------

    def wait_for_settle(self, previous_url: str, *selectors: str, timeout_ms: int = 15_000) -> Optional[str]:
        """Wait until a click has actually produced a new page state.

        `wait_for_load_state` is not enough on its own: if the navigation has not
        started yet it reports the *old* document as loaded and the caller reads
        the previous page. That race is invisible on a fast local server and
        routine on a real retail site, and it shows up as phantom failures — a
        login that "did not complete", a confirmation page that "was not shown".

        Settling means either the URL changed (classic navigation) or one of the
        expected selectors appeared (single-page apps, where it never will).
        Returns the selector that matched, or None on timeout.
        """
        import time

        #: Once the URL has changed, keep looking this long for a selector, so a
        #: page that renders its content asynchronously is not called a failure.
        grace_s = 2.0

        deadline = time.monotonic() + timeout_ms / 1000
        navigated_at: Optional[float] = None

        while time.monotonic() < deadline:
            for selector in selectors:
                try:
                    if selector and self.page.locator(selector).count() > 0:
                        return selector
                except Exception:
                    pass  # mid-navigation; the next poll sees a stable page

            if navigated_at is None:
                try:
                    if self.page.url != previous_url:
                        navigated_at = time.monotonic()
                        self.page.wait_for_load_state("domcontentloaded")
                except Exception:
                    pass
            elif time.monotonic() - navigated_at > grace_s:
                # Navigation finished and none of the expected markers appeared.
                return None

            self.page.wait_for_timeout(100)
        return None

    def match_line_item(self, task: ReturnTask, items: list[LineItem]) -> Optional[LineItem]:
        """Find the platform-side line item that corresponds to this sheet row.

        Matching is by product id first (exact, from the product link), then by
        a normalised title overlap. Ambiguity is resolved by returning nothing —
        placing a return against the wrong SKU is far worse than asking a human.
        """
        if not items:
            return None

        if task.sku:
            exact = [i for i in items if i.product_id and i.product_id.lower() == task.sku.lower()]
            if len(exact) == 1:
                return exact[0]

        needle = _normalise(task.product_name or task.product_link)
        if needle:
            scored = sorted(
                ((_overlap(needle, _normalise(i.title)), i) for i in items),
                key=lambda pair: pair[0],
                reverse=True,
            )
            best_score, best = scored[0]
            runner_up = scored[1][0] if len(scored) > 1 else 0.0
            # Require a clear winner, not just a leader.
            if best_score >= 0.45 and best_score - runner_up >= 0.15:
                return best

        if len(items) == 1:
            return items[0]
        return None


def _normalise(text: str) -> set[str]:
    import re

    tokens = re.split(r"[^a-z0-9]+", (text or "").lower())
    stop = {"www", "com", "flipkart", "amazon", "dl", "in", "p", "pid", "lid", "marketplace", "https", "http"}
    return {t for t in tokens if len(t) > 2 and t not in stop and not t.isdigit()}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
