# Browser Agent for Automated Multi-Item Returns

A browser agent that reads pending return tasks from Excel, places the returns on
Flipkart and Amazon, and writes the outcome back per line item.

| | |
|---|---|
| **Part 1** | Returns agent: `agent/`, `mock_site/`, `tests/` |
| **Part 2** | SQL and statistics: `analytics/`, answers in [`analytics/outputs/RESULTS.md`](analytics/outputs/RESULTS.md) |

## Quick start

```bash
python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium

# Seed a workbook and start the mock storefront
./.venv/bin/python scripts/seed_workbook.py --source mock
./.venv/bin/python -m mock_site.server --port 8765 &

# Run the agent against it
./.venv/bin/python -m agent.cli --workbook data/return_tasks.xlsx \
    run --mock http://127.0.0.1:8765 --otp 123456

# Review the results
./.venv/bin/python -m agent.cli --workbook data/return_tasks.xlsx status
```

For the live platforms, drop `--mock` and `--otp`. The agent prompts for the OTP
on stdin when Flipkart asks for one.

## What a run produces

13 line items across 7 orders, both platforms, five distinct outcomes:

```
order_id               sku                return_status                return_id          task_status
OD337960018546978100         ETHH7Z3FJTCRQQNB   Placed                       CR26060000000001   Done
OD337915105120141100         VPAHNMQYW9PYYWH8   Out of window                None               Done
OD337915012166989100         TSHG9FQZSSAUGKUP   Already Cancelled & Refunded None               Done
OD337983703007211100         HMBH8MV7VA3PCJDQ   Not yet delivered            None               Needs human review
OD337974610559997100         JEAHJHY3CBJYNZNW   Placed                       CR26060000000002   Done
OD337974610559997100         JEAH87B2GRCCS3DZ   Placed                       CR26060000000003   Done
OD337974610559997100         DREHHF5SKMVFGUKU   Out of window                None               Done
OD337974610559997100         DREHK6H2PN8XX6ZM   Support Needed               None               Needs human review
403-7712345-9911223    B0C1AZ1111         Placed                       RMA88000004        Done
403-7712345-9911223    B0C1AZ2222         Placed                       RMA88000005        Done
403-7712345-9911223    B0C1AZ3333         Out of window                None               Done
403-5540099-1122334    B0C1AZ4444         Placed                       RMA88000006        Done
403-5540099-1122334    B0C1AZ5555         Placed                       RMA88000007        Done
```

Order `OD337974610559997100` is the case the brief centres on: one order, four SKUs,
four different outcomes, none of them affecting the others.

## Architecture

```
agent/
├── models.py       ReturnTask, one SKU on one order
├── excel_io.py     read and per-row write-back, flushed to disk immediately
├── eligibility.py  return-window arithmetic
├── humanize.py     timing, input behaviour, session pacing, stealth script
├── browser.py      persistent Chromium context
├── runner.py       orchestration, partial success, failure containment
└── platforms/
    ├── base.py     adapter contract and line-item matching
    ├── flipkart.py sequential flow
    └── amazon.py   batch flow with sequential fallback
```

The runner is platform agnostic. It asks an adapter four things (log in, open an
order, list line items, return one item) and the adapter decides how. That
boundary lets one orchestration loop serve both flow models, and lets the agent
be tested against a mock storefront with no change to the runner.

### Batch and sequential flows

Platforms handle multi-item orders differently, so the flow is chosen **per
order**, not per platform:

