"""Command-line entry point.

    python -m agent.cli run --workbook data/return_tasks.xlsx
    python -m agent.cli run --mock http://127.0.0.1:8765    # drive the mock storefront
    python -m agent.cli status                              # what is pending / needs review
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

from .config import REPO_ROOT, AgentConfig
from .excel_io import read_tasks
from .models import Platform, TaskStatus
from .runner import ReturnsAgent


def _build_config(args: argparse.Namespace) -> AgentConfig:
    config = AgentConfig()
    if args.workbook:
        config.workbook = Path(args.workbook)
    if args.sheet:
        config.sheet_name = args.sheet
    if getattr(args, "headless", False):
        config.browser.headless = True
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "reason", None):
        config.return_reason = args.reason
    if getattr(args, "max_returns", None):
        config.humanize.max_returns_per_session = args.max_returns
    if getattr(args, "fast", False):
        # Only ever appropriate against the mock site.
        config.humanize.enabled = False
    if getattr(args, "phone", None):
        config.login_phone = args.phone
    if getattr(args, "mock", None):
        base = args.mock.rstrip("/")
        config.browser.base_url_override = {
            Platform.FLIPKART.value: f"{base}/flipkart",
            Platform.AMAZON.value: f"{base}/amazon",
        }
    return config


def _file_otp_provider(path: Path, timeout_s: float):
    """Take the OTP from a file instead of from stdin.

    The code cannot be supplied up front: the platform only sends it once the
    agent has clicked "Request OTP". This provider parks at that point and polls
    a file, so the run works when stdin is not interactive (a background run, a
    CI job, or an operator handing the code over from another window).

    The file is deleted once read, so a stale code cannot be silently reused on
    the next run.
    """

    def provider(phone: str) -> str:
        print(f"\n>>> OTP requested on {phone}.", flush=True)
        print(f">>> Write the 6-digit code to: {path}", flush=True)
        print(f">>>   printf '123456' > {path}", flush=True)

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if path.exists():
                code = path.read_text().strip()
                if code:
                    path.unlink()
                    print(f">>> got OTP ({len(code)} digits), continuing", flush=True)
                    return code
            time.sleep(1)

        print(f">>> no OTP supplied within {timeout_s:.0f}s", flush=True)
        return ""

    return provider


def cmd_run(args: argparse.Namespace) -> int:
    config = _build_config(args)
    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()

    print(f"workbook : {config.workbook}")
    print(f"mode     : {'MOCK ' + args.mock if args.mock else 'LIVE'}"
          f"{' (dry run)' if config.dry_run else ''}")
    print(f"today    : {today}\n")

    if args.otp_timeout:
        config.otp_timeout_s = args.otp_timeout

    if args.otp_file:
        otp_provider = _file_otp_provider(Path(args.otp_file), config.otp_timeout_s)
    elif args.otp:
        otp_provider = lambda phone: args.otp  # noqa: E731
    else:
        otp_provider = None  # prompt on stdin
    agent = ReturnsAgent(config, otp_provider=otp_provider, today=today)
    report = agent.run()

    print("\n=== run summary ===")
    print(report)
    return 0 if not report.stopped_early else 1


def cmd_status(args: argparse.Namespace) -> int:
    config = _build_config(args)
    tasks = read_tasks(config.workbook, config.sheet_name)

    buckets: dict[str, int] = {}
    for task in tasks:
        buckets[task.task_status.value] = buckets.get(task.task_status.value, 0) + 1

    print(f"{len(tasks)} line item(s) in {config.workbook.name}\n")
    for status, count in sorted(buckets.items()):
        print(f"  {status:<20} {count}")

    review = [t for t in tasks if t.task_status == TaskStatus.NEEDS_REVIEW]
    if review:
        print("\nneeds human review:")
        for task in review:
            note = task.log.splitlines()[-1] if task.log else ""
            print(f"  row {task.row}: {task.label}\n      {note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent", description=__doc__)
    parser.add_argument("--workbook", default=None, help="path to the returns workbook")
    parser.add_argument("--sheet", default=None, help="worksheet name (default: Returns)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="process every pending line item")
    run.add_argument("--mock", default=None, help="base URL of the mock storefront")
    run.add_argument("--headless", action="store_true")
    run.add_argument("--fast", action="store_true", help="disable human pacing (mock runs only)")
    run.add_argument("--dry-run", action="store_true", help="stop before the final confirm click")
    run.add_argument("--phone", default=None, help="login phone number (never stored in the repo)")
    run.add_argument("--otp", default=None, help="OTP to use instead of prompting")
    run.add_argument("--otp-file", default=None, help="read the OTP from this file when asked")
    run.add_argument("--otp-timeout", type=float, default=None, help="seconds to wait for an OTP")
    run.add_argument("--reason", default=None, help="return reason to select")
    run.add_argument("--max-returns", type=int, default=None, help="session cap")
    run.add_argument("--today", default=None, help="override today's date (YYYY-MM-DD)")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="summarise the workbook")
    status.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
