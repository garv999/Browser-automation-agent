"""Return-window eligibility.

Checked twice, deliberately:

* **Before** opening the browser, from the sheet's own dates. An item that is
  clearly past its window costs nothing to skip and saves a page load — and
  every avoided page load is one less request against a rate-limited account.
* **Again** on the platform page, from what the site itself says. The sheet is
  input data and can be stale; the platform is the authority. If the site offers
  no return control, that verdict wins over the spreadsheet's optimism.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .models import ReturnStatus, ReturnTask


@dataclass
class Eligibility:
    eligible: bool
    reason: str
    status: Optional[ReturnStatus] = None
    days_left: Optional[int] = None


def check(task: ReturnTask, today: Optional[date] = None) -> Eligibility:
    """Decide, from sheet data alone, whether this line item is worth attempting."""
    today = today or date.today()

    if task.return_window_days is None:
        # No window on the sheet is not a reason to skip — let the platform
        # decide. Silently dropping the item would violate partial-success.
        return Eligibility(True, "no return window on record; deferring to the platform")

    if task.delivery_date is None:
        return Eligibility(
            False,
            "no delivery date on record, so the return window cannot be computed",
            ReturnStatus.NOT_DELIVERED,
        )

    if task.delivery_date > today:
        return Eligibility(
            False,
            f"delivery date {task.delivery_date:%Y-%m-%d} is in the future; item not delivered yet",
            ReturnStatus.NOT_DELIVERED,
        )

    # The window is inclusive of the last day: an item delivered on the 1st with
    # a 10-day window is still returnable on the 11th.
    days_since = (today - task.delivery_date).days
    days_left = task.return_window_days - days_since

    if days_left < 0:
        return Eligibility(
            False,
            (
                f"return window closed: delivered {task.delivery_date:%Y-%m-%d}, "
                f"{task.return_window_days}-day window expired "
                f"{abs(days_left)} day(s) ago"
            ),
            ReturnStatus.OUT_OF_WINDOW,
            days_left,
        )

    return Eligibility(
        True,
        f"within return window, {days_left} day(s) remaining",
        days_left=days_left,
    )
