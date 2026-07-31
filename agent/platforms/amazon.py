"""Amazon adapter — batch flow, with a sequential fallback.

Amazon's order page usually offers "Return or replace items", which opens one
form covering every returnable item on the order: tick the items, give each a
reason, submit once, and the confirmation lists a separate return ID per item.
That is the batch model.

The entry point is not always present — digital items, orders past their window,
and some seller-fulfilled orders do not get it. So the flow is **detected per
order**, not assumed per platform, and when it is absent the adapter falls back
to returning items one at a time.

Either way the outcome is per line item. A batch flow on the platform must never
collapse into a single verdict in the sheet.
"""

from __future__ import annotations

import re
from typing import Optional

from ..models import FlowModel, LineItem, Platform, ReturnOutcome, ReturnStatus, ReturnTask
from .base import AdapterContext, LoginRequired, OrderNotFound, PlatformAdapter, Selectors
from .flipkart import _classify_unavailable, _parse_amount, _text

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
    batch_entry="[data-testid='batch-return-entry']",
    batch_item_checkbox="[data-testid='batch-item']",
)

LIVE_SELECTORS = Selectors(
    login_url="/ap/signin",
    orders_url="/gp/css/order-history",
    order_url_template="/gp/your-account/order-details?orderID={order_id}",
    phone_input="input#ap_email",
    phone_submit="input#continue",
    otp_input="input#auth-mfa-otpcode",
    otp_submit="input#auth-signin-button",
    logged_in_marker="#nav-link-accountList",
    order_container="div.order-card, div#orderDetails",
    line_item="div.a-fixed-left-grid.a-spacing-none, div[class*='shipment'] div.a-row",
    line_item_title="a.a-link-normal[href*='/gp/product/'], div.yohtmlc-item a",
    line_item_amount="span.a-color-price, div.a-column span.a-size-small",
    return_button="a:has-text('Return or replace items'), a:has-text('Return items')",
    return_unavailable="text=/Return window closed|no longer returnable|not eligible/i",
    reason_select="select[name*='reason'], #reason-select",
    reason_option_template="{reason}",
    refund_mode="input[name*='refundMethod'], select[name*='refund']",
    comment_box="textarea[name*='comment']",
    confirm_button="input[name*='submit'], button:has-text('Submit return')",
    confirmation_marker="text=/Return request (submitted|complete)/i",
    return_id_field="text=/RMA|Return (ID|authorization)[: ]*[A-Z0-9-]+/i",
    refund_amount_field="text=/Refund (total|amount)[: $₹]*[0-9,.]+/i",
    batch_entry="a:has-text('Return or replace items')",
    batch_item_checkbox="input[type='checkbox'][name*='item']",
)

RMA_RE = re.compile(r"([A-Z0-9]{6,}-?[A-Z0-9]*)")