* **Sequential** (Flipkart, and Amazon's fallback): the return micro-flow runs
  once per line item.
* **Batch** (Amazon): every eligible item is selected, submitted once, and the
  confirmation lists a separate return ID per item.

Amazon's "Return or replace items" entry point is absent on some orders, so
`detect_flow_model` probes the open order page and falls back to sequential when
it is missing. A batch action still produces one row per line item.

Amazon's batch confirmation does not preserve the order-page sequence, so the
adapter keys confirmations on product ID rather than position. The mock
reproduces the reordering and [a test](tests/test_e2e_amazon.py) asserts on it.

### Write-back

Write-back targets exactly the row the task came from and is flushed to disk
before the agent continues, so a return placed on the platform is always recorded
in the file.

| Column | Written by | Notes |
|---|---|---|
| `platform`, `order_id`, `sku`, `product_link`, `quantity`, `amount` | Input | locates the order and matches the line item |
| `order_date`, `delivery_date`, `return_window_days` | Input | drives the eligibility check |
| `return_id` | Agent | captured from the platform |
| `return_status` | Agent | Placed, Failed, Out of window, Already Cancelled & Refunded, Not yet delivered, Support Needed |
| `refund_amount` | Agent | as shown by the platform |
| `task_status` | Agent | Done, Needs human review, Pending |
| `timestamp`, `log` | Agent | when it ran, plus an appended note per attempt |

`Out of window` and `Already Cancelled & Refunded` map to **Done**: they are
correct final answers. `Failed`, `Support Needed` and `Not yet delivered` map to
**Needs human review**.

### Partial success

Failure is contained at three levels:

* **Line item:** an error on item C writes C's row and moves to item D.
* **Order:** an unreachable order writes every one of its rows and moves on.
* **Run:** a login failure stops the run, and everything already placed is
  already recorded.

Ineligible items are screened from sheet data before the browser opens, so an
expired item costs no page load. The platform's verdict then takes precedence: if
the site says an item cannot be returned, that is what gets recorded.

An order is reported fully settled only once every line item has a final state.

Where the matcher cannot confidently identify which line item a row refers to, it
declines rather than guessing, and the row is flagged for review.

## Bot-detection avoidance

**Fingerprint.** `humanize.STEALTH_JS` runs before any page script and patches
the standard probes: `navigator.webdriver`, missing `window.chrome`, empty plugin
array, `languages`, the Permissions/Notification mismatch, and the SwiftShader
WebGL vendor. Chromium launches with `--disable-blink-features=AutomationControlled`
and without the `--enable-automation` banner.

**Behaviour.** Pauses are drawn from a log-normal distribution rather than a
uniform range, matching the right-skewed shape of real human gaps. Typing is
per-character with a longer beat after separators. Clicks move the pointer along
a jittered multi-step path and land off-centre.

**Volume.** `SessionPacer` caps returns per session (default 25) and pauses
2.5 to 6 seconds between line items, 8 to 20 seconds between orders. When the cap
is reached the run stops cleanly and leaves remaining items Pending.

A persistent browser profile keeps the session alive across runs, so an OTP is
typed once rather than once per run. The agent does not attempt to intercept SMS.

## Importing the supplied sheet

`scripts/import_sheet.py` converts the Test Orders sheet into the agent's schema:

```bash
./.venv/bin/python scripts/import_sheet.py "Test Orders.xlsx" --dry-run
./.venv/bin/python scripts/import_sheet.py "Test Orders.xlsx" --out data/return_tasks.xlsx
```

* **Header aliasing.** `Order Id`, `Return Window`, `Refund ID` and
  `No of Product` are matched case, space and punctuation insensitively, and the
  header row is located rather than assumed.
* **Multi-item cells are expanded.** Order `OD337974610559997100` stacks four product
  links in one `Product Link` cell, interleaved with pasted chat text. The
  importer extracts every URL, keys them by Flipkart's `pid`, removes duplicate
  pastes, and emits one row per SKU.
* **Value formats.** Return windows written as `10 Days` and delivery dates
  written as `27 June` or `5-6 July` are parsed. For a date range the later day
  is used, so an item that is still returnable is not skipped.
* **Count mismatches warn rather than truncate.** If the extracted SKU count
  differs from `No of Product`, every distinct SKU is kept and the difference is
  reported.

`--dry-run` reports what it found, including rows missing a delivery date or
return window, without writing anything.

## Testing

```bash
./.venv/bin/python -m pytest -q                 # everything
./.venv/bin/python -m pytest -q -m "not e2e"    # fast, no browser
```

**98 tests.** The 19 end-to-end tests launch a real Chromium against a real HTTP
server and cover flow detection, matching, eligibility, partial success, retries,
session capping and write-back. Humanised delays are compressed rather than
disabled, so the click and typing paths under test are the ones used in
production.

| File | Covers |
|---|---|
| `test_eligibility.py` | window arithmetic, including the inclusive last day |
| `test_excel_io.py` | per-row write-back, immediate flush, log accumulation, schema errors |
| `test_matching.py` | SKU and title matching, and declining ambiguous matches |
| `test_humanize.py` | stealth coverage, delay distribution, session cap |
| `test_sheet_import.py` | sheet value formats, multi-link cells, header aliases |
| `test_mock_site.py` | the mock storefront itself |
| `test_e2e_flipkart.py` | sequential flow, all outcome types, the four-SKU order |
| `test_e2e_amazon.py` | batch flow, product-ID-keyed confirmations, sequential fallback |
| `test_e2e_resilience.py` | injected errors, retries, unknown orders, session cap, resumability |

The mock storefront reproduces every shape in the supplied sheet: a clean return,
an expired window, an order already cancelled and refunded, one not yet
delivered, a four-SKU order combining all of the above, and an item that offers
neither a return button nor an explanation.

### Notable fixes found by these tests

**Batch submission safety.** If a batch submitted but the confirmation page could
not be read, the resulting error previously reached the runner, whose response to
a batch failure is a sequential retry, which would return every item twice.
Everything after the submit click is now contained in the adapter and those items
are flagged for review.

**Navigation timing.** `wait_for_load_state("domcontentloaded")` reports the
previous document as loaded when a navigation has not yet started, producing
intermittent phantom failures. `PlatformAdapter.wait_for_settle` replaces it,
settling on either a URL change or an expected selector, which covers both
classic navigation and single-page apps.

## CLI

```
agent.cli [--workbook PATH] [--sheet NAME] run|status

run   --mock URL          drive a mock storefront instead of the live site
      --dry-run           walk the whole flow, stop before the final confirm
      --otp CODE          supply the OTP instead of prompting
      --headless          no visible window
      --fast              disable human pacing (mock runs only)
      --max-returns N     session cap
      --reason TEXT       return reason to select
      --today YYYY-MM-DD  override today's date for the window check

status                    counts by task status, and every row needing review
```

Every option is also settable by environment variable. See
[`agent/config.py`](agent/config.py).

Selectors for the live sites are defined in `LIVE_SELECTORS` profiles in each
adapter, so markup changes are handled in one place. Use `--dry-run` for the
first run against a live account: it walks the entire flow and stops before the
final confirm click.

## Part 2: Analytics

Answers, charts and commentary: [`analytics/outputs/RESULTS.md`](analytics/outputs/RESULTS.md).

```bash
./.venv/bin/python scripts/extract_dataset.py "path/to/Data Set (2).pdf"
./.venv/bin/python analytics/run_analytics.py
```

The dataset PDF is a printed spreadsheet whose text layer merges the amount and
narration into one token (`1342IMPS`). The extractor splits them and asserts that
every non-header line was consumed: 501 transactions, 10 users, January to July
2020.

[`analytics/queries.sql`](analytics/queries.sql) holds all five answers and is
executed directly by `run_analytics.py`, so the documented SQL is the SQL that
ran.

Three findings worth noting:

* The data contains a fifth category, `IFT`, on 89 rows. It is reported rather
  than filtered, so the counts sum to all 501 transactions.
* Load amount is not normally distributed. Shapiro-Wilk rejects normality
  (p ≈ 1e-9), excess kurtosis is -1.24, there are no outliers beyond 1.5×IQR, and
  a KS test against a uniform distribution does not reject. The bell curve is
  provided as requested, with the fitted normal shown for comparison.
* Every user's net amount is negative, since the dataset holds 352 credits
  against 149 debits. "Highest net amount" therefore selects the least
  net-credited user.

## Repo layout

```
agent/              the returns agent
mock_site/          local Flipkart and Amazon mock with working return flows
tests/              98 tests, 19 of them full browser runs
scripts/            dataset extraction, workbook seeding, sheet import
analytics/          queries.sql, run_analytics.py, outputs/
data/               seeded workbooks
```

`data/return_tasks.xlsx` matches the mock fixtures.
`data/test_orders_from_sheet.xlsx` is derived from the supplied Test Orders
sheet. Both are regenerated by `scripts/seed_workbook.py`, and a run modifies
them in place, so re-seed to reset.

## Notes

* Task status is read tolerantly: `To Do`, `Pending`, `Open` and a blank cell all
  mean work to do. An unrecognised status is routed to human review rather than
  re-attempted, since a placed return cannot be undone.
* Flipkart login requires a person to enter the OTP, by design.
* Amazon's live login is modelled on the phone and OTP path. Password and CAPTCHA
  variants are routed to human review.
* `import_sheet.py` divides an order's total `Amount` evenly across its line
  items, since the sheet records a per-order total. The refund figure that
  matters is read from the platform.
* The session cap defers all remaining items once reached, including any that
  would not have placed a return. They stay Pending for the next run.
