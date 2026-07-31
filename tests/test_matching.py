"""Line-item matching.

Attaching a return to the wrong SKU is the worst outcome this agent can produce:
it refunds the wrong product and the sheet records a lie. So the matcher is
required to *decline* whenever the winner is not clear, and these tests pin that
behaviour down rather than just checking the happy path.
"""

from __future__ import annotations

from agent.models import LineItem, Platform, ReturnTask
from agent.platforms.base import AdapterContext, PlatformAdapter, Selectors
from agent.platforms.flipkart import product_id_from_link


class _Matcher(PlatformAdapter):
    """Only `match_line_item` is under test, so the flow methods stay unused."""

    platform = Platform.FLIPKART

    def is_logged_in(self):  # pragma: no cover - not exercised
        return True

    def login(self):  # pragma: no cover
        ...

    def open_order(self, order_id):  # pragma: no cover
        ...

    def list_line_items(self, order_id):  # pragma: no cover
        return []

    def place_return(self, task, item):  # pragma: no cover
        ...


def matcher() -> _Matcher:
    empty = Selectors(*[""] * 22)
    return _Matcher(AdapterContext(page=None, config=None, humanizer=None), empty, "http://x")


def task(**kwargs) -> ReturnTask:
    base = dict(row=2, platform=Platform.FLIPKART, order_id="OD1", product_link="")
    base.update(kwargs)
    return ReturnTask(**base)


def test_product_id_is_extracted_from_a_flipkart_link():
    link = (
        "https://www.flipkart.com/wearza-colorblock-men-round-neck-black-yellow-t-shirt/p/"
        "itm709cb9948322a?pid=TSHG9FQZSSAUGKUP&lid=LSTTSHG9FQZSSAUGKUPDCX8GY&marketplace=FLIPKART"
    )
    assert product_id_from_link(link) == "TSHG9FQZSSAUGKUP"


def test_link_without_a_pid_yields_nothing():
    assert product_id_from_link("https://www.flipkart.com/orders") is None


def test_sku_match_wins_outright():
    items = [
        LineItem(0, "Some Blue Jeans", product_id="JEAHJHY3CBJYNZNW"),
        LineItem(1, "Some Blue Jeans", product_id="JEAH87B2GRCCS3DZ"),
    ]
    found = matcher().match_line_item(task(sku="JEAH87B2GRCCS3DZ"), items)
    assert found.index == 1


def test_title_match_resolves_distinct_products():
    items = [
        LineItem(0, "Dolsia Regular Women Blue Jeans", product_id="A"),
        LineItem(1, "Vasan Women A-Line Maroon Midi Calf Length Dress", product_id="B"),
    ]
    found = matcher().match_line_item(
        task(product_name="Vasan Women A-Line Maroon Midi Calf Length Dress"), items
    )
    assert found.product_id == "B"


def test_near_identical_titles_are_refused_rather_than_guessed():
    """Two jeans with near-identical names and no usable SKU: the correct answer
    is 'I don't know', which the runner turns into a human-review flag."""
    items = [
        LineItem(0, "Tokyo Talkies Loose Fit Women Blue Jeans", product_id=None),
        LineItem(1, "Tokyo Talkies Loose Fit Women Blue Jeans", product_id=None),
    ]
    assert matcher().match_line_item(task(product_name="Tokyo Talkies Loose Fit Women Blue Jeans"), items) is None


def test_single_item_order_matches_without_a_title():
    items = [LineItem(0, "Anything At All", product_id="Z")]
    assert matcher().match_line_item(task(), items).product_id == "Z"


def test_empty_order_page_matches_nothing():
    assert matcher().match_line_item(task(sku="A"), []) is None


def test_unknown_sku_falls_through_to_title_matching():
    items = [
        LineItem(0, "Bunaai Dhaga Women Kurta Palazzo Dupatta Set", product_id="P1"),
        LineItem(1, "Prestige Electric Kettle", product_id="P2"),
    ]
    found = matcher().match_line_item(
        task(sku="NOT-ON-THIS-ORDER", product_name="Bunaai Dhaga Women Kurta Palazzo Dupatta Set"),
        items,
    )
    assert found.product_id == "P1"
