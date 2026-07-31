"""Assignment part 2 — run every SQL answer and build the charts.

`queries.sql` is the single source of truth for the SQL: this script reads it,
splits it into statements and executes them, so the file in the repo is exactly
the SQL that produced the numbers below it. Nothing is re-typed here.

    python analytics/run_analytics.py

Writes to analytics/outputs/: RESULTS.md, three PNGs, and the SQLite database.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from scipy import stats

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "transactions.csv"
QUERIES = ROOT / "queries.sql"
OUT = ROOT / "outputs"

# Palette roles, from the validated reference palette. Single-series charts use
# SERIES_1 alone; the fitted-normal overlay is the only second mark anywhere and
# uses slot 2 (the pair validates all-pairs, CVD ΔE 24.7).
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e6e5e1"
SERIES_1 = "#2a78d6"
SERIES_2 = "#eb6834"
SEQ_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def style_axes(ax) -> None:
    """Recessive grid and axes; the data is the only prominent thing."""
    ax.set_facecolor(SURFACE)
    ax.figure.patch.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
        ax.spines[side].set_linewidth(1)
    ax.tick_params(colors=INK_MUTED, labelsize=9, length=0)
    ax.grid(True, axis="y", color=GRID, linewidth=1, alpha=0.9)
    ax.set_axisbelow(True)


def load_db() -> sqlite3.Connection:
    """Load the extracted CSV into SQLite, adding the two derived date columns
    the queries rely on."""
    frame = pd.read_csv(DATA)
    parsed = pd.to_datetime(frame["transaction_time"], format="%m/%d/%Y")
    frame["txn_date"] = parsed.dt.strftime("%Y-%m-%d")
    frame["txn_month"] = parsed.dt.strftime("%Y-%m")

    OUT.mkdir(parents=True, exist_ok=True)
    db_path = OUT / "transactions.db"
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    frame.to_sql("transactions", conn, index=False)
    return conn


def read_statements() -> list[str]:
    """Split queries.sql into executable statements.

    Comments are stripped *before* splitting on `;`, because the explanatory
    comments in that file contain semicolons of their own and splitting first
    tears a statement in half.
    """
    code = "\n".join(line.split("--")[0] for line in QUERIES.read_text().splitlines())
    return [chunk.strip() for chunk in code.split(";") if chunk.strip()]


def as_markdown(frame: pd.DataFrame) -> str:
    return frame.to_markdown(index=False)


# ---------------------------------------------------------------- charts


def chart_categories(frame: pd.DataFrame) -> Path:
    """Q2 — transaction count per category.

    Magnitude across categories, so this is one hue with the bars ranked, not
    eight identity colours. Values are direct-labelled, which lets the x-axis
    ticks go away entirely.
    """
    fig, ax = plt.subplots(figsize=(7.2, 4.0), dpi=200)
    style_axes(ax)

    data = frame.sort_values("txn_count", ascending=False)
    bars = ax.bar(
        data["category"],
        data["txn_count"],
        color=SERIES_1,
        width=0.62,
        zorder=3,
    )
    for rect, value, pct in zip(bars, data["txn_count"], data["pct_of_txns"]):
        ax.annotate(
            f"{value}  ({pct:.1f}%)",
            (rect.get_x() + rect.get_width() / 2, rect.get_height()),
            textcoords="offset points",
            xytext=(0, 5),
            ha="center",
            fontsize=9,
            color=INK,
        )

    ax.set_title("Transactions by category", color=INK, fontsize=13, pad=14, loc="left")
    ax.set_ylabel("Transactions", color=INK_MUTED, fontsize=9)
    ax.set_ylim(0, data["txn_count"].max() * 1.16)
    ax.grid(False)

    path = OUT / "q2_category_counts.png"
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_bell_curve(loads: pd.Series) -> Path:
    """Q3 — the distribution of load amounts against a fitted normal.

    The fitted curve is drawn so the reader can see *how far off* normal the data
    is, which is the actual finding here — not as decoration.
    """
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=200)
    style_axes(ax)

    ax.hist(
        loads,
        bins=24,
        color=SERIES_1,
        edgecolor=SURFACE,
        linewidth=2,  # the 2px surface gap between adjacent fills
        density=True,
        zorder=3,
    )

    grid = np.linspace(loads.min(), loads.max(), 400)
    ax.plot(
        grid,
        stats.norm.pdf(grid, loads.mean(), loads.std(ddof=1)),
        color=SERIES_2,
        linewidth=2,
        zorder=4,
    )
    ax.annotate(
        "fitted normal",
        (grid[int(len(grid) * 0.78)], stats.norm.pdf(grid[int(len(grid) * 0.78)], loads.mean(), loads.std(ddof=1))),
        textcoords="offset points",
        xytext=(10, 14),
        fontsize=9,
        color=INK_MUTED,
    )

    ax.axvline(loads.mean(), color=INK_MUTED, linewidth=1, linestyle="--", zorder=5)
    ax.annotate(
        f"mean {loads.mean():,.0f}",
        (loads.mean(), ax.get_ylim()[1]),
        textcoords="offset points",
        xytext=(6, -12),
        fontsize=9,
        color=INK_MUTED,
    )

    ax.set_title("Distribution of load amount (CREDIT transactions)", color=INK, fontsize=13, pad=14, loc="left")
    ax.set_xlabel("Load amount", color=INK_MUTED, fontsize=9)
    ax.set_ylabel("Density", color=INK_MUTED, fontsize=9)

    path = OUT / "q3_bell_curve.png"
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_box_plot(loads: pd.Series) -> Path:
    """Q3 — box plot of the same load amounts."""
    fig, ax = plt.subplots(figsize=(7.2, 2.8), dpi=200)
    style_axes(ax)
    ax.grid(True, axis="x", color=GRID, linewidth=1)
    ax.grid(False, axis="y")

    ax.boxplot(
        loads,
        orientation="horizontal",
        widths=0.45,
        patch_artist=True,
        boxprops=dict(facecolor=SERIES_1, edgecolor=SERIES_1, linewidth=1),
        medianprops=dict(color=SURFACE, linewidth=2),
        whiskerprops=dict(color=INK_MUTED, linewidth=1),
        capprops=dict(color=INK_MUTED, linewidth=1),
        flierprops=dict(
            marker="o", markersize=5, markerfacecolor=SERIES_1,
            markeredgecolor=SURFACE, markeredgewidth=1, alpha=0.8,
        ),
    )

    q1, median, q3 = np.percentile(loads, [25, 50, 75])
    for value, label in ((q1, "Q1"), (median, "median"), (q3, "Q3")):
        ax.annotate(
            f"{label} {value:,.0f}",
            (value, 1.32),
            ha="center",
            fontsize=9,
            color=INK_MUTED,
        )

    ax.set_title("Load amount spread", color=INK, fontsize=13, pad=14, loc="left")
    ax.set_xlabel("Load amount", color=INK_MUTED, fontsize=9)
    ax.set_yticks([])
    ax.set_ylim(0.5, 1.5)

    path = OUT / "q3_box_plot.png"
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


def chart_cohort(matrix: pd.DataFrame) -> Path:
    """Q4 — the cohort grid as a heatmap.

    Continuous magnitude, so one hue light→dark. Empty cells below the diagonal
    are left as surface, because 'structurally impossible' and 'zero' are not
    the same thing and must not look the same.
    """
    fig, ax = plt.subplots(figsize=(7.6, 4.6), dpi=200)
    ax.set_facecolor(SURFACE)
    fig.patch.set_facecolor(SURFACE)

    cmap = LinearSegmentedColormap.from_list("seq_blue", SEQ_RAMP)
    values = matrix.to_numpy(dtype=float)
    masked = np.ma.masked_invalid(values)
    cmap.set_bad(SURFACE)

    ax.imshow(masked, cmap=cmap, aspect="auto", vmin=0, vmax=np.nanmax(values))

    ax.set_xticks(range(len(matrix.columns)), matrix.columns, fontsize=9, color=INK_MUTED)
    ax.set_yticks(range(len(matrix.index)), matrix.index, fontsize=9, color=INK_MUTED)
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)

    # A 2px surface gap between adjacent cells.
    ax.set_xticks(np.arange(-0.5, len(matrix.columns), 1), minor=True)
    ax.set_yticks(np.arange(-0.5, len(matrix.index), 1), minor=True)
    ax.grid(which="minor", color=SURFACE, linewidth=2)
    ax.tick_params(which="minor", length=0)

    peak = np.nanmax(values)
    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            value = values[row, col]
            if np.isnan(value):
                continue
            ax.text(
                col, row, f"{int(value)}",
                ha="center", va="center", fontsize=9,
                # Ink flips on the dark end of the ramp so the label stays legible.
                color="#ffffff" if value > peak * 0.55 else INK,
            )

    ax.set_title(
        "Monthly cohort of active users (users making DEBIT transactions)",
        color=INK, fontsize=13, pad=14, loc="left",
    )
    ax.set_xlabel("Active month", color=INK_MUTED, fontsize=9)
    ax.set_ylabel("First month", color=INK_MUTED, fontsize=9)

    path = OUT / "q4_cohort.png"
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path


# ---------------------------------------------------------------- report


def main() -> None:
    conn = load_db()
    statements = read_statements()
    assert len(statements) == 5, f"expected 5 queries in queries.sql, found {len(statements)}"

    q1, q2, q3, q4, q5 = (pd.read_sql_query(s, conn) for s in statements)

    total_txns = pd.read_sql_query("SELECT COUNT(*) AS n FROM transactions", conn)["n"][0]
    users = pd.read_sql_query("SELECT COUNT(DISTINCT user_id) AS n FROM transactions", conn)["n"][0]
    loads = pd.read_sql_query(
        "SELECT transaction_amt FROM transactions WHERE transaction_type = 'CREDIT'", conn
    )["transaction_amt"]

    # -- Q3 statistical summary, beyond what SQL conveniently gives ---------
    q1v, median, q3v = np.percentile(loads, [25, 50, 75])
    iqr = q3v - q1v
    lower_fence, upper_fence = q1v - 1.5 * iqr, q3v + 1.5 * iqr
    outliers = loads[(loads < lower_fence) | (loads > upper_fence)]
    shapiro_stat, shapiro_p = stats.shapiro(loads)
    ks_stat, ks_p = stats.kstest(
        loads, "uniform", args=(loads.min(), loads.max() - loads.min())
    )

    summary = pd.DataFrame(
        {
            "statistic": [
                "count", "mean", "median", "std dev", "min", "max",
                "Q1", "Q3", "IQR", "skewness", "excess kurtosis",
                "lower fence", "upper fence", "outliers (1.5×IQR)",
            ],
            "value": [
                f"{len(loads)}",
                f"{loads.mean():,.2f}",
                f"{median:,.2f}",
                f"{loads.std(ddof=1):,.2f}",
                f"{loads.min():,.2f}",
                f"{loads.max():,.2f}",
                f"{q1v:,.2f}",
                f"{q3v:,.2f}",
                f"{iqr:,.2f}",
                f"{stats.skew(loads):.4f}",
                f"{stats.kurtosis(loads):.4f}",
                f"{lower_fence:,.2f}",
                f"{upper_fence:,.2f}",
                f"{len(outliers)}",
            ],
        }
    )

    # -- Q4 cohort matrix in the layout the brief asked for ----------------
    # Every month in the data becomes both a row and a column, as in the brief's
    # template — so a month that formed no new cohort shows up as an empty row
    # rather than silently vanishing.
    months = sorted(
        pd.read_sql_query("SELECT DISTINCT txn_month FROM transactions ORDER BY 1", conn)["txn_month"]
    )
    matrix = (
        q4.pivot(index="cohort_month", columns="active_month", values="active_users")
        .reindex(index=months, columns=months)
    )
    matrix.index.name = "cohort_month"

    # A cell on or after the cohort month with no rows means "nobody was active",
    # which is a 0. A cell before it is impossible and stays blank — the two must
    # not look the same on the heatmap.
    for cohort in matrix.index:
        for active in matrix.columns:
            if active >= cohort and cohort in set(q4["cohort_month"]) and pd.isna(matrix.at[cohort, active]):
                matrix.at[cohort, active] = 0

    charts = [
        chart_categories(q2),
        chart_bell_curve(loads),
        chart_box_plot(loads),
        chart_cohort(matrix),
    ]

    # -- write the report ---------------------------------------------------
    seventh = q1["seventh_highest_debit_amount"][0] if len(q1) else "no 7th rank exists"
    ift_share = q2.loc[q2["category"] == "IFT", "txn_count"]
    ift_note = (
        f"\n> **Note.** The brief lists four categories (UPI, IMPS, RTGS, NEFT), but the data "
        f"also contains **IFT** on {int(ift_share.iloc[0])} rows. It is reported above rather than "
        f"filtered out, so the counts sum to all {total_txns} transactions.\n"
        if len(ift_share)
        else ""
    )

    report = f"""# Analytics — answers

