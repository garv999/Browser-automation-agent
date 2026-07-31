"""End-to-end: what happens when things go wrong.

Partial-success handling is only real if failures are actually injected, so
these tests make the platform misbehave and then check that the damage stayed
inside one line item.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.models import ReturnStatus, TaskStatus
from mock_site.server import STATE

pytestmark = pytest.mark.e2e

MULTI_ITEM_ORDER = "OD337974610559997100"
CLEAN_ORDER = "OD337960018546978100"


def test_transient_platform_error_is_retried(workbook_for, run_agent, rows_by_sku):
    """The platform errors once. The agent should try again rather than write
    off a return the user is entitled to."""
    workbook = workbook_for(CLEAN_ORDER)
    STATE.fail_next_return = True

    report, log, _ = run_agent(workbook)

    row = rows_by_sku(workbook)["ETHH7Z3FJTCRQQNB"]
    assert row.return_status == ReturnStatus.PLACED
    assert row.task_status == TaskStatus.DONE
    assert any("attempt 1 failed" in line for line in log)
    assert report.placed == 1


def test_a_failing_item_does_not_abandon_the_rest_of_the_order(workbook_for, run_agent, rows_by_sku):
    """One line item fails permanently. The other eligible item on the same
    order must still be returned — never abandon the order because of one SKU."""
    workbook = workbook_for(MULTI_ITEM_ORDER)
    STATE.fail_next_return = True

    report, _, _ = run_agent(workbook, max_attempts_per_item=1)

    rows = rows_by_sku(workbook)

    failed = rows["JEAHJHY3CBJYNZNW"]
    assert failed.return_status == ReturnStatus.FAILED
    assert failed.task_status == TaskStatus.NEEDS_REVIEW
    assert "no return confirmation shown after submit" in failed.log

    survived = rows["JEAH87B2GRCCS3DZ"]
    assert survived.return_status == ReturnStatus.PLACED
    assert survived.task_status == TaskStatus.DONE
    assert survived.return_id

    # And every row still ended with a recorded state.
    assert all(r.task_status != TaskStatus.PENDING for r in rows.values())
    assert report.needs_review == 2  # the failed item plus the support-needed one


def test_unknown_order_flags_all_of_its_line_items(tmp_path, run_agent, rows_by_sku):
    """The order is not on the account. Every one of its rows must say so — the
    rows must not be left Pending for a run that will never find them either."""
    from scripts.seed_workbook import write

    workbook = tmp_path / "missing_order.xlsx"
    write(
        [
            {
                "platform": "Flipkart",
                "order_id": "OD000000000000",
                "sku": f"SKU{n}",
                "product_name": f"Ghost Product {n}",
                "product_link": f"https://www.flipkart.com/p/itmghost?pid=SKU{n}",
                "quantity": 1,
                "amount": 100.0 * n,
                "order_date": None,
                "delivery_date": None,
                "return_window_days": None,
                "address": "",
                "contact_number": "",
            }
            for n in (1, 2)
        ],
        workbook,
    )

    run_agent(workbook)

    rows = rows_by_sku(workbook)
    assert len(rows) == 2
    for row in rows.values():
        assert row.task_status == TaskStatus.NEEDS_REVIEW
        assert row.return_status == ReturnStatus.SUPPORT_NEEDED
        assert "not reachable" in row.log
    assert STATE.placed == []


def test_session_cap_stops_work_and_leaves_the_rest_pending(workbook_for, run_agent, rows_by_sku):
    """Volume is what gets an account flagged, so the cap must actually bite —
    and the items it defers must stay Pending so the next run picks them up.

    Note the cap defers *every* remaining item once it is hit, including ones
    that would not have placed a return. The agent cannot know that in advance,
    and deferring is the safe way to be wrong.
    """
    workbook = workbook_for(MULTI_ITEM_ORDER)

    def capped(cfg):
        cfg.humanize.max_returns_per_session = 1

    report, log, _ = run_agent(workbook, _mutate=capped)

    rows = rows_by_sku(workbook)
    placed = [r for r in rows.values() if r.return_status == ReturnStatus.PLACED]
    pending = [r for r in rows.values() if r.task_status == TaskStatus.PENDING]

    assert len(placed) == 1
    assert len(pending) == 2
    assert report.skipped_unvisited == 2
    assert any("session cap reached" in line for line in log)
    assert len(STATE.placed) == 1

    # The out-of-window item was settled before the cap could apply, because it
    # never needed the platform at all.
    assert rows["DREHHF5SKMVFGUKU"].return_status == ReturnStatus.OUT_OF_WINDOW


def test_a_second_run_picks_up_what_the_first_left_pending(workbook_for, run_agent, rows_by_sku):
    """The resumability property: nothing is lost between runs, and nothing
    already returned is returned twice."""
    workbook = workbook_for(MULTI_ITEM_ORDER)

    def capped(cfg):
        cfg.humanize.max_returns_per_session = 1

    run_agent(workbook, _mutate=capped)
    first_pass = rows_by_sku(workbook)
    assert sum(1 for r in first_pass.values() if r.task_status == TaskStatus.PENDING) == 2

    report2, _, _ = run_agent(workbook)

    rows = rows_by_sku(workbook)
    assert all(r.task_status != TaskStatus.PENDING for r in rows.values())
    assert sum(1 for r in rows.values() if r.return_status == ReturnStatus.PLACED) == 2
    # Exactly two returns on the platform in total — the first run's return was
    # not placed a second time.
    assert len(STATE.placed) == 2
