"""The mock storefront itself.

If the mock is wrong the end-to-end tests prove nothing, so its behaviour is
pinned down independently before anything drives it with a browser.
"""

from __future__ import annotations

import pytest

from mock_site.server import MOCK_OTP, STATE, create_app


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def login(client, platform="flipkart"):
    client.post(f"/{platform}/account/login", data={"phone": "9000000001"})
    return client.post(f"/{platform}/account/otp", data={"otp": MOCK_OTP})


def test_orders_require_authentication(client):
    response = client.get("/flipkart/orders")
    assert response.status_code == 302
    assert "/account/login" in response.headers["Location"]


def test_wrong_otp_is_rejected(client):
    client.post("/flipkart/account/login", data={"phone": "9000000001"})
    response = client.post("/flipkart/account/otp", data={"otp": "000000"})
    assert b"Incorrect OTP" in response.data
    assert client.get("/flipkart/orders").status_code == 302


def test_login_then_orders_shows_the_account_marker(client):
    login(client)
    page = client.get("/flipkart/orders")
    assert page.status_code == 200
    assert b'data-testid="account-menu"' in page.data


def test_out_of_window_item_shows_no_return_button(client):
    login(client)
    page = client.get("/flipkart/orders/OD337915105120").data.decode()
    assert 'data-testid="return-unavailable"' in page
    assert "Return window closed" in page
    assert 'data-testid="return-button"' not in page


def test_support_needed_item_shows_neither_button_nor_reason(client):
    """The sheet's hardest case: the platform simply offers nothing. The agent
    has to notice the absence rather than read an explanation."""
    login(client)
    page = client.get("/flipkart/orders/OD337974610559").data.decode()
    assert "Shivanshcloset" in page
    assert page.count('data-testid="return-button"') == 2  # only the two eligible jeans


def test_placing_a_return_yields_an_id_and_a_refund(client):
    login(client)
    response = client.post(
        "/flipkart/returns/create",
        data={"order_id": "OD337960018546", "index": "0", "reason": "Item is not as described"},
    )
    body = response.data.decode()
    assert 'data-testid="return-confirmation"' in body
    assert "CR2606" in body
    assert len(STATE.placed) == 1
    assert STATE.placed[0].refund_amount == 686.0


def test_amazon_batch_entry_appears_only_on_batch_orders(client):
    login(client, "amazon")
    with_batch = client.get("/amazon/orders/403-7712345-9911223").data.decode()
    without = client.get("/amazon/orders/403-5540099-1122334").data.decode()

    assert 'data-testid="batch-return-entry"' in with_batch
    assert 'data-testid="batch-return-entry"' not in without


def test_batch_confirmation_lists_one_return_id_per_item(client):
    login(client, "amazon")
    response = client.post(
        "/amazon/returns/batch",
        data={"order_id": "403-7712345-9911223", "item": ["0", "1"]},
    )
    body = response.data.decode()
    assert body.count('data-testid="confirmed-item"') == 2
    assert len({p.return_id for p in STATE.placed}) == 2


def test_batch_confirmation_is_deliberately_out_of_order(client):
    """Amazon does not preserve the order-page sequence here. The mock mirrors
    that so a positionally-matching adapter fails loudly instead of quietly
    attaching the wrong return ID to a row."""
    login(client, "amazon")
    body = client.post(
        "/amazon/returns/batch",
        data={"order_id": "403-7712345-9911223", "item": ["0", "1"]},
    ).data.decode()

    first_listed = body.index("B0C1AZ2222")
    second_listed = body.index("B0C1AZ1111")
    assert first_listed < second_listed


def test_unknown_order_is_a_404(client):
    login(client)
    assert client.get("/flipkart/orders/OD-DOES-NOT-EXIST").status_code == 404


def test_fail_next_makes_one_return_error(client):
    login(client)
    client.post("/__fail_next")
    response = client.post(
        "/flipkart/returns/create",
        data={"order_id": "OD337960018546", "index": "0"},
    )
    assert response.status_code == 500
    assert b"data-testid=\"return-confirmation\"" not in response.data
    assert STATE.placed == []