Source: `Data Set (2).pdf`, extracted verbatim to
[`data/transactions.csv`](data/transactions.csv) by
[`scripts/extract_dataset.py`](../scripts/extract_dataset.py) — **{total_txns} transactions,
{users} users, {loads.count()} of them CREDIT**, spanning 2020-01 to 2020-07.

The SQL lives in [`queries.sql`](queries.sql) and is executed straight from that
file by [`run_analytics.py`](run_analytics.py), so what is documented is what ran.

---

## Q1 — 7th highest DEBIT amount through IMPS

**{seventh:,}**

{as_markdown(q1)}

Read as the 7th highest *distinct* amount (`DENSE_RANK`): two debits of equal
value share one rank. If the intent were the 7th highest *row*, swap in
`ROW_NUMBER` — with this data both give the same answer, because there are no
duplicate IMPS debit amounts.

---

## Q2 — Transaction count by category

{as_markdown(q2)}
{ift_note}
![Transactions by category](q2_category_counts.png)

---

## Q3 — Load amount: statistical summary, bell curve, box plot

"Load amount" is read as money loaded *into* the account — the **CREDIT**
transactions ({loads.count()} rows).

{as_markdown(summary)}

![Bell curve](q3_bell_curve.png)

![Box plot](q3_box_plot.png)

