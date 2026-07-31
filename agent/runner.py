"""Orchestration.

The runner owns the two rules that the spec cares most about, and it owns them
in one place so no adapter can violate them:

**Every line item gets its own recorded outcome.** Work is grouped by order only
to avoid reloading the same page; the unit of write-back is always the row.

**No failure is allowed to widen.** Failure is contained at three levels — one
line item, one order, one run. An exception on item C writes C's row and moves
to item D. An unreachable order writes every one of its rows and moves to the
next order. Nothing is ever left silently Pending after being attempted.
"""

from __future__ import annotations

import traceback
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from . import eligibility, excel_io
from .browser import BrowserSession
from .config import AgentConfig
from .humanize import Humanizer, SessionPacer
from .models import (
    FlowModel,
    LineItem,
    Platform,
    ReturnOutcome,
    ReturnStatus,
    ReturnTask,
    TaskStatus,
)
from .platforms import AdapterContext, LoginRequired, OrderNotFound, build_adapter


class RunReport:
    """Tallies of what the run did, for the console summary and for tests."""

    def __init__(self) -> None:
        self.attempted = 0
        self.placed = 0
        self.out_of_window = 0
        self.needs_review = 0
        self.skipped_unvisited = 0
        self.orders_fully_settled: list[str] = []
        self.orders_partial: list[str] = []
        self.stopped_early: Optional[str] = None

    def record(self, outcome: ReturnOutcome) -> None:
        self.attempted += 1
        if outcome.return_status == ReturnStatus.PLACED:
            self.placed += 1
        elif outcome.return_status == ReturnStatus.OUT_OF_WINDOW:
            self.out_of_window += 1
        if outcome.task_status == TaskStatus.NEEDS_REVIEW:
            self.needs_review += 1

    def __str__(self) -> str:
        lines = [
            f"line items processed : {self.attempted}",
            f"  returns placed     : {self.placed}",
            f"  out of window      : {self.out_of_window}",
            f"  needs human review : {self.needs_review}",
            f"orders fully settled : {len(self.orders_fully_settled)}",
            f"orders still partial : {len(self.orders_partial)}",
        ]
        if self.skipped_unvisited:
            lines.append(f"left pending (session cap): {self.skipped_unvisited}")
        if self.stopped_early:
            lines.append(f"stopped early: {self.stopped_early}")
        return "\n".join(lines)


