# Clicking 'Run SQL' from extneded query.html page results in URL not found error

**Severity:** medium · **Points:** 2 · **Triage:** bug

On an ad-hoc query results page, the Run SQL form posts to the database root with no /-/query suffix, so re-running hits a not-found page. The reporter also saw the base_url prefix dropped, which the code I read does not explain. I did not check whether 1.0a19 already contains a fix.

## Steps to reproduce
1. Open a database page under a base_url prefix, e.g. /sam_datasette/rm_toucans_slice.
2. Click Run SQL on the database page's query form. It submits to .../-/query and works.
3. On the results page, click Run SQL again without changing anything.
4. Reporter sees a 404 at /rm_toucans_slice/-/query?sql=+ with the sam_datasette prefix missing.

**Expected:** The query re-runs at /sam_datasette/rm_toucans_slice/-/query?sql=..., the same URL the first run used.
**Actual:** The results page form action is /sam_datasette/rm_toucans_slice with no /-/query. Re-running sends the user to a not-found page at a URL without sam_datasette.

## Cause
Most likely: query.html line 48 builds the ad-hoc results form action from the database URL alone, so the form submits to the database root. Confirmed by reading the code and matching the reporter's HTML. Not confirmed: why sam_datasette is lost. The redirect should keep the prefix, so check the reporter's proxy or base_url setup and reproduce with base_url set.

**Where:**
- `datasette/templates/query.html` lines 48: The form action is {{ urls.database(database) }}{% if stored_query %}/{{ stored_query }}{% endif %}. For ad-hoc queries this renders the bare database URL with no /-/query, which matches the reporter's HTML. The database page's own form in database.html line 45 correctly uses /-/query.
- `datasette/views/database.py` lines 103-112: A GET to the database URL with a sql parameter redirects to urls.database(database) + '/-/query' plus the query string. This is the step that turns the root submission into the /-/query URL in the report. Its prefix handling looks correct on reading.
- `datasette/url_builder.py` `Urls.path, Urls.database` lines 16-19, 75-77: These prepend base_url. Nothing I read strips the prefix, so this is where to look if a reproduction shows Datasette dropping it.

## Fix approach
In query.html line 48, use /-/query as the action when there is no stored_query, keeping /{{ stored_query }} for stored queries and leaving the POST write path unchanged. Separately, reproduce with base_url set and change prefix handling in the database redirect only if Datasette itself drops the prefix.

## Tests
**Regression test** (`tests/test_html.py`): Add test_base_url_query_results_form_action_keeps_prefix(ds_client_base_url_prefix): request get_unprefixed(ds_client_base_url_prefix, '/prefix/fixtures/-/query?sql=select+1'), parse the page with Soup, select the form with class 'sql core', and assert its action equals exactly '/prefix/fixtures/-/query'. It fails now because the results form action is built from the database URL alone (/prefix/fixtures), and passes once query.html uses /-/query as the action when there is no stored_query. Existing test_base_url_config only checks startswith('/prefix'), so it does not catch this.

**Reuse:** ds_client_base_url_prefix fixture in tests/test_html.py, get_unprefixed helper in tests/test_html.py, test_base_url_config parametrized path list in tests/test_html.py (~L1290-1343), test_show_hide_sql_query in tests/test_html.py (~L1099-1121) as the pattern for following the hide link and reading hidden sql inputs, test_stored_query_with_hide_has_no_hidden_sql in tests/test_html.py (~L1124-1132)

**Scenarios:**
- [automate] Query results page with _hide_sql=1 under the prefix: the form action must be exactly /prefix/fixtures/-/query, not /prefix/fixtures or /prefix/prefix/. (tests/test_html.py)
- [automate] Follow the hide link from a prefixed query results page, then check the form action is still /prefix/fixtures/-/query and the hidden sql input carries the original SQL. (tests/test_html.py)
- [automate] Without base_url, the query results form action stays /fixtures/-/query, so the fix does not change unprefixed output. (tests/test_html.py)
- [automate] Stored query page under the prefix keeps its form action built from /prefix/<db>/<query name>, and the fix does not change the stored-query branch. (tests/test_html.py)
- [automate] Multi-line SQL with newlines and special characters submitted from the results form, checked with get_unprefixed for a 200 response and no 404. (tests/test_html.py)
- [manual] Clicking Run SQL in a browser from the database home page through a real reverse proxy with a path prefix, as in the report. (Needs a real browser and a reverse-proxy deployment with a path prefix; the suite only tests the app behind ds_client_base_url_prefix, not the proxy.)

## Open questions
- Is the instance configured with base_url (/sam_datasette/) directly, or is a reverse proxy adding the sam_datasette prefix? (Assuming: Datasette is configured with base_url and the proxy passes paths through unchanged.)
- Does the 404 still happen when the same results page is opened directly with no proxy in front? (Assuming: no, if the proxy is what strips the prefix.)

---
Refined from [simonw/datasette#2487](https://github.com/simonw/datasette/issues/2487) by defectrefiner (claude-haiku-5-5), against commit `775e8ba28525`, in 51 s for $0.012. Approved by a person on 2026-11-23.
