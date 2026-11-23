# Rules for clean_customers.csv

Read `customers_raw.csv` and write `clean_customers.csv` with exactly these columns, in this order:
`id,name,email,phone,signup_date`.

1. Trim surrounding spaces from every field and collapse repeated inner spaces in names to one space.
2. `name`: each word capitalised, the rest lower case (`ALICE SMITH` -> `Alice Smith`).
3. `email`: lower case. Drop any row whose email is empty or not a valid address (must contain `@`).
4. `phone`: E.164 for US numbers: `+1` followed by the 10 digits (`(415) 555-0100`, `415.555.0100`,
   `+1 415 555 0100` and `14155550100` all become `+14155550100`).
5. `signup_date`: ISO `YYYY-MM-DD`. Inputs use `YYYY-MM-DD`, `MM/DD/YYYY`, `YYYY/MM/DD` or `DD Mon YYYY`.
6. Duplicates are rows with the same email after rule 3. Keep only the row with the earliest signup_date
   (keep its own id and other fields).
7. Sort by `id` ascending (numeric). Write a header row; no index column; plain comma-separated, no quotes
   unless needed.
