"""Domain model for the returns agent.

Everything the agent reads and writes is anchored on a single unit of work: one
**line item** on one order. An order with four SKUs is four `ReturnTask`s, each
with its own return window, its own outcome, and its own row in the workbook.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    FLIPKART = "Flipkart"
    AMAZON = "Amazon"


class FlowModel(str, Enum):
    """How a platform lets you return several items from one order.

    BATCH     - a single return flow carries every selected item (Amazon's
                "return or replace items" page).
    SEQUENTIAL- the return micro-flow runs once per line item (Flipkart, and
                Amazon when the batch entry point is unavailable).
    """

    BATCH = "batch"
    SEQUENTIAL = "sequential"


class TaskStatus(str, Enum):
    PENDING = "Pending"
    DONE = "Done"
    NEEDS_REVIEW = "Needs human review"


class ReturnStatus(str, Enum):
    PLACED = "Placed"
    FAILED = "Failed"
    OUT_OF_WINDOW = "Out of window"
    ALREADY_REFUNDED = "Already Cancelled & Refunded"
    NOT_DELIVERED = "Not yet delivered"
    SUPPORT_NEEDED = "Support Needed"


#: Return statuses that represent a settled, non-retryable outcome but which the
#: agent did not itself achieve — a human decides what happens next.
REVIEW_STATUSES = {
    ReturnStatus.FAILED,
    ReturnStatus.SUPPORT_NEEDED,
    ReturnStatus.NOT_DELIVERED,
}


@dataclass
class ReturnTask:
    """One SKU on one order — the atomic unit of work and of write-back."""

    row: int
    """1-based worksheet row this task was read from; write-back targets it."""

    platform: Platform
    order_id: str
    product_link: str

    sku: Optional[str] = None
    product_name: Optional[str] = None
    quantity: int = 1
    amount: Optional[float] = None

    order_date: Optional[date] = None
    delivery_date: Optional[date] = None
    return_window_days: Optional[int] = None

    task_status: TaskStatus = TaskStatus.PENDING
    return_id: Optional[str] = None
    return_status: Optional[ReturnStatus] = None
    refund_amount: Optional[float] = None
    timestamp: Optional[datetime] = None
    log: str = ""

    #: Free-form context carried from the sheet (address, phone) — used only to
    #: disambiguate a line item when several SKUs on an order look alike.
    notes: dict = field(default_factory=dict)

    @property
    def is_pending(self) -> bool:
        return self.task_status == TaskStatus.PENDING

    @property
    def label(self) -> str:
        """Short human-readable identity used in logs."""
        what = self.sku or self.product_name or self.product_link[:48]
        return f"{self.platform.value}/{self.order_id}/{what}"


@dataclass
class ReturnOutcome:
    """What a platform adapter reports back for a single line item."""

    return_status: ReturnStatus
    return_id: Optional[str] = None
    refund_amount: Optional[float] = None
    message: str = ""

    @property
    def task_status(self) -> TaskStatus:
        """Map a platform outcome onto the task's terminal state.

        `Placed`, `Out of window` and `Already refunded` are final and correct —
        the agent did its job and nothing is left to decide. Everything else is
        surfaced to a human rather than silently dropped.
        """
        if self.return_status in REVIEW_STATUSES:
            return TaskStatus.NEEDS_REVIEW
        return TaskStatus.DONE


@dataclass
class LineItem:
    """A line item as discovered on the platform's own order page."""

    index: int
    title: str
    product_id: Optional[str] = None
    amount: Optional[float] = None
    returnable: bool = True
    reason_unreturnable: str = ""
