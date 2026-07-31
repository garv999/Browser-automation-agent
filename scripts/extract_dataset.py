"""Extract the transaction dataset from `Data Set (2).pdf` into a tidy CSV.

The PDF is a printed spreadsheet, so the text layer collapses the amount and the
narration into a single token (e.g. ``1342IMPS``). The regex below re-splits them
and asserts that every non-header line was consumed, so a silent partial parse
cannot slip through.

Usage:
    python scripts/extract_dataset.py "/path/to/Data Set (2).pdf"
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

ROW_RE = re.compile(
    r"(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<user_id>\d+)\s+"
    r"(?P<amount>\d+)"
    r"(?P<narration>[A-Za-z]+)\s+"
    r"(?P<txn_type>CREDIT|DEBIT)\s+"
    r"(?P<txn_id>\S+)\s+"
    r"(?P<month>\d+)\s+"
    r"(?P<month2>\d+)"
)

FIELDS = [
    "transaction_time",
    "user_id",
    "transaction_amt",
    "narration",
    "transaction_type",
    "txn_id",
    "month",
    "month2",
]


def extract(pdf_path: Path) -> list[dict[str, str]]:
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(pdf_path)).pages)

    rows: list[dict[str, str]] = []
    for match in ROW_RE.finditer(text):
        g = match.groupdict()
        rows.append(
            {
                "transaction_time": g["date"],
                "user_id": g["user_id"],
                "transaction_amt": g["amount"],
                "narration": g["narration"].upper(),
                "transaction_type": g["txn_type"],
                "txn_id": g["txn_id"],
                "month": g["month"],
                "month2": g["month2"],
            }
        )

    unmatched = [
        line
        for line in text.splitlines()
        if line.strip() and "Transaction" not in line and not ROW_RE.search(line)
    ]
    if unmatched:
        raise SystemExit(
            f"{len(unmatched)} line(s) did not parse; refusing to write a partial CSV.\n"
            + "\n".join(unmatched[:10])
        )
    return rows


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)

    rows = extract(Path(sys.argv[1]))
    out = Path(__file__).resolve().parents[1] / "analytics" / "data" / "transactions.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"wrote {len(rows)} rows -> {out}")


if __name__ == "__main__":
    main()
