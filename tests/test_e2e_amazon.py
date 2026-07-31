"""End-to-end: Amazon's batch flow, and the fallback when it is not offered.

The point of these tests is that a *batch* action on the platform still produces
*per-line-item* records in the sheet. One click, three rows.
"""

from __future__ import annotations

import pytest

from agent.models import FlowModel, ReturnStatus, TaskStatus
from mock_site.server import STATE

pytestmark = pytest.mark.e2e

BATCH_ORDER = "403-7712345-9911223"
NO_BATCH_ORDER = "403-5540099-1122334"


def test_batch_flow_is_detected_and_used(workbook_for, run_agent):
    workbook = workbook_for(BATCH_ORDER)
    _, log, _ = run_agent(workbook)

    assert any("batch return flow detected" in line for line in log)
    assert any("using batch return flow" in line for line in log)


def test_batch_return_writes_one_row_per_line_item(workbook_for, run_agent, rows_by_sku):
    workbook = workbook_for(BATCH_ORDER)
    report, _, _ = run_agent(workbook)

    rows = rows_by_sku(workbook)
    assert len(rows) == 3

    # Two eligible items, returned in a single batch submission.
    for sku in ("B0C1AZ1111", "B0C1AZ2222"):
        assert rows[sku].return_status == ReturnStatus.PLACED, sku
        assert rows[sku].task_status == TaskStatus.DONE
        assert rows[sku].return_id and rows[sku].return_id.startswith("RMA")

    # The third was past its window and never entered the batch.
    assert rows["B0C1AZ3333"].return_status == ReturnStatus.OUT_OF_WINDOW
    assert rows["B0C1AZ3333"].return_id is None

    assert report.placed == 2
    assert len(STATE.placed) == 2
    assert {p.flow for p in STATE.placed} == {"batch"}


def test_batch_return_ids_are_matched_by_product_not_by_position(workbook_for, run_agent, rows_by_sku):
    """Amazon's confirmation page reorders the items. If the adapter matched
    positionally, these two rows would hold each other's return IDs — so this is
    the assertion that catches a whole class of silent mis-attribution."""
    workbook = workbook_for(BATCH_ORDER)
    run_agent(workbook)

    rows = rows_by_sku(workbook)
    platform_by_product = {p.product_id: p for p in STATE.placed}

    for sku, placed in platform_by_product.items():
        assert rows[sku].return_id == placed.return_id, f"{sku} got the wrong return ID"
        assert rows[sku].refund_amount == placed.refund_amount

    # And the two rows genuinely differ, so the check above is not vacuous.
    assert rows["B0C1AZ1111"].return_id != rows["B0C1AZ2222"].return_id
    assert rows["B0C1AZ1111"].refund_amount == 1299.0
    assert rows["B0C1AZ2222"].refund_amount == 1899.0


def test_missing_batch_entry_falls_back_to_sequential(workbook_for, run_agent, rows_by_sku):
    workbook = workbook_for(NO_BATCH_ORDER)
    report, log, _ = run_agent(workbook)

    assert any("sequential return flow detected" in line for line in log)
    assert any("one pass per line item" in line for line in log)

    rows = rows_by_sku(workbook)
    assert len(rows) == 2
    assert all(r.return_status == ReturnStatus.PLACED for r in rows.values())
    assert len({r.return_id for r in rows.values()}) == 2
    assert {p.flow for p in STATE.placed} == {"sequential"}
    assert report.placed == 2


def test_a_submitted_batch_is_never_retried_sequentially(workbook_for, run_agent, rows_by_sku, monkeypatch):
    """The dangerous case: the batch submits fine, then reading the confirmation
    blows up. If that exception reached the runner it would fall back to the
    sequential flow and return every one of these items a *second* time.

    So the items must end up flagged for a human, and the sequential fallback
    must never be entered.

    The load-bearing assertion is the one on the log. The mock storefront is
    idempotent — a second return for the same product returns the existing entry —
    so the `placed == 2` check alone would still pass on the broken code. A real
    platform offers no such protection, which is exactly why this is guarded in
    the adapter rather than relied upon downstream.
    """
    from agent.platforms.amazon import AmazonAdapter

    def explode(self, pairs):
        raise RuntimeError("confirmation page unreadable")

    monkeypatch.setattr(AmazonAdapter, "_read_batch_confirmation", explode)

    workbook = workbook_for(BATCH_ORDER)
    report, log, _ = run_agent(workbook)

    assert len(STATE.placed) == 2, "the batch was re-submitted — items returned twice"
    assert not any("falling back to sequential" in line for line in log)

    rows = rows_by_sku(workbook)
    for sku in ("B0C1AZ1111", "B0C1AZ2222"):
        assert rows[sku].task_status == TaskStatus.NEEDS_REVIEW
        assert "SUBMITTED" in rows[sku].log
        assert "would return these items twice" in rows[sku].log

    assert report.needs_review == 2


def test_flow_model_is_decided_per_order_not_per_platform(workbook_for, run_agent):
    """Both Amazon orders in one run: one batch, one sequential. A platform-level
    assumption would get one of them wrong."""
    workbook = workbook_for(BATCH_ORDER, NO_BATCH_ORDER)
    report, log, _ = run_agent(workbook)

    joined = "\n".join(log)
    assert FlowModel.BATCH.value in joined
    assert FlowModel.SEQUENTIAL.value in joined
    assert report.placed == 4
    assert {p.flow for p in STATE.placed} == {"batch", "sequential"}
