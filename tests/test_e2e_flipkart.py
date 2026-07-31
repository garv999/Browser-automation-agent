"""End-to-end: Flipkart's sequential flow, driven by a real browser.

Every test here starts from a seeded workbook, runs the whole agent — launch,
login, navigate, click, confirm, write back — and then asserts on what ended up
in the spreadsheet and on what the platform actually recorded. The two have to
agree; a return that exists on one and not the other is the failure this suite
is built to catch.
"""

from __future__ import annotations

import pytest

from agent.models import ReturnStatus, TaskStatus
from mock_site.server import STATE

pytestmark = pytest.mark.e2e

CLEAN_ORDER = "OD337960018546"
OUT_OF_WINDOW_ORDER = "OD337915105120"
ALREADY_REFUNDED_ORDER = "OD337915012166"
UNDELIVERED_ORDER = "OD337983703007"
MULTI_ITEM_ORDER = "OD337974610559"


def test_single_item_order_returns_cleanly(workbook_for, run_agent, rows_by_sku):
    workbook = workbook_for(CLEAN_ORDER)
    report, _, _ = run_agent(workbook)

    assert report.placed == 1
    row = rows_by_sku(workbook)["ETHH7Z3FJTCRQQNB"]

    assert row.return_status == ReturnStatus.PLACED
    assert row.task_status == TaskStatus.DONE
    assert row.return_id and row.return_id.startswith("CR")
    assert row.refund_amount == 686.0
    assert row.timestamp is not None

    # The sheet and the platform must tell the same story.
    assert len(STATE.placed) == 1
    assert STATE.placed[0].return_id == row.return_id


def test_out_of_window_item_is_skipped_without_touching_the_platform(workbook_for, run_agent, rows_by_sku):
    """The window is checked from the sheet first, so an expired item costs no
    page load and no request against a rate-limited account."""
    workbook = workbook_for(OUT_OF_WINDOW_ORDER)
    report, log, _ = run_agent(workbook)

    row = rows_by_sku(workbook)["VPAHNMQYW9PYYWH8"]
    assert row.return_status == ReturnStatus.OUT_OF_WINDOW
    assert row.task_status == TaskStatus.DONE  # a correct final answer, not an error
    assert "return window closed" in row.log.lower()

    assert STATE.placed == []
    assert report.out_of_window == 1
    assert any("settled without opening it" in line for line in log)


def test_undelivered_item_is_flagged_for_a_human(workbook_for, run_agent, rows_by_sku):
    workbook = workbook_for(UNDELIVERED_ORDER)
    run_agent(workbook)

    row = rows_by_sku(workbook)["HMBH8MV7VA3PCJDQ"]
    assert row.return_status == ReturnStatus.NOT_DELIVERED
    assert row.task_status == TaskStatus.NEEDS_REVIEW
    assert STATE.placed == []


def test_platform_verdict_overrides_the_sheet(workbook_for, run_agent, rows_by_sku):
    """The sheet says this item is inside its window; Flipkart says the order was
    already cancelled and refunded. The platform wins, and the sheet records what
    the platform said."""
    workbook = workbook_for(ALREADY_REFUNDED_ORDER)
    run_agent(workbook)

    row = rows_by_sku(workbook)["TSHG9FQZSSAUGKUP"]
    assert row.return_status == ReturnStatus.ALREADY_REFUNDED
    assert row.task_status == TaskStatus.DONE
    assert "already cancelled and refunded" in row.log.lower()
    assert STATE.placed == []


def test_multi_item_order_partial_success(workbook_for, run_agent, rows_by_sku):
    """The core requirement: one order, four SKUs, four different fates, and not
    one of them allowed to sink the others."""
    workbook = workbook_for(MULTI_ITEM_ORDER)
    report, _, _ = run_agent(workbook)

    rows = rows_by_sku(workbook)
    assert len(rows) == 4

    # Two eligible jeans: returned.
    for sku in ("JEAHJHY3CBJYNZNW", "JEAH87B2GRCCS3DZ"):
        assert rows[sku].return_status == ReturnStatus.PLACED, sku
        assert rows[sku].task_status == TaskStatus.DONE
        assert rows[sku].return_id

    # Past its window: skipped, recorded, and not an error.
    assert rows["DREHHF5SKMVFGUKU"].return_status == ReturnStatus.OUT_OF_WINDOW
    assert rows["DREHHF5SKMVFGUKU"].task_status == TaskStatus.DONE
    assert rows["DREHHF5SKMVFGUKU"].return_id is None

    # No return control on the page at all: flagged, never silently dropped.
    assert rows["DREHK6H2PN8XX6ZM"].return_status == ReturnStatus.SUPPORT_NEEDED
    assert rows["DREHK6H2PN8XX6ZM"].task_status == TaskStatus.NEEDS_REVIEW

    # Every line item ended with a state, so the order counts as settled.
    assert all(r.task_status != TaskStatus.PENDING for r in rows.values())
    assert MULTI_ITEM_ORDER in report.orders_fully_settled
    assert report.placed == 2
    assert report.needs_review == 1


def test_each_line_item_gets_its_own_return_id(workbook_for, run_agent, rows_by_sku):
    workbook = workbook_for(MULTI_ITEM_ORDER)
    run_agent(workbook)

    ids = [r.return_id for r in rows_by_sku(workbook).values() if r.return_id]
    assert len(ids) == 2
    assert len(set(ids)) == 2, "two line items must not share one return ID"
    assert {p.return_id for p in STATE.placed} == set(ids)


def test_refund_amounts_come_from_the_platform_not_the_sheet(workbook_for, run_agent, rows_by_sku):
    workbook = workbook_for(MULTI_ITEM_ORDER)
    run_agent(workbook)

    rows = rows_by_sku(workbook)
    placed = {p.product_id: p.refund_amount for p in STATE.placed}
    for sku, refund in placed.items():
        assert rows[sku].refund_amount == refund


def test_whole_workbook_run_settles_every_row(workbook, run_agent, rows_by_sku):
    """A full pass over every fixture order across both platforms. Nothing may
    be left Pending, whatever happened to it."""
    report, _, _ = run_agent(workbook)

    rows = rows_by_sku(workbook)
    pending = [sku for sku, row in rows.items() if row.task_status == TaskStatus.PENDING]
    assert pending == [], f"rows left Pending: {pending}"
    assert all(row.timestamp is not None for row in rows.values())
    assert report.attempted == len(rows)
    assert not report.orders_partial