class AmazonAdapter(PlatformAdapter):
    platform = Platform.AMAZON
    default_flow = FlowModel.BATCH

    def __init__(self, ctx: AdapterContext, selectors: Selectors = MOCK_SELECTORS, base_url: str = "https://www.amazon.in"):
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
        if self.is_logged_in():
            self.log("session already authenticated; reusing profile")
            return

        self.page.goto(self.url(self.sel.login_url), wait_until="domcontentloaded")
        self.human.pause()

        phone = self.page.locator(self.sel.phone_input).first
        phone.wait_for(state="visible")
        self.human.type_like_human(phone, self.ctx.config.login_phone)
        self.human.click(self.page, self.page.locator(self.sel.phone_submit).first)

        otp_box = self.page.locator(self.sel.otp_input).first
        otp_box.wait_for(state="visible", timeout=int(self.ctx.config.otp_timeout_s * 1000))

        provider = self.ctx.otp_provider
        otp = provider(self.ctx.config.login_phone) if provider else ""
        if not otp:
            raise LoginRequired("no OTP supplied; cannot authenticate")

        before = self.page.url
        self.human.type_like_human(otp_box, otp)
        self.human.click(self.page, self.page.locator(self.sel.otp_submit).first)

        if self.wait_for_settle(before, self.sel.logged_in_marker) is None:
            raise LoginRequired("login did not complete (OTP rejected or expired)")

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
            unavailable = row.locator(self.sel.return_unavailable)
            items.append(
                LineItem(
                    index=index,
                    title=_text(row.locator(self.sel.line_item_title).first),
                    product_id=row.get_attribute("data-product-id"),
                    amount=_parse_amount(_text(row.locator(self.sel.line_item_amount).first)),
                    returnable=unavailable.count() == 0,
                    reason_unreturnable=_text(unavailable.first) if unavailable.count() else "",
                )
            )
        return items

    def detect_flow_model(self, order_id: str) -> FlowModel:
        """Probe the open order page for the batch entry point."""
        if not self.sel.batch_entry:
            return FlowModel.SEQUENTIAL
        has_batch = self.page.locator(self.sel.batch_entry).count() > 0
        model = FlowModel.BATCH if has_batch else FlowModel.SEQUENTIAL
        self.log(f"order {order_id}: {model.value} return flow detected")
        return model

    # -- batch flow -------------------------------------------------------

    def place_batch_return(
        self, tasks: list[ReturnTask], items: list[LineItem]
    ) -> dict[str, ReturnOutcome]:
        """Tick every eligible item, give each a reason, submit once.

        Items the runner already ruled out are simply left unticked; they keep
        the outcome the runner assigned and never enter this flow.
        """
        pairs = [(t, i) for t, i in zip(tasks, items) if i is not None and i.returnable]
        outcomes: dict[str, ReturnOutcome] = {}

        for task, item in zip(tasks, items):
            if item is None:
                outcomes[task.label] = ReturnOutcome(
                    ReturnStatus.SUPPORT_NEEDED,
                    message="line item could not be matched on the Amazon order page",
                )
            elif not item.returnable:
                note = item.reason_unreturnable or "Amazon offers no return option for this item"
                outcomes[task.label] = ReturnOutcome(_classify_unavailable(note), message=note)

        if not pairs:
            self.log("no returnable items on this order; batch flow skipped")
            return outcomes

        before = self.page.url
        self.human.click(self.page, self.page.locator(self.sel.batch_entry).first)
        if self.wait_for_settle(before, self.sel.batch_item_checkbox) is None:
            raise RuntimeError("the batch return form never appeared")
        self.human.pause()

        checkboxes = self.page.locator(self.sel.batch_item_checkbox)
        for task, item in pairs:
            box = checkboxes.nth(item.index)
            if box.count() == 0:
                outcomes[task.label] = ReturnOutcome(
                    ReturnStatus.SUPPORT_NEEDED,
                    message="item missing from the batch return form",
                )
                continue
            self.human.click(self.page, box)
            self.human.pause(0.4)

            per_item_reason = self.page.locator(f"[data-testid='reason-select-{item.index}']")
            target = per_item_reason.first if per_item_reason.count() else self.page.locator(self.sel.reason_select).first
            if target.count():
                try:
                    target.select_option(label=self.ctx.config.return_reason)
                except Exception:
                    pass

        if self.ctx.config.dry_run:
            self.log("DRY RUN — batch form completed but not submitted")
            for task, _ in pairs:
                outcomes[task.label] = ReturnOutcome(
                    ReturnStatus.SUPPORT_NEEDED,
                    message="dry run: batch return form completed but not submitted",
                )
            return outcomes

        before = self.page.url
        self.human.click(self.page, self.page.locator(self.sel.confirm_button).first)

        # Past this point the batch has been submitted, so nothing may propagate
        # an exception to the runner: the runner's response to a batch failure is
        # to retry the items sequentially, which would place every one of these
        # returns a second time. A submitted batch we cannot read is a job for a
        # human, not for a retry.
        try:
            self.wait_for_settle(before, self.sel.confirmation_marker)
            self.human.pause()
            outcomes.update(self._read_batch_confirmation(pairs))
        except Exception as exc:  # noqa: BLE001
            self.log(f"batch submitted but the confirmation could not be read: {exc}")
            for task, _ in pairs:
                outcomes[task.label] = ReturnOutcome(
                    ReturnStatus.SUPPORT_NEEDED,
                    message=(
                        "batch return was SUBMITTED but the confirmation page could not be read "
                        f"({exc}); check Amazon before retrying — a retry would return these items twice"
                    ),
                )
        return outcomes

    def _read_batch_confirmation(self, pairs) -> dict[str, ReturnOutcome]:
        """Read the per-item return IDs off the batch confirmation page.

        The confirmation is keyed by product id rather than by row order,
        because Amazon reorders items on this page and matching positionally
        would attach one item's return ID to another item's row.
        """
        outcomes: dict[str, ReturnOutcome] = {}

        if self.page.locator(self.sel.confirmation_marker).count() == 0:
            for task, _ in pairs:
                outcomes[task.label] = ReturnOutcome(
                    ReturnStatus.FAILED,
                    message="batch return submitted but no confirmation page was shown",
                )
            return outcomes

        for task, item in pairs:
            block = self.page.locator(f"[data-testid='confirmed-item'][data-product-id='{item.product_id}']")
            if block.count() == 0:
                # Fall back to a title match before giving up on this item.
                block = self.page.locator("[data-testid='confirmed-item']").filter(has_text=item.title[:40])

            if block.count() == 0:
                outcomes[task.label] = ReturnOutcome(
                    ReturnStatus.SUPPORT_NEEDED,
                    message="batch confirmation did not list this item; verify manually before retrying",
                )
                continue

            return_id = _text(block.first.locator(self.sel.return_id_field).first)
            match = RMA_RE.search(return_id)
            if match:
                return_id = match.group(1)
            refund = _parse_amount(_text(block.first.locator(self.sel.refund_amount_field).first))

            outcomes[task.label] = ReturnOutcome(
                ReturnStatus.PLACED if return_id else ReturnStatus.SUPPORT_NEEDED,
                return_id=return_id or None,
                refund_amount=refund,
                message=(
                    "return placed via Amazon batch flow"
                    if return_id
                    else "item confirmed in batch but no return ID was shown"
                ),
            )
        return outcomes

    # -- sequential fallback ----------------------------------------------

    def place_return(self, task: ReturnTask, item: LineItem) -> ReturnOutcome:
        if not item.returnable:
            note = item.reason_unreturnable or "Amazon offers no return option for this item"
            return ReturnOutcome(_classify_unavailable(note), message=note)

        row = self.page.locator(self.sel.line_item).nth(item.index)
        button = row.locator(self.sel.return_button)
        if button.count() == 0:
            return ReturnOutcome(
                ReturnStatus.SUPPORT_NEEDED,
                message="no per-item return control and no batch entry point on this order",
            )

        before = self.page.url
        self.human.click(self.page, button.first)
        if self.wait_for_settle(before, self.sel.reason_select, self.sel.confirm_button) is None:
            return ReturnOutcome(
                ReturnStatus.FAILED,
                message="the return form never appeared after opening the return flow",
            )
        self.human.pause()

        reason = self.page.locator(self.sel.reason_select).first
        if reason.count():
            try:
                reason.select_option(label=self.ctx.config.return_reason)
            except Exception:
                pass

        if self.ctx.config.dry_run:
            return ReturnOutcome(
                ReturnStatus.SUPPORT_NEEDED,
                message="dry run: return form completed but not submitted",
            )

        before = self.page.url
        self.human.click(self.page, self.page.locator(self.sel.confirm_button).first)
        if self.wait_for_settle(before, self.sel.confirmation_marker) is None:
            return ReturnOutcome(ReturnStatus.FAILED, message="no confirmation shown after submit")

        return_id = _text(self.page.locator(self.sel.return_id_field).first)
        match = RMA_RE.search(return_id)
        if match:
            return_id = match.group(1)
        refund = _parse_amount(_text(self.page.locator(self.sel.refund_amount_field).first))

        return ReturnOutcome(
            ReturnStatus.PLACED if return_id else ReturnStatus.SUPPORT_NEEDED,
            return_id=return_id or None,
            refund_amount=refund,
            message="return placed via Amazon sequential flow",
        )
