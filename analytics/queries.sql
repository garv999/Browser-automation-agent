-- Assignment part 2 — SQL answers.
--
-- Table `transactions` is loaded from analytics/data/transactions.csv, which is
-- extracted verbatim from "Data Set (2).pdf" by scripts/extract_dataset.py.
--
--   transaction_time  TEXT    m/d/yyyy as printed in the source
--   txn_date          TEXT    ISO yyyy-mm-dd, derived on load
--   txn_month         TEXT    yyyy-mm, derived on load
--   user_id           INTEGER
--   transaction_amt   REAL
--   narration         TEXT    IMPS | IFT | UPI | NEFT | RTGS
--   transaction_type  TEXT    CREDIT | DEBIT
--   txn_id            TEXT
--
-- Dialect: SQLite. Every query here is standard SQL that also runs on Postgres
-- or MySQL 8 unchanged, except where a note says otherwise.


-- =====================================================================
-- Q1. 7th highest DEBIT amount transacted through IMPS.
-- =====================================================================
--
-- "7th highest" is read as the 7th highest *distinct* amount. Two debits of the
-- same value are one rank, not two — otherwise a repeated amount silently
-- shifts every rank below it. DENSE_RANK is what encodes that choice; swap it
-- for ROW_NUMBER if the intent is the 7th highest *row* instead.

WITH ranked AS (
    SELECT DISTINCT
           transaction_amt,
           DENSE_RANK() OVER (ORDER BY transaction_amt DESC) AS amount_rank
    FROM   transactions
    WHERE  transaction_type = 'DEBIT'
      AND  narration        = 'IMPS'
)
SELECT amount_rank,
       transaction_amt AS seventh_highest_debit_amount
FROM   ranked
WHERE  amount_rank = 7;


-- =====================================================================
-- Q2. Number of transactions category-wise (UPI, IMPS, RTGS, NEFT).
-- =====================================================================
--
-- The brief names four categories; the data contains a fifth, IFT, on 89 rows.
-- Dropping it would make the counts not sum to the table, so it is reported and
-- flagged rather than quietly filtered out.

SELECT narration                          AS category,
       COUNT(*)                           AS txn_count,
       SUM(CASE WHEN transaction_type = 'DEBIT'  THEN 1 ELSE 0 END) AS debit_count,
       SUM(CASE WHEN transaction_type = 'CREDIT' THEN 1 ELSE 0 END) AS credit_count,
       ROUND(SUM(transaction_amt), 2)     AS total_amount,
       ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM transactions), 2) AS pct_of_txns
FROM   transactions
GROUP  BY narration
ORDER  BY txn_count DESC;


-- =====================================================================
-- Q3. Statistical summary of load amount.
-- =====================================================================
--
-- "Load amount" is taken to mean money loaded *into* the account, i.e. CREDIT
-- transactions. The bell curve and box plot are produced by run_analytics.py;
-- this is the numeric summary behind them.
--
-- SQLite has no PERCENTILE_CONT, so the quartiles are picked positionally with
-- NTILE. On Postgres the whole block collapses to:
--   SELECT PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY transaction_amt) ...

WITH load_txns AS (
    SELECT transaction_amt
    FROM   transactions
    WHERE  transaction_type = 'CREDIT'
),
quartiles AS (
    SELECT transaction_amt,
           NTILE(4) OVER (ORDER BY transaction_amt) AS quartile
    FROM   load_txns
)
SELECT (SELECT COUNT(*)            FROM load_txns)                       AS n,
       (SELECT ROUND(AVG(transaction_amt), 2) FROM load_txns)            AS mean,
       (SELECT ROUND(MIN(transaction_amt), 2) FROM load_txns)            AS min,
       (SELECT ROUND(MAX(transaction_amt), 2) FROM load_txns)            AS max,
       (SELECT MAX(transaction_amt) FROM quartiles WHERE quartile = 1)   AS q1,
       (SELECT MAX(transaction_amt) FROM quartiles WHERE quartile = 2)   AS median,
       (SELECT MAX(transaction_amt) FROM quartiles WHERE quartile = 3)   AS q3;


-- =====================================================================
-- Q4. Monthly cohort of active users (users doing DEBIT transactions).
-- =====================================================================
--
-- A user's cohort is the month of their first DEBIT transaction. The cell at
-- (cohort, active_month) counts distinct users from that cohort who made at
-- least one DEBIT in that month.
--
-- Cells below the diagonal are structurally empty — a user cannot be active
-- before their own first transaction — so the output is naturally triangular.

WITH debits AS (
    SELECT user_id, txn_month
    FROM   transactions
    WHERE  transaction_type = 'DEBIT'
),
cohorts AS (
    SELECT user_id,
           MIN(txn_month) AS cohort_month
    FROM   debits
    GROUP  BY user_id
)
SELECT c.cohort_month,
       d.txn_month                     AS active_month,
       COUNT(DISTINCT d.user_id)       AS active_users
FROM   debits d
JOIN   cohorts c ON c.user_id = d.user_id
GROUP  BY c.cohort_month, d.txn_month
ORDER  BY c.cohort_month, d.txn_month;


-- =====================================================================
-- Q5. Top 10 percentile of users by net amount (DEBIT - CREDIT).
-- =====================================================================
--
-- Net amount per user is total DEBIT minus total CREDIT. "Top 10 percentile"
-- means the users in the highest decile of that measure — with 10 distinct
-- users in this dataset that is the single top user, which is worth stating
-- plainly rather than letting a reader assume the query returned too little.
--
-- NTILE(10) is used rather than a hand-rolled cutoff so the query keeps working
-- unchanged as the user base grows.

WITH per_user AS (
    SELECT user_id,
           SUM(CASE WHEN transaction_type = 'DEBIT'  THEN transaction_amt ELSE 0 END) AS total_debit,
           SUM(CASE WHEN transaction_type = 'CREDIT' THEN transaction_amt ELSE 0 END) AS total_credit
    FROM   transactions
    GROUP  BY user_id
),
scored AS (
    SELECT user_id,
           total_debit,
           total_credit,
           total_debit - total_credit AS net_amount,
           NTILE(10) OVER (ORDER BY total_debit - total_credit DESC) AS decile
    FROM   per_user
)
SELECT user_id,
       ROUND(net_amount, 2)   AS net_amount,
       ROUND(total_debit, 2)  AS total_debit,
       ROUND(total_credit, 2) AS total_credit
FROM   scored
WHERE  decile = 1
ORDER  BY net_amount DESC;
