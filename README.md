# Browser Agent for Automated Multi-Item Returns

Reads pending return tasks from Excel, places the returns on Flipkart and Amazon,
and writes the outcome back per line item.

| | |
|---|---|
| **Part 1** | Returns agent: `agent/`, `mock_site/`, `tests/` |
| **Part 2** | SQL and statistics: answers in [`analytics/outputs/RESULTS.md`](analytics/outputs/RESULTS.md) |

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium

./.venv/bin/python scripts/seed_workbook.py --source mock
./.venv/bin/python -m mock_site.server --port 8765 &

./.venv/bin/python -m agent.cli --workbook data/return_tasks.xlsx \
    run --mock http://127.0.0.1:8765 --otp 123456
```

For the live sites, drop `--mock` and `--otp` and pass `--phone <number>`. The
agent prompts for the OTP when Flipkart asks for one.

## What a run produces

```
order_id               sku                return_status                return_id          task_status
OD337960018546978100   ETHH7Z3FJTCRQQNB   Placed                       CR26060000000001   Done
OD337915105120141100   VPAHNMQYW9PYYWH8   Out of window                None               Done
OD337915012166989100   TSHG9FQZSSAUGKUP   Already Cancelled & Refunded None               Done
OD337983703007211100   HMBH8MV7VA3PCJDQ   Not yet delivered            None               Needs human review
OD337974610559997100   JEAHJHY3CBJYNZNW   Placed                       CR26060000000002   Done
OD337974610559997100   JEAH87B2GRCCS3DZ   Placed                       CR26060000000003   Done
OD337974610559997100   DREHHF5SKMVFGUKU   Out of window                None               Done
OD337974610559997100   DREHK6H2PN8XX6ZM   Support Needed               None               Needs human review
403-7712345-9911223    B0C1AZ1111         Placed                       RMA88000004        Done
403-7712345-9911223    B0C1AZ2222         Placed                       RMA88000005        Done
403-7712345-9911223    B0C1AZ3333         Out of window                None               Done
403-5540099-1122334    B0C1AZ4444         Placed                       RMA88000006        Done
403-5540099-1122334    B0C1AZ5555         Placed                       RMA88000007        Done
```

`OD337974610559997100` is the case the brief centres on: one order, four SKUs,
four different outcomes, none affecting the others.

## How it works

```
agent/
├── models.py       ReturnTask, one SKU on one order
├── excel_io.py     read and per-row write-back, flushed immediately
├── eligibility.py  return-window arithmetic
├── humanize.py     timing, input behaviour, session pacing, stealth
├── browser.py      persistent Chromium context
├── runner.py       orchestration and failure containment
└── platforms/      base.py, flipkart.py (sequential), amazon.py (batch)
```

The runner is platform agnostic: it asks an adapter to log in, open an order,
list line items and return one item, and the adapter decides how.

**Flow model is chosen per order, not per platform.** Flipkart runs the return
micro-flow once per line item. Amazon submits all eligible items at once and its
confirmation lists a return ID per item; where that entry point is absent the
adapter falls back to sequential. Amazon reorders items on the confirmation page,
so confirmations are keyed by product ID rather than position.

**Write-back is per row and flushed before the agent continues**, so a return
placed on the platform is always recorded. `return_id`, `return_status`,
`refund_amount`, `task_status`, `timestamp` and `log` are agent-written; the rest
come from the sheet. `Out of window` and `Already Cancelled & Refunded` count as
**Done**; `Failed`, `Support Needed` and `Not yet delivered` go to **Needs human
review**.

**Failure is contained at three levels.** An error on one line item still writes
that row and moves to the next; an unreachable order writes all of its rows; a
login failure stops the run with everything already placed recorded. Ineligible
items are screened from sheet data before the browser opens, and the platform's
verdict then overrides the sheet. Ambiguous line-item matches are declined rather
than guessed.

## Bot-detection avoidance

- **Fingerprint:** an init script patches `navigator.webdriver`, missing
  `window.chrome`, the empty plugin array, `languages`, the
  Permissions/Notification mismatch and the SwiftShader WebGL vendor. Chromium
  launches without the automation banner.
- **Behaviour:** log-normal pauses rather than uniform ones, per-character
  typing, and clicks that travel a jittered path and land off-centre.
- **Volume:** a session cap (default 25 returns) with 2.5-6s between line items
  and 8-20s between orders. On reaching the cap the run stops and leaves the rest
  Pending.

A persistent profile keeps the session alive across runs, so the OTP is typed
once rather than every run. The agent does not intercept SMS.

## Importing the supplied sheet

```bash
./.venv/bin/python scripts/import_sheet.py "Test Orders.xlsx" --dry-run
```

Matches headers by alias (`Order Id`, `Return Window`, `Refund ID`), expands
multi-link cells into one row per SKU keyed by Flipkart's `pid`, and parses the
sheet's own value formats (`10 Days`, `27 June`, `5-6 July`). Count mismatches
against `No of Product` warn rather than truncate.

## Testing

```bash
./.venv/bin/python -m pytest -q                 # 98 tests
./.venv/bin/python -m pytest -q -m "not e2e"    # fast, no browser
```

19 of the tests drive a real Chromium against a mock storefront that reproduces
every shape in the supplied sheet, including an item offering neither a return
button nor an explanation. Humanised delays are compressed rather than disabled.

Navigation, session reuse, order lookup, line-item reading, flow detection and
write-back are additionally confirmed against live flipkart.com. The return
submission step itself is not, as no order within its return window was available
to test against.

## CLI

```
agent.cli [--workbook PATH] run|status

run   --mock URL      drive the mock storefront
      --phone NUMBER  login number (never stored in the repo)
      --dry-run       walk the flow, stop before the final confirm
      --otp CODE      supply the OTP instead of prompting
      --otp-file PATH read the OTP from a file when asked
      --headless      no visible window
      --max-returns N session cap
      --today DATE    override today's date for the window check

status                counts by status, and every row needing review
```

`scripts/probe_live.py` reports what a live page actually contains. It only
reads; it never clicks a control that could modify an order.

## Part 2: Analytics

```bash
./.venv/bin/python scripts/extract_dataset.py "Data Set (2).pdf"
./.venv/bin/python analytics/run_analytics.py
```

[`queries.sql`](analytics/queries.sql) holds all five answers and is executed
directly, so the documented SQL is the SQL that ran. The extractor asserts every
non-header line parsed: 501 transactions, 10 users.

Three findings: the data carries a fifth category (`IFT`, 89 rows) not named in
the brief; load amount is uniform rather than normal (Shapiro-Wilk p ≈ 1e-9,
excess kurtosis -1.24, no outliers past 1.5×IQR); and every user's net amount is
negative, since there are 352 credits to 149 debits.

## Notes

- Order IDs are the full 20-character Flipkart values. The source sheet wraps
  them across two lines, and a transcription that stops at the wrap yields an ID
  the platform cannot resolve.
- The sheet's orders were delivered in late June and early July 2026, so their
  windows have closed and the agent correctly reports them out of window.
- Phone numbers and addresses in this repo are placeholders. Pass a real number
  at runtime with `--phone`.
- Amazon fixtures are constructed: the supplied sheet is Flipkart-only, but the
  brief puts Amazon in scope and it is the platform with the batch flow.
- Task status is read tolerantly (`To Do`, `Pending`, `Open`, blank). An
  unrecognised status goes to human review rather than being re-attempted, since
  a placed return cannot be undone.
