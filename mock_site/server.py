"""A local mock storefront that speaks Flipkart's and Amazon's return flows.

Why this exists: the agent cannot be tested end to end against the real sites.
Logging in needs an OTP delivered to a phone, and every successful test would
place a real, irreversible return on a real account. So the return *flows* are
reproduced here — login → OTP → orders → order detail → return form → return ID —
and the agent drives them with a real browser, real navigation and real clicks.

What that buys us: the orchestration, matching, eligibility, partial-success and
write-back logic is verified against a browser rather than a stub. What it does
not buy us: proof that the live selector profiles still match today's Flipkart
DOM. That gap is called out in the README and is deliberately isolated to the
`LIVE_SELECTORS` blocks so it cannot leak into the logic.

Run it:
    python -m mock_site.server --port 8765
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional

from flask import Flask, redirect, render_template, request, session, url_for

from .fixtures import ALL_ORDERS, MockItem, MockOrder, find_order, orders_for

MOCK_OTP = "123456"


@dataclass
class PlacedReturn:
    platform: str
    order_id: str
    product_id: str
    return_id: str
    refund_amount: float
    reason: str
    flow: str  # "sequential" | "batch"


@dataclass
class MockState:
    """Everything the fake platform remembers, resettable between tests."""

    placed: list[PlacedReturn] = field(default_factory=list)
    counter: int = 0
    #: Set by tests to make a route misbehave once — used to prove the agent
    #: contains failures instead of abandoning the rest of the order.
    fail_next_return: bool = False

    def next_return_id(self, platform: str) -> str:
        self.counter += 1
        if platform == "flipkart":
            return f"CR{26060000000000 + self.counter}"
        return f"RMA{88000000 + self.counter}"

    def already_returned(self, order_id: str, product_id: str) -> Optional[PlacedReturn]:
        for entry in self.placed:
            if entry.order_id == order_id and entry.product_id == product_id:
                return entry
        return None

    def reset(self) -> None:
        self.placed.clear()
        self.counter = 0
        self.fail_next_return = False


STATE = MockState()

RETURN_REASONS = [
    "Item is not as described",
    "Wrong size",
    "Damaged on arrival",
    "Better price available",
    "Quality issue",
]
REFUND_MODES = ["Original payment method", "Store credit / wallet"]


def item_view(order: MockOrder, index: int, item: MockItem) -> dict:
    """Everything a template needs to render one line item, including whether
    the platform will offer a Return control for it."""
    existing = STATE.already_returned(order.order_id, item.product_id)
    reason = item.unavailable_reason()

    if existing:
        unavailable = f"Return already requested — {existing.return_id}"
    elif item.no_return_control:
        # Flipkart's real-world "no direct return button, chat did not confirm"
        # case straight from the supplied sheet.
        unavailable = None
    else:
        unavailable = reason

    return {
        "index": index,
        "item": item,
        "unavailable": unavailable,
        # An item with no return control shows neither a button nor a reason —
        # the agent has to notice the absence and flag it for a human.
        "show_button": unavailable is None and reason is None and not item.no_return_control,
        "existing": existing,
    }


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.secret_key = "mock-storefront-not-a-real-secret"

    # -- test control plane ------------------------------------------------

    @app.post("/__reset")
    def reset():
        STATE.reset()
        session.clear()
        return {"ok": True}

    @app.get("/__state")
    def state():
        return {
            "placed": [vars(p) for p in STATE.placed],
            "count": len(STATE.placed),
        }

    @app.post("/__fail_next")
    def fail_next():
        STATE.fail_next_return = True
        return {"ok": True}

    @app.get("/")
    def index():
        return render_template("index.html", orders=ALL_ORDERS)

    # -- auth --------------------------------------------------------------

    @app.route("/<platform>/account/login", methods=["GET", "POST"])
    def login(platform: str):
        if request.method == "POST":
            phone = (request.form.get("phone") or "").strip()
            if len(phone) < 10:
                return render_template("login.html", platform=platform, error="Enter a valid mobile number")
            session[f"{platform}_phone"] = phone
            return render_template("otp.html", platform=platform, phone=phone)
        return render_template("login.html", platform=platform, error=None)

    @app.post("/<platform>/account/otp")
    def verify_otp(platform: str):
        otp = (request.form.get("otp") or "").strip()
        if otp != MOCK_OTP:
            return render_template(
                "otp.html",
                platform=platform,
                phone=session.get(f"{platform}_phone", ""),
                error="Incorrect OTP. Please try again.",
            )
        session[f"{platform}_authed"] = True
        return redirect(url_for("orders", platform=platform))

    def _require_login(platform: str):
        if not session.get(f"{platform}_authed"):
            return redirect(url_for("login", platform=platform))
        return None

    # -- orders ------------------------------------------------------------

    @app.get("/<platform>/orders")
    def orders(platform: str):
        guard = _require_login(platform)
        if guard:
            return guard
        return render_template("orders.html", platform=platform, orders=orders_for(platform))

    @app.get("/<platform>/orders/<order_id>")
    def order_detail(platform: str, order_id: str):
        guard = _require_login(platform)
        if guard:
            return guard

        order = find_order(platform, order_id)
        if order is None:
            return render_template("not_found.html", platform=platform, order_id=order_id), 404

        views = [item_view(order, i, item) for i, item in enumerate(order.items)]
        return render_template(
            "order_detail.html",
            platform=platform,
            order=order,
            views=views,
            show_batch=order.batch_return and any(v["show_button"] for v in views),
        )

    # -- sequential return flow -------------------------------------------

    @app.get("/<platform>/returns/new/<order_id>/<int:index>")
    def return_form(platform: str, order_id: str, index: int):
        guard = _require_login(platform)
        if guard:
            return guard

        order = find_order(platform, order_id)
        if order is None or index >= len(order.items):
            return render_template("not_found.html", platform=platform, order_id=order_id), 404

        return render_template(
            "return_form.html",
            platform=platform,
            order=order,
            index=index,
            item=order.items[index],
            reasons=RETURN_REASONS,
            refund_modes=REFUND_MODES,
        )

    @app.post("/<platform>/returns/create")
    def create_return(platform: str):
        guard = _require_login(platform)
        if guard:
            return guard

        order_id = request.form["order_id"]
        index = int(request.form["index"])
        reason = request.form.get("reason") or RETURN_REASONS[0]

        order = find_order(platform, order_id)
        item = order.items[index]

        if STATE.fail_next_return:
            # A platform-side error page: no confirmation marker, so the agent
            # must record a failure for this item and carry on with the rest.
            STATE.fail_next_return = False
            return render_template("error.html", platform=platform), 500

        existing = STATE.already_returned(order_id, item.product_id)
        if existing:
            entry = existing
        else:
            entry = PlacedReturn(
                platform=platform,
                order_id=order_id,
                product_id=item.product_id,
                return_id=STATE.next_return_id(platform),
                refund_amount=round(item.amount * item.quantity, 2),
                reason=reason,
                flow="sequential",
            )
            STATE.placed.append(entry)

        return render_template("return_confirm.html", platform=platform, order=order, entry=entry, item=item)

    # -- batch return flow (Amazon) ---------------------------------------

    @app.get("/<platform>/returns/batch/<order_id>")
    def batch_form(platform: str, order_id: str):
        guard = _require_login(platform)
        if guard:
            return guard

        order = find_order(platform, order_id)
        if order is None:
            return render_template("not_found.html", platform=platform, order_id=order_id), 404

        views = [item_view(order, i, item) for i, item in enumerate(order.items)]
        return render_template(
            "batch_form.html",
            platform=platform,
            order=order,
            views=views,
            reasons=RETURN_REASONS,
        )

    @app.post("/<platform>/returns/batch")
    def batch_create(platform: str):
        guard = _require_login(platform)
        if guard:
            return guard

        order_id = request.form["order_id"]
        order = find_order(platform, order_id)
        selected = request.form.getlist("item")

        entries: list[PlacedReturn] = []
        for raw in selected:
            index = int(raw)
            item = order.items[index]
            existing = STATE.already_returned(order_id, item.product_id)
            if existing:
                entries.append(existing)
                continue
            entry = PlacedReturn(
                platform=platform,
                order_id=order_id,
                product_id=item.product_id,
                return_id=STATE.next_return_id(platform),
                refund_amount=round(item.amount * item.quantity, 2),
                reason=request.form.get(f"reason_{index}") or RETURN_REASONS[0],
                flow="batch",
            )
            STATE.placed.append(entry)
            entries.append(entry)

        # Deliberately reversed: the real Amazon confirmation does not preserve
        # the order-page sequence, and the adapter must key on product id rather
        # than position. Reversing here is what proves it does.
        pairs = list(reversed([(e, order.items[int(i)]) for e, i in zip(entries, selected)]))
        return render_template("batch_confirm.html", platform=platform, order=order, pairs=pairs)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    create_app().run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
