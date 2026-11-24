# Supported bank CSV formats

<!-- Generated from app/parsers/bank_formats.py — do not edit by hand.
     Regenerate with:  python app/parsers/bank_formats.py --write -->

FinanceBook recognises these exports from the file's header row and reads the sign the way each bank writes it. Any other CSV / Excel file still imports with the generic column detection. **Verified** formats were checked against a real export; unverified ones are built from the bank's documented layout — please report a problem if one imports wrong.

| Bank | Account kind | Sign style | Quirks handled | Verified | Fixture | Added |
|---|---|---|---|---|---|---|
| Capital One | checking | Unsigned amount + type column (Debit / Credit) | Amount is always positive; the Transaction Type column (Debit / Credit) decides the sign — before 1.16 every row imported as money in<br>Two-digit years<br>No newline at end of file | Y | `tests/fixtures/bank_csv/capitalone_checking.csv` | 1.16.0 |
| Capital One | credit card | Separate Debit / Credit columns | Some rows have a blank Description; the payee becomes "Capital One purchase (&lt;Category&gt;)"<br>Two-digit years<br>Windows (CRLF) line endings | Y | `tests/fixtures/bank_csv/capitalone_credit.csv` | 1.16.0 |
| Chase | checking | Signed amount (negative = money out) | Every row ends with an extra comma (one more field than the header)<br>Details (DEBIT / CREDIT / CHECK / DSLIP) is checked against the sign | Y | `tests/fixtures/bank_csv/chase_checking.csv` | 1.16.0 |
| Chase | credit card | Signed amount (negative = money out) | HTML entities in names ("BEST PIZZA &amp;amp; BREW") are unescaped<br>Type (Sale / Payment / Return / Adjustment) is checked against the sign | Y | `tests/fixtures/bank_csv/chase_credit.csv` | 1.16.0 |
| U.S. Bank | checking | Signed amount (negative = money out) | Memo "Download from usbank.com." is dropped<br>Repeated spaces in names are collapsed<br>$0.00 rows are skipped | Y | `tests/fixtures/bank_csv/usbank_checking.csv` | 1.16.0 |
| U.S. Bank | credit card | Signed amount (negative = money out) | Memo reference strings ("24692…; 05942; ; ; ;") are dropped; words such as "WEB AUTOMTC" are kept<br>$0.00 rows (e.g. a waived annual fee) are skipped<br>Same layout as U.S. Bank checking — the account you import into picks | Y | `tests/fixtures/bank_csv/usbank_credit.csv` | 1.16.0 |
| Wells Fargo | checking | Signed amount (negative = money out) | Check numbers go to the memo | Y | `tests/fixtures/bank_csv/wellsfargo_checking.csv` | 1.16.0 |
| Wells Fargo | credit card | Signed amount (negative = money out) | Same layout as Wells Fargo checking — the account you import into picks | Y | `tests/fixtures/bank_csv/wellsfargo_credit.csv` | 1.16.0 |
| Wells Fargo (no header row) | checking | Signed amount (negative = money out) | No header row: date, amount, "*", check #, description | N | `tests/fixtures/bank_csv/wellsfargo_checking_noheader.csv` | 1.16.0 |
| Wells Fargo (no header row) | credit card | Signed amount (negative = money out) | No header row: date, amount, "*", check #, description | N | `tests/fixtures/bank_csv/wellsfargo_credit_noheader.csv` | 1.16.0 |
| Bank of America | checking | Signed amount (negative = money out) | Summary block (beginning / ending balance, totals) above the header is skipped<br>"Beginning balance as of …" row with no amount is skipped | N | `tests/fixtures/bank_csv/bankofamerica_checking.csv` | 1.16.0 |
| Bank of America | credit card | Signed amount (negative = money out) | Reference Number and Address are not imported | N | `tests/fixtures/bank_csv/bankofamerica_credit.csv` | 1.16.0 |
| Apple Card | credit card | Purchases positive, payments negative (reversed) | Purchases are positive and payments / credits negative — signs are reversed<br>Payee is the clean Merchant name; the bank's raw Description goes to the memo<br>Type (Purchase / Payment / Credit / Installment) is checked against the sign | N | `tests/fixtures/bank_csv/applecard_credit.csv` | 1.16.0 |
| BMO (U.S.) | checking | Unsigned amount + type column (Debit / Credit) | BMO U.S. (formerly BMO Harris / Bank of the West) checking export: Type is "DDA Debit" / "DDA Deposit" / "DDA Credit"<br>Amount is signed, and the CREDIT/DEBIT column decides the sign anyway, so an export with unsigned amounts reads right too<br>Runs of spaces in descriptions ("PPD  ACME PAYROLL") are collapsed<br>Reference numbers, Currency and Original Amount are not imported<br>Lines of text above the header row are skipped; YYYYMMDD dates are read<br>Windows (CRLF) line endings | Y | `tests/fixtures/bank_csv/bmo_checking.csv` | 1.19.0 |

## Adding a bank

1. Export a CSV from the bank and anonymize it: replace people's names, account / card / reference numbers and balances with fake ones, but keep the header row, column order, quoting, date format, sign style, merchant names and every kind of row (purchase, payment, refund, fee, $0.00, blank description).
2. Save it as `tests/fixtures/bank_csv/<bank>_<checking|credit>.csv`.
3. Add a `BankFormat` entry to `REGISTRY` in `app/parsers/bank_formats.py`.
4. Add the expected rows to `FIXTURE_EXPECTATIONS` in `tests/test_bank_formats.py`.
5. Run `python app/parsers/bank_formats.py --write` and the test suite.