class ReturnsAgent:
    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        logger: Optional[Callable[[str], None]] = None,
        otp_provider: Optional[Callable[[str], str]] = None,
        today: Optional[date] = None,
    ):
        self.config = config or AgentConfig()
        self.log = logger or print
        self.otp_provider = otp_provider
        self.today = today
        self.humanizer = Humanizer(self.config.humanize)
        self.pacer = SessionPacer(self.config.humanize, self.humanizer)
        self.report = RunReport()

    # -- entry point ------------------------------------------------------

    def run(self, session: Optional[BrowserSession] = None) -> RunReport:
        tasks = excel_io.read_tasks(self.config.workbook, self.config.sheet_name)
        pending = excel_io.pending_tasks(tasks)
        self.log(f"{len(pending)} pending line item(s) across {len(tasks)} row(s)")
        if not pending:
            return self.report

        owns_session = session is None
        session = session or BrowserSession(self.config.browser)
        if owns_session:
            session.start()

        try:
            self._run_groups(session, tasks, pending)
        finally:
            if owns_session:
                session.close()

        return self.report

    # -- grouping ---------------------------------------------------------

    @staticmethod
    def group_by_order(tasks: Iterable[ReturnTask]) -> "OrderedDict[tuple[Platform, str], list[ReturnTask]]":
        """Group line items by order, preserving sheet order.

        Grouping is a page-load optimisation and a batch-flow prerequisite — it
        is never a write-back unit.
        """
        groups: "OrderedDict[tuple[Platform, str], list[ReturnTask]]" = OrderedDict()
        for task in tasks:
            groups.setdefault((task.platform, task.order_id), []).append(task)
        return groups

    def _run_groups(self, session: BrowserSession, all_tasks: list[ReturnTask], pending: list[ReturnTask]) -> None:
        groups = self.group_by_order(pending)
        adapters: dict[Platform, object] = {}

        for (platform, order_id), tasks in groups.items():
            if self.pacer.exhausted:
                self.report.skipped_unvisited += len(tasks)
                self.report.stopped_early = (
                    f"session cap of {self.config.humanize.max_returns_per_session} returns reached; "
                    f"remaining items left Pending for the next run"
                )
                self.log(self.report.stopped_early)
                continue

            try:
                adapter = adapters.get(platform)
                if adapter is None:
                    adapter = self._build_adapter(platform, session)
                    adapter.login()
                    adapters[platform] = adapter

                self._process_order(adapter, order_id, tasks)

            except LoginRequired as exc:
                # Without a session nothing further can be attempted anywhere.
                self._fail_remaining(tasks, f"login required: {exc}")
                self.report.stopped_early = f"authentication failed for {platform.value}: {exc}"
                self.log(self.report.stopped_early)
                break

            except Exception as exc:  # noqa: BLE001 — an order must not kill the run
                self._capture_failure(session, order_id)
                self._fail_remaining(tasks, f"unhandled error on order {order_id}: {exc}")
                self.log(f"order {order_id} failed: {exc}\n{traceback.format_exc(limit=3)}")

            finally:
                self._settle_order(all_tasks, order_id)

    def _build_adapter(self, platform: Platform, session: BrowserSession):
        # A fresh tab per platform — the spec's "open a new browser window/tab
        # for the platform named in that record".
        page = session.new_tab() if session.page is not None else session.page
        ctx = AdapterContext(
            page=page,
            config=self.config,
            humanizer=self.humanizer,
            logger=self.log,
            otp_provider=self.otp_provider,
        )
        return build_adapter(platform, ctx, self.config)

    # -- one order --------------------------------------------------------

    def _process_order(self, adapter, order_id: str, tasks: list[ReturnTask]) -> None:
        self.log(f"--- order {order_id} ({len(tasks)} line item(s)) ---")

        # 1. Cheap eligibility screen from sheet data. Ineligible items are
        #    settled without ever loading a page.
        remaining: list[ReturnTask] = []
        for task in tasks:
            verdict = eligibility.check(task, self.today)
            if verdict.eligible:
                remaining.append(task)
            else:
                self._write(
                    task,
                    ReturnOutcome(
                        verdict.status or ReturnStatus.FAILED,
                        message=f"Skipped: {verdict.reason}",
                    ),
                )
                self.log(f"  {task.label}: skipped — {verdict.reason}")

        if not remaining:
            self.log(f"  no eligible line items on {order_id}; order settled without opening it")
            return

        # 2. Open the order once for every remaining item on it.
        self.pacer.before_task(order_id)
        try:
            adapter.open_order(order_id)
        except OrderNotFound as exc:
            self._fail_remaining(remaining, str(exc), ReturnStatus.SUPPORT_NEEDED)
            return

        items = adapter.list_line_items(order_id)
        self.log(f"  platform shows {len(items)} line item(s) on this order")

        matched: list[Optional[LineItem]] = [adapter.match_line_item(t, items) for t in remaining]

        flow = adapter.detect_flow_model(order_id)
        if flow == FlowModel.BATCH:
            self._run_batch(adapter, order_id, remaining, matched)
        else:
            self._run_sequential(adapter, order_id, remaining, matched)

    def _run_batch(self, adapter, order_id: str, tasks: list[ReturnTask], matched) -> None:
        self.log("  using batch return flow")
        try:
            outcomes = adapter.place_batch_return(tasks, matched)
        except Exception as exc:  # noqa: BLE001
            # Only reachable for failures *before* the batch was submitted — the
            # adapter contains everything after the submit click, precisely so
            # this fallback cannot return the same items twice.
            self.log(f"  batch flow failed before submission ({exc}); falling back to sequential")
            adapter.open_order(order_id)
            self._run_sequential(adapter, order_id, tasks, matched)
            return

        for task in tasks:
            outcome = outcomes.get(task.label) or ReturnOutcome(
                ReturnStatus.SUPPORT_NEEDED,
                message="batch flow returned no result for this line item",
            )
            if outcome.return_status == ReturnStatus.PLACED:
                self.pacer.record_return()
            self._write(task, outcome)

    def _run_sequential(self, adapter, order_id: str, tasks: list[ReturnTask], matched) -> None:
        self.log("  using sequential return flow (one pass per line item)")

        for position, (task, item) in enumerate(zip(tasks, matched)):
            if self.pacer.exhausted:
                self.report.skipped_unvisited += 1
                self.log(f"  {task.label}: left Pending — session cap reached")
                continue

            if position > 0:
                # The previous item's flow navigated away from the order page.
                self.pacer.before_task(order_id)
                try:
                    adapter.open_order(order_id)
                    item = self._rematch(adapter, order_id, task, item)
                except Exception as exc:  # noqa: BLE001
                    self._write(
                        task,
                        ReturnOutcome(
                            ReturnStatus.SUPPORT_NEEDED,
                            message=f"could not reopen order {order_id} for this item: {exc}",
                        ),
                    )
                    continue

            if item is None:
                self._write(
                    task,
                    ReturnOutcome(
                        ReturnStatus.SUPPORT_NEEDED,
                        message=(
                            "line item could not be matched on the platform's order page; "
                            "verify the product link or SKU"
                        ),
                    ),
                )
                self.log(f"  {task.label}: no confident match on the order page")
                continue

            outcome = self._attempt_item(adapter, task, item)
            if outcome.return_status == ReturnStatus.PLACED:
                self.pacer.record_return()
            self._write(task, outcome)

    def _rematch(self, adapter, order_id: str, task: ReturnTask, previous: Optional[LineItem]):
        """Re-resolve the item after a page reload; indices can shift once an
        earlier item on the same order enters 'return requested' state."""
        items = adapter.list_line_items(order_id)
        return adapter.match_line_item(task, items) or previous

    def _attempt_item(self, adapter, task: ReturnTask, item: LineItem) -> ReturnOutcome:
        """Run one line item's return, retrying only transient failures."""
        last_error = ""
        for attempt in range(1, self.config.max_attempts_per_item + 1):
            try:
                outcome = adapter.place_return(task, item)
                if outcome.return_status == ReturnStatus.FAILED and attempt < self.config.max_attempts_per_item:
                    last_error = outcome.message
                    self.log(f"  {task.label}: attempt {attempt} failed — {outcome.message}; retrying")
                    self.humanizer.pause_between((3.0, 7.0))
                    adapter.open_order(task.order_id)
                    continue
                return outcome
            except Exception as exc:  # noqa: BLE001 — one item must not kill the order
                last_error = f"{type(exc).__name__}: {exc}"
                self.log(f"  {task.label}: attempt {attempt} raised {last_error}")
                if attempt < self.config.max_attempts_per_item:
                    self.humanizer.pause_between((3.0, 7.0))
                    try:
                        adapter.open_order(task.order_id)
                    except Exception:
                        break

        return ReturnOutcome(
            ReturnStatus.FAILED,
            message=f"return could not be placed after {self.config.max_attempts_per_item} attempt(s): {last_error}",
        )

    # -- write-back -------------------------------------------------------

    def _write(self, task: ReturnTask, outcome: ReturnOutcome) -> None:
        excel_io.write_outcome(
            self.config.workbook, task, outcome, self.config.sheet_name, when=datetime.now()
        )
        self.report.record(outcome)

    def _fail_remaining(
        self,
        tasks: list[ReturnTask],
        message: str,
        status: ReturnStatus = ReturnStatus.FAILED,
    ) -> None:
        """Give every not-yet-settled item in the group a recorded state.

        This is what stops a crash from leaving rows silently Pending — the
        outcome may be 'needs human review', but it is never 'nothing happened
        and nobody knows'.
        """
        for task in tasks:
            if task.task_status == TaskStatus.PENDING:
                self._write(task, ReturnOutcome(status, message=message))

    def _settle_order(self, all_tasks: list[ReturnTask], order_id: str) -> None:
        if excel_io.order_is_fully_settled(all_tasks, order_id):
            self.report.orders_fully_settled.append(order_id)
        else:
            self.report.orders_partial.append(order_id)

    def _capture_failure(self, session: BrowserSession, order_id: str) -> None:
        if not self.config.screenshot_on_failure:
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = Path(self.config.log_dir) / f"failure-{order_id}-{stamp}.png"
        if session.screenshot(path):
            self.log(f"  screenshot written to {path}")
