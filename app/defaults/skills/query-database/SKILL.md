---
name: query-database
description: Query or edit a SQLite, DuckDB, CSV or Excel data set with SQL and report the answer. Use when the user has a database file or wants SQL over their files.
---

# Query database

1. SQLite: python3 sqlite3 module (there is no sqlite3 program; use the module). DuckDB ships inside Meshpool (never pip install it): `import duckdb`, then duckdb.sql over CSV, Parquet and Excel files directly. Postgres or MySQL only when the user gives a connection they own; read it from an environment variable.
2. Look at the schema first. Run read queries freely; for any change (insert, update, delete, drop) copy the file first, say what will change and how many rows, and wait for a yes.
3. Show the SQL you ran and the result as a small table. Write large results to CSV.

Rules: Write in plain sentences, no em dashes. Save results in the project's "Meshpool outputs" folder unless the user names a place. Look at every visual result with view_output before you say it is done. Reply with the file path and two sentences on what it is.