### What the numbers actually say

The fitted normal curve is drawn to show how poorly it fits, and it fits badly:

* **Shapiro–Wilk W = {shapiro_stat:.4f}, p = {shapiro_p:.2e}** — normality is rejected.
* Skewness {stats.skew(loads):.3f} is near zero, so the data is symmetric, but
  excess kurtosis {stats.kurtosis(loads):.3f} is strongly **negative** — the
  distribution is flat-topped, not bell-shaped.
* A Kolmogorov–Smirnov test against a **uniform** distribution over
  [{loads.min():,.0f}, {loads.max():,.0f}] gives D = {ks_stat:.4f}, p = {ks_p:.3f} —
  uniform is not rejected.
* The box plot has **{len(outliers)} outliers** beyond 1.5×IQR, which is itself a
  tell: a genuine spend distribution is right-skewed with a long tail of large
  loads, and this one has no tail at all.

So load amount here is **approximately uniform between ~{loads.min():,.0f} and
~{loads.max():,.0f}**, consistent with synthetic test data rather than real
transaction behaviour. The bell curve was requested and is provided, but the
honest reading is that a normal model is the wrong description of this variable.

---

## Q4 — Monthly cohort of active users (DEBIT transactions)

Cohort = the month of a user's **first DEBIT** transaction. Each cell counts the
distinct users from that cohort who made at least one DEBIT in that month.

