"""Fixture orders for the mock storefront.

These are modelled directly on the supplied test-orders sheet, so the agent is
exercised against the same shapes it will meet in production:

* a plain single-item order that returns cleanly,
* an order whose window has expired,
* an order already cancelled and refunded upstream,
* an order that has not been delivered yet,
* a **four-item** order that mixes all of the above — the partial-success case,
* an Amazon order that exposes the batch return flow,
* an Amazon order where the batch entry point is missing, forcing the fallback.

Dates are expressed as *days since delivery* rather than as calendar dates, and
both this module and the workbook seeder derive from that, so the suite behaves
identically whenever it is run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional


@dataclass
class MockItem:
    product_id: str
    title: str
    amount: float
    quantity: int = 1
    return_window_days: int = 10
    days_since_delivery: int = 3
    #: Overrides eligibility entirely — the platform refuses for its own reason.
    blocked_reason: Optional[str] = None
    #: The item is on the order page but exposes no return control at all
    #: (Flipkart's "support needed" case from the sheet).
    no_return_control: bool = False

    @property
    def delivery_date(self) -> date:
        return date.today() - timedelta(days=self.days_since_delivery)

    @property
    def order_date(self) -> date:
        return self.delivery_date - timedelta(days=3)

    @property
    def within_window(self) -> bool:
        return self.days_since_delivery <= self.return_window_days

    def unavailable_reason(self) -> Optional[str]:
        """What the mock page shows instead of a Return button."""
        if self.blocked_reason:
            return self.blocked_reason
        if self.days_since_delivery < 0:
            return "Not yet delivered — out for delivery"
        if not self.within_window:
            return (
                f"Return window closed — this item had a {self.return_window_days} day "
                f"return period"
            )
        return None


@dataclass
class MockOrder:
    order_id: str
    platform: str  # "flipkart" | "amazon"
    items: list[MockItem]
    #: Amazon only: whether the "Return or replace items" entry point is shown.
    batch_return: bool = False
    address: str = "Sample Address, Sector 42, Gurgaon, Haryana - 122009, India"
    contact: str = "9000000001"
    extras: dict = field(default_factory=dict)


FLIPKART_ORDERS = [
    # Clean single-item return — the happy path.
    MockOrder(
        "OD337960018546978100",
        "flipkart",
        [
            MockItem(
                "ETHH7Z3FJTCRQQNB",
                "Bunaai Dhaga Women Kurta Palazzo Dupatta Set",
                686.0,
                return_window_days=10,
                days_since_delivery=2,
            )
        ],
    ),
    # Past its window — the agent must skip it without opening a return.
    MockOrder(
        "OD337915105120141100",
        "flipkart",
        [
            MockItem(
                "VPAHNMQYW9PYYWH8",
                "Gulab Thar Women Kurta",
                350.0,
                return_window_days=7,
                days_since_delivery=35,
            )
        ],
    ),
    # Already cancelled and refunded upstream.
    MockOrder(
        "OD337915012166989100",
        "flipkart",
        [
            MockItem(
                "TSHG9FQZSSAUGKUP",
                "Wearza Colorblock Men Round Neck Black Yellow T-shirt",
                284.0,
                blocked_reason="This order was already cancelled and refunded",
            )
        ],
    ),
    # Not delivered yet.
    MockOrder(
        "OD337983703007211100",
        "flipkart",
        [
            MockItem(
                "HMBH8MV7VA3PCJDQ",
                "Carrylux Women Pink Shoulder Bag",
                395.5,
                quantity=4,
                days_since_delivery=-2,
            )
        ],
    ),
    # The partial-success centrepiece: four SKUs on one order, four fates.
    MockOrder(
        "OD337974610559997100",
        "flipkart",
        [
            MockItem(
                "JEAHJHY3CBJYNZNW",
                "Dolsia Regular Women Blue Jeans",
                645.0,
                days_since_delivery=1,
            ),
            MockItem(
                "JEAH87B2GRCCS3DZ",
                "Tokyo Talkies Loose Fit Women Blue Jeans",
                645.0,
                days_since_delivery=1,
            ),
            MockItem(
                "DREHHF5SKMVFGUKU",
                "Vasan Women A-Line Maroon Midi Calf Length Dress",
                645.0,
                return_window_days=7,
                days_since_delivery=30,  # out of window
            ),
            MockItem(
                "DREHK6H2PN8XX6ZM",
                "Shivanshcloset Women Fit Flare Blue Beige Maxi Full Length Dress",
                644.0,
                days_since_delivery=1,
                no_return_control=True,  # needs a human — no return button rendered
            ),
        ],
    ),
]

AMAZON_ORDERS = [
    # Batch flow: three items, one of them already out of window.
    MockOrder(
        "403-7712345-9911223",
        "amazon",
        [
            MockItem("B0C1AZ1111", "Boat Rockerz 255 Pro Bluetooth Headset", 1299.0, days_since_delivery=4),
            MockItem("B0C1AZ2222", "Wildcraft 35L Laptop Backpack", 1899.0, days_since_delivery=4),
            MockItem(
                "B0C1AZ3333",
                "Amazon Basics HDMI Cable 2m",
                349.0,
                return_window_days=7,
                days_since_delivery=25,
            ),
        ],
        batch_return=True,
    ),
    # No batch entry point — the adapter must fall back to sequential.
    MockOrder(
        "403-5540099-1122334",
        "amazon",
        [
            MockItem("B0C1AZ4444", "Prestige Electric Kettle 1.5L", 1099.0, days_since_delivery=2),
            MockItem("B0C1AZ5555", "Milton Thermosteel Flask 1L", 899.0, days_since_delivery=2),
        ],
        batch_return=False,
    ),
]

ALL_ORDERS = FLIPKART_ORDERS + AMAZON_ORDERS


def orders_for(platform: str) -> list[MockOrder]:
    return [o for o in ALL_ORDERS if o.platform == platform]


def find_order(platform: str, order_id: str) -> Optional[MockOrder]:
    for order in orders_for(platform):
        if order.order_id == order_id:
            return order
    return None
