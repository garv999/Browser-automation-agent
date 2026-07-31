"""Return-window arithmetic — the check that decides whether an item is even
worth a page load, and the one that produces 'Out of window' in the sheet."""

from __future__ import annotations

from datetime import date, timedelta

from agent import eligibility
from agent.models import Platform, ReturnStatus, ReturnTask

TODAY = date(2026, 7, 31)


def task(**kwargs) -> ReturnTask:
    base = dict(
        row=2,
        platform=Platform.FLIPKART,
        order_id="OD1",
        product_link="https://www.flipkart.com/p/itmx?pid=ABC",
        return_window_days=10,
        delivery_date=TODAY - timedelta(days=3),
    )
    base.update(kwargs)
    return ReturnTask(**base)


def test_item_inside_window_is_eligible():
    verdict = eligibility.check(task(), TODAY)
    assert verdict.eligible
    assert verdict.days_left == 7


def test_last_day_of_window_is_still_eligible():
    """A 10-day window on an item delivered 10 days ago must not be rejected —
    an off-by-one here silently refuses returns the user is entitled to."""
    verdict = eligibility.check(task(delivery_date=TODAY - timedelta(days=10)), TODAY)
    assert verdict.eligible
    assert verdict.days_left == 0


def test_one_day_past_window_is_out_of_window():
    verdict = eligibility.check(task(delivery_date=TODAY - timedelta(days=11)), TODAY)
    assert not verdict.eligible
    assert verdict.status == ReturnStatus.OUT_OF_WINDOW
    assert "1 day(s) ago" in verdict.reason


def test_future_delivery_is_not_delivered_yet():
    verdict = eligibility.check(task(delivery_date=TODAY + timedelta(days=2)), TODAY)
    assert not verdict.eligible
    assert verdict.status == ReturnStatus.NOT_DELIVERED


def test_missing_delivery_date_is_flagged_not_guessed():
    verdict = eligibility.check(task(delivery_date=None), TODAY)
    assert not verdict.eligible
    assert verdict.status == ReturnStatus.NOT_DELIVERED


def test_missing_window_defers_to_the_platform():
    """No window on the sheet is missing input, not a refusal. Skipping here
    would drop an item the platform might well accept."""
    verdict = eligibility.check(task(return_window_days=None, delivery_date=None), TODAY)
    assert verdict.eligible
    assert "deferring to the platform" in verdict.reason