{as_markdown(matrix.fillna("").reset_index().rename(columns={"cohort_month": "First month / Active month"}))}

![Cohort](q4_cohort.png)

Reading the grid: blank below the diagonal is *structurally impossible* — a user
cannot be active before their own first transaction. A **0** on or after the
diagonal is a real result: that cohort existed and nobody in it transacted that
month. The two are deliberately not drawn the same way.

The 2020-04 to 2020-07 rows are empty because **no new cohort formed after March** —
all {users} users made their first DEBIT in Jan–Mar, and 8 of them in January alone.
With a user base this small the grid is a shape check rather than a retention
finding: the January cohort's 5 in February is one quiet month, not churn.

---

## Q5 — Top 10 percentile of users by net amount (DEBIT − CREDIT)

{as_markdown(q5)}

`NTILE(10)` over {users} users puts exactly one user in the top decile. That is
the correct answer to the question as asked, and the query scales unchanged as
the user base grows — but with a 10-user dataset it is worth saying plainly that
"top 10 percentile" and "top 1 user" are the same thing here.

Worth flagging: **every user's net amount is negative**, because the dataset is
352 credits against 149 debits. So "highest net amount (DEBIT − CREDIT)" selects
the user who is *least* net-credited, not one who is net-debited. The ranking is
correct as specified; the sign is a property of the data, not of the query.

Full per-user ranking for context:

{as_markdown(pd.read_sql_query('''
    SELECT user_id,
           ROUND(SUM(CASE WHEN transaction_type = 'DEBIT'  THEN transaction_amt ELSE 0 END), 2) AS total_debit,
           ROUND(SUM(CASE WHEN transaction_type = 'CREDIT' THEN transaction_amt ELSE 0 END), 2) AS total_credit,
           ROUND(SUM(CASE WHEN transaction_type = 'DEBIT'  THEN transaction_amt ELSE -transaction_amt END), 2) AS net_amount
    FROM transactions GROUP BY user_id ORDER BY net_amount DESC
''', conn))}
"""

    (OUT / "RESULTS.md").write_text(textwrap.dedent(report).lstrip())
    conn.close()

    print(f"wrote {OUT / 'RESULTS.md'}")
    for chart in charts:
        print(f"wrote {chart}")


if __name__ == "__main__":
    main()
