# Tables with a column named `rowid` and no primary key are handled incorrectly

**Severity:** critical · **Points:** 8 · **Triage:** bug

For tables with no primary key, Datasette uses the bare name rowid as the implicit row ID, but a real column named rowid shadows it in SQLite. Row selection, ordering, pagination cursors, row URLs and the post-insert row lookup then act on the user's column, so one delete can remove several rows.

## Steps to reproduce
1. Create table t (rowid text, name text) with no primary key and insert ('z','first'), ('a','second'), ('a','third'), ('b','fourth'), (null,'fifth').
2. POST /data/t/a/-/delete.
3. GET /data/t/a.json?_shape=array.
4. Page through the table with ?_size=1.
5. GET /data/t/1.
6. Open the row for 'fifth' from the table page.

**Expected:** Each row is addressed by its real SQLite row ID. Deleting one row deletes only that row. Each row has its own URL and row page. Pagination visits all five rows once. The real row ID resolves. A row with a null rowid gets a usable link or none.
**Actual:** Delete removes both 'second' and 'third' (the row is selected with where rowid = 'a'). Both rows link to /data/t/a and that page returns both. Pagination skips 'third'. /data/t/1 returns 404. 'fifth' links to /data/t/None. These results come from the report and were not reproduced during grooming.

## Cause
Medium-high confidence in the mechanism, not confirmed by running it. SQLite resolves an unqualified rowid to a real column of that name before the implicit row ID, and Datasette builds SQL with the bare word rowid in the places listed above. To confirm, run the report's table with select rowid, * from t where rowid = 'a' and check it returns two rows, then confirm the delete handler reaches row_s

**Where:**
- `datasette/utils/__init__.py` `row_sql_params_pks` lines 1813-1831: With no primary key it sets select to 'rowid, *' and pks to ['rowid'], then builds 'where rowid=:p0'. With a real rowid column the select returns two rowid columns and the WHERE matches the text column. Called from datasette/app.py:3275, which the findings tie to the row page, update and delete. Most likely cause of the multi-row delete and the shared URL.
- `datasette/views/table.py` lines 2596-2613: For use_rowid it prepends 'rowid, ' to the select and sets order_by and order_by_pks to 'rowid' (and appends ', rowid'). Sorting and the table page select therefore use the user's column.
- `datasette/views/table.py` lines 2299-2317 and 2339-2400: The _next cursor code compares 'rowid <= :p' and 'rowid > :p' and maps columns named rowid to 'rowid'. Comparing against the text column explains the skipped rows. _page_position at 2448-2471 also uses 'rowid' as the position key.
- `datasette/views/query_helpers.py` `_inserted_row_url` lines 498-541: Selects rowid and filters 'where rowid = ?' with lastrowid. When the column shadows the real ID this returns the wrong row or none, so the View row link is wrong.
- `datasette/views/table.py` lines 779-796 and 978-986: has_rowid_column is a name check on the result columns, so it cannot tell the real row ID from a user column. It decides whether rows get links, which is how the /None and duplicate-URL symptoms arise.

## Fix approach
Add one helper that picks the row-ID alias for a table: the first of rowid, _rowid_ and oid that is not a real column. Use it wherever the code hardcodes rowid for no-PK tables. Alias it in the select (for example select <alias> as a datasette-internal name, *) so the rest of the code sees one name. Use that name in WHERE, ORDER BY, cursor comparisons, last_rowid_sql, _inserted_row_url and path_fr

## Tests
**Regression test** (`tests/test_row_keys.py`): Create a temp table `create table t (rowid text, name text)` and insert ('z','first'),('a','second'),('a','third'),('b','fourth'),(null,'fifth') via the ds fixture's writable database. GET /data/t.json?_shape=array and assert every row path is unique and that the row link for 'second' resolves only to that row (GET /data/t/<path>.json returns exactly one row named 'second'). Setup reuses ds_client-style Datasette creation as in test_every_row_link_leads_to_its_own_row. Assert that the set of names returned across ?_size=1 pagination equals all five names, with 'third' included.

**Reuse:** test_every_row_link_leads_to_its_own_row in tests/test_row_keys.py, ds_client fixture, Soup (BeautifulSoup) parsing of data-row attributes in tests/test_row_keys.py, test_sort_rowid_with_next in tests/test_table_html.py (extend it to check returned rows), test_rowid_sortable_no_primary_key in tests/test_table_html.py

**Scenarios:**
- [automate] Deleting the row whose rowid text is 'a' via POST /data/t/a/-/delete removes only the 'second' row, leaving 'third' in place. (tests/test_api_write.py)
- [automate] Updating the row at /data/t/a via the update endpoint changes only one row, not both duplicate 'a' rows. (tests/test_api_write.py)
- [automate] The real integer row ID (e.g. /data/t/1) returns the matching row, not a 404. (tests/test_row_keys.py)
- [automate] The null rowid text value produces no dead link to /data/t/None; the row has no data-row link. (tests/test_row_keys.py)
- [automate] The row page /fragment?_row= for a shadowed rowid returns the single correct row. (tests/test_table_html.py)
- [automate] Table with rowid, _rowid_ and oid all real columns: no row links, no row page, no edit or delete, matching view behaviour. (tests/test_row_keys.py)
- [automate] Inserting a row into a shadowed-rowid table and following the View row link after insert goes to the correct row. (tests/test_api_write.py)
- [automate] Ordinary no-primary-key table with no rowid column still sorts and paginates by rowid as before (regression guard). (tests/test_table_html.py)

## Open questions
- For a table where rowid, _rowid_ and oid are all real columns, should Datasette refuse update and delete, or show the table read-only with no row links? (Assuming: it is read-only with no row links, and the write endpoints return a clear error.)
- Is the public row URL format expected to change? (Assuming: it does not change; only the value behind the URL is corrected.)

---
Refined from [simonw/datasette#3110](https://github.com/simonw/datasette/issues/3110) by defectrefiner (claude-haiku-5-5), against commit `775e8ba28525`, in 37 s for $0.010. Approved by a person on 2026-11-23.
