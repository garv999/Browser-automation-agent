"""Flipkart adapter — sequential flow.

Flipkart has no multi-item return: each SKU on an order is returned through its
own micro-flow (open item → Return → reason → refund mode → confirm → return ID).
The adapter therefore runs `place_return` once per line item and reports one
outcome per item.

Two selector profiles ship with this adapter:

* `MOCK_SELECTORS` drives the local storefront in `mock_site/` and is exercised
  end to end by the test suite.
* `LIVE_SELECTORS` targets flipkart.com. **These are unverified.** Reaching the
  real order pages needs an OTP delivered to the account holder's phone, which
  is not something this repository can do in CI, so the live profile is a
  best-effort reading of Flipkart's public markup. The flow logic above it is
  identical and is the part the tests prove; if a live selector has drifted,
  fixing it is a one-line edit to the profile below.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import FlowModel, LineItem, Platform, ReturnOutcome, ReturnStatus, ReturnTask
from .base import AdapterContext, LoginRequired, OrderNotFound, PlatformAdapter, Selectors

MOCK_SELECTORS = Selectors(
    login_url="/account/login",
    orders_url="/orders",
    order_url_template="/orders/{order_id}",
    phone_input="[data-testid='phone-input']",
    phone_submit="[data-testid='phone-submit']",
    otp_input="[data-testid='otp-input']",
    otp_submit="[data-testid='otp-submit']",
    logged_in_marker="[data-testid='account-menu']",
    order_container="[data-testid='order-detail']",
    line_item="[data-testid='line-item']",
    line_item_title="[data-testid='item-title']",
    line_item_amount="[data-testid='item-amount']",
    return_button="[data-testid='return-button']",
    return_unavailable="[data-testid='return-unavailable']",
    reason_select="[data-testid='reason-select']",
    reason_option_template="{reason}",
    refund_mode="[data-testid='refund-mode']",
    comment_box="[data-testid='return-comment']",
    confirm_button="[data-testid='confirm-return']",
    confirmation_marker="[data-testid='return-confirmation']",
    return_id_field="[data-testid='return-id']",
    refund_amount_field="[data-testid='refund-amount']",
)

LIVE_SELECTORS = Selectors(
    login_url="/account/login",
    orders_url="/orders",
    order_url_template="/orders?order_id={order_id}",
    phone_input="input[type='text'][autocomplete='off']",
    phone_submit="button:has-text('Request OTP')",
    otp_input="input[type='text'][maxlength='6']",
    otp_submit="button:has-text('Verify')",
    logged_in_marker="text=My Account",
    order_container="div._1OwMU0, div[class*='order-detail']",
    line_item="div._1AtVbE:has(div._2-uHZH), div[class*='order-item']",
    line_item_title="a._2Kn22P, div._2-uHZH",
    line_item_amount="div._2-ut7f, div[class*='price']",
    return_button="button:has-text('Return'), a:has-text('Return')",
    return_unavailable="text=/Return window closed|not eligible for return|Return period over/i",
    reason_select="div[class*='reason'] select, select",
    reason_option_template="{reason}",
    refund_mode="div[class*='refund'] input[type='radio']",
    comment_box="textarea",
    confirm_button="button:has-text('Confirm'), button:has-text('Submit')",
    confirmation_marker="text=/Return request .*(placed|created|submitted)/i",
    return_id_field="text=/Return ID[: ]*[A-Z0-9]+/i",
    refund_amount_field="text=/Refund (amount|of)[: ₹]*[0-9,]+/i",
)

#: Product id in a Flipkart URL, e.g. ...&pid=TSHG9FQZSSAUGKUP&...
PID_RE = re.compile(r"[?&]pid=([A-Za-z0-9]+)", re.IGNORECASE)
RETURN_ID_RE = re.compile(r"([A-Z]{2}\d{10,})", re.IGNORECASE)
AMOUNT_RE = re.compile(r"([0-9][0-9,]*(?:\.\d{1,2})?)")


def product_id_from_link(link: str) -> Optional[str]:
    match = PID_RE.search(link or "")
    return match.group(1) if match else None


class FlipkartAdapter(PlatformAdapter):
    platform = Platform.FLIPKART
    default_flow = FlowModel.SEQUENTIAL

    def __init__(self, ctx: AdapterContext, selectors: Selectors = MOCK_SELECTORS, base_url: str = "https://www.flipkart.com"):
        super().__init__(ctx, selectors, base_url)

    # -- session ----------------------------------------------------------

    def is_logged_in(self) -> bool:
        try:
            self.page.goto(self.url(self.sel.orders_url), wait_until="domcontentloaded")
        except Exception:
            return False
        self.human.pause(0.5)
        return self.page.locator(self.sel.logged_in_marker).count() > 0

    def login(self) -> None:
        """Phone + OTP. The OTP is typed by a human — by design.

        The agent will not attempt to intercept an SMS. A one-time code is the
        account holder's to enter, and a persistent browser profile means they
        do it once rather than once per run.
        """
        if self.is_logged_in():
            self.log("session already authenticated; reusing profile")
            return

        self.log("logging in")
        self.page.goto(self.url(self.sel.login_url), wait_until="domcontentloaded")
        self.human.pause()

        phone = self.page.locator(self.sel.phone_input).first
        phone.wait_for(state="visible")
        self.human.type_like_human(phone, self.ctx.config.login_phone)
        self.human.pause()
        self.human.click(self.page, self.page.locator(self.sel.phone_submit).first)

        otp_box = self.page.locator(self.sel.otp_input).first
        otp_box.wait_for(state="visible", timeout=int(self.ctx.config.otp_timeout_s * 1000))

        provider = self.ctx.otp_provider or _prompt_for_otp
        otp = provider(self.ctx.config.login_phone)
        if not otp:
            raise LoginRequired("no OTP supplied; cannot authenticate")

        before = self.page.url
        self.human.type_like_human(otp_box, otp)
        self.human.pause()
        self.human.click(self.page, self.page.locator(self.sel.otp_submit).first)

        if self.wait_for_settle(before, self.sel.logged_in_marker) is None:
            raise LoginRequired("login did not complete (OTP rejected or expired)")
        self.log("login complete")

    # -- orders -----------------------------------------------------------

    def open_order(self, order_id: str) -> None:
        target = self.url(self.sel.order_url_template.format(order_id=order_id))
        self.page.goto(target, wait_until="domcontentloaded")
        self.human.pause()
        self.human.idle_scroll(self.page)

        if self.page.locator(self.sel.order_container).count() == 0:
            raise OrderNotFound(f"order {order_id} not reachable at {target}")

    def list_line_items(self, order_id: str) -> list[LineItem]:
        rows = self.page.locator(self.sel.line_item)
        items: list[LineItem] = []

        for index in range(rows.count()):
            row = rows.nth(index)
            title = _text(row.locator(self.sel.line_item_title).first)
            amount = _parse_amount(_text(row.locator(self.sel.line_item_amount).first))

            product_id = None
            link = row.locator("a").first
            if link.count() > 0:
                product_id = product_id_from_link(link.get_attribute("href") or "")
            if not product_id:
                product_id = row.get_attribute("data-product-id")

            unavailable = row.locator(self.sel.return_unavailable)
            returnable = row.locator(self.sel.return_button).count() > 0 and unavailable.count() == 0

            items.append(
                LineItem(
                    index=index,
                    title=title,
                    product_id=product_id,
                    amount=amount,
                    returnable=returnable,
                    reason_unreturnable=_text(unavailable.first) if unavailable.count() else "",
                )
            )
        return items

    # -- the return micro-flow -------------------------------------------

    def place_return(self, task: ReturnTask, item: LineItem) -> ReturnOutcome:
        """One line item, start to finish.

        The platform's own verdict outranks the spreadsheet: if the site says
        the item cannot be returned, that is the outcome recorded, regardless of
        what the sheet's return window claimed.
        """
        row = self.page.locator(self.sel.line_item).nth(item.index)

        if not item.returnable:
            note = item.reason_unreturnable or "platform offers no return option for this item"
            status = _classify_unavailable(note)
            self.log(f"{task.label}: not returnable — {note}")
            return ReturnOutcome(status, message=note)

        before = self.page.url
        self.human.click(self.page, row.locator(self.sel.return_button).first)
        if self.wait_for_settle(before, self.sel.reason_select, self.sel.confirm_button) is None:
            return ReturnOutcome(
                ReturnStatus.FAILED,
                message="the return form never appeared after opening the return flow",
            )
        self.human.pause()

        self._select_reason()
        self._select_refund_mode()

        comment = self.page.locator(self.sel.comment_box)
        if comment.count() > 0:
            self.human.type_like_human(comment.first, "Item not as described.")
            self.human.pause(0.5)

        if self.ctx.config.dry_run:
            self.log(f"{task.label}: DRY RUN — stopping before the confirm click")
            return ReturnOutcome(
                ReturnStatus.SUPPORT_NEEDED,
                message="dry run: return form completed but not submitted",
            )

        before = self.page.url
        self.human.click(self.page, self.page.locator(self.sel.confirm_button).first)
        self.wait_for_settle(before, self.sel.confirmation_marker)
        self.human.pause()

        return self._read_confirmation(task)

    def _select_reason(self) -> None:
        reason = self.ctx.config.return_reason
        select = self.page.locator(self.sel.reason_select).first
        if select.count() == 0:
            self.log("no reason control on the page; continuing")
            return

        self.human.pause(0.6)
        tag = (select.evaluate("el => el.tagName") or "").lower()
        if tag == "select":
            try:
                select.select_option(label=reason)
            except Exception:
                # Wording differs between platforms; fall back to the first
                # non-placeholder option rather than abandoning the return.
                options = select.locator("option")
                for i in range(options.count()):
                    value = options.nth(i).get_attribute("value")
                    if value:
                        select.select_option(value=value)
                        self.log(f"reason {reason!r} unavailable; used {options.nth(i).inner_text()!r}")
                        break
        else:
            self.human.click(self.page, select)
            option = self.page.locator(f"text={reason}").first
            if option.count() > 0:
                self.human.click(self.page, option)

    def _select_refund_mode(self) -> None:
        control = self.page.locator(self.sel.refund_mode)
        if control.count() == 0:
            return
        self.human.pause(0.5)
        first = control.first
        tag = (first.evaluate("el => el.tagName") or "").lower()
        if tag == "select":
            try:
                first.select_option(label=self.ctx.config.refund_mode)
            except Exception:
                pass
        else:
            self.human.click(self.page, first)

    def _read_confirmation(self, task: ReturnTask) -> ReturnOutcome:
        """Capture the return ID and refund amount the platform reports."""
        if self.page.locator(self.sel.confirmation_marker).count() == 0:
            body = _text(self.page.locator("body").first)[:400]
            return ReturnOutcome(
                ReturnStatus.FAILED,
                message=f"no return confirmation shown after submit; page said: {body!r}",
            )

        return_id = _text(self.page.locator(self.sel.return_id_field).first)
        match = RETURN_ID_RE.search(return_id)
        if match:
            return_id = match.group(1)

        refund = _parse_amount(_text(self.page.locator(self.sel.refund_amount_field).first))

        if not return_id:
            # The return may well have been placed; a missing ID is exactly the
            # case a human must look at rather than the agent retrying blindly.
            return ReturnOutcome(
                ReturnStatus.SUPPORT_NEEDED,
                refund_amount=refund,
                message="confirmation page shown but no return ID could be read",
            )

        self.log(f"{task.label}: return placed, id={return_id}, refund={refund}")
        return ReturnOutcome(
            ReturnStatus.PLACED,
            return_id=return_id,
            refund_amount=refund,
            message=f"return placed via Flipkart sequential flow (reason: {self.ctx.config.return_reason})",
        )


def _classify_unavailable(note: str) -> ReturnStatus:
    """Map the platform's own refusal text onto a recorded status."""
    text = (note or "").lower()
    if "cancel" in text or "refunded" in text:
        return ReturnStatus.ALREADY_REFUNDED
    if "not delivered" in text or "out for delivery" in text or "shipped" in text:
        return ReturnStatus.NOT_DELIVERED
    if "window" in text or "period" in text or "expired" in text or "eligib" in text:
        return ReturnStatus.OUT_OF_WINDOW
    return ReturnStatus.SUPPORT_NEEDED


def _text(locator) -> str:
    try:
        if locator.count() == 0:
            return ""
        return (locator.inner_text() or "").strip()
    except Exception:
        return ""


def _parse_amount(text: str) -> Optional[float]:
    match = AMOUNT_RE.search((text or "").replace("₹", ""))
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _prompt_for_otp(phone: str) -> str:
    print(f"\n>>> Flipkart has sent an OTP to {phone}. Enter it here to continue.")
    return input(">>> OTP: ").strip()
