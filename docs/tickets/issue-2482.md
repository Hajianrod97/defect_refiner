# Add another filter button is missing in latest alpha

**Severity:** low · **Points:** 1 · **Triage:** not_a_bug

The 'Add another filter' button is hidden on the last filter row until a column is selected. On the compound_three_primary_keys page the empty filter row has no column chosen, so its add button is hidden by design, not missing.

## Steps to reproduce
1. Open https://latest.datasette.io/fixtures/compound_three_primary_keys
2. Look at the empty filter row (column select shows '- column -')
3. Observe that no add button is visible on that row

**Expected:** Per the report, an 'Add another filter' button is expected on the filter row.
**Actual:** No 'Add another filter' button is visible on the empty filter row.

## Cause
Intended behaviour: the add button is only shown on the last filter row once a column has a value. Confidence is moderate-high from the code. I did not see the screenshot's contents, so I cannot confirm the reporter's row was empty. Someone should confirm on the page that selecting a column makes the button appear.

**Where:**
- `datasette/static/table.js` `updateFilterRowButtons` lines 699-710: Line 709 sets addButton.hidden when the row is not the last one or when column.value is empty, so the add button stays hidden until a column is picked on the last row.
- `datasette/static/table.js` `addFilterRowButtons` lines 782-800: Creates the add button with class filter-row-add and wires its click handler. The button exists in the DOM but is hidden by the logic above.
- `datasette/static/app.css` lines 1690-1700: Styles for .filter-row-add. CSS only styles the button and does not hide it, though the [hidden] rule at 1668-1670 makes the hidden attribute effective.

## Fix approach
No code change is needed if the behaviour is intended. If the reporter expects the button always visible, that is a feature change to updateFilterRowButtons, and the design should decide whether it is shown on empty rows.

## Tests
**Regression test** (`tests/test_playwright.py`): Playwright test in tests/test_playwright.py using the page and datasette_server fixtures: open /fixtures/compound_three_primary_keys, then assert expect(page.get_by_role('button', name='Add another filter')).to_be_visible() on the first filter row before any column is chosen. It fails now if the add button is hidden on empty rows, and passes once that is fixed. Confirm the intended design first, since the investigation says hiding the button on an empty row may be intended behaviour, in which case this test should instead assert the button appears after a column is selected.

**Reuse:** page fixture in tests/test_playwright.py, datasette_server fixture in tests/test_playwright.py, expect assertions as used in test_column_statistics_use_current_filters in tests/test_playwright.py, compound_three_primary_keys table from tests/fixtures.py

**Scenarios:**
- [automate] After selecting a column on the first filter row, the 'Add another filter' button is visible on that row. This confirms the intended behaviour the investigation describes. (tests/test_playwright.py)
- [automate] Clicking 'Add another filter' inserts a new filter row after the current one and focuses its column select, and the add button then appears on the new row only once it has a column. (tests/test_playwright.py)
- [automate] Clicking 'Remove this filter' removes a row and keeps at least one filter row with its add button still reachable, including when the removed row was the last one. (tests/test_playwright.py)
- [automate] A page opened with existing filters in the URL shows one row per filter with correct add and remove buttons on each row, so updateFilterRowButtons handles both states. (tests/test_playwright.py)
- [automate] Server-rendered filter form markup on compound_three_primary_keys stays unchanged, so the JavaScript fix does not alter HTML that other tests check. (tests/test_table_html.py)
- [manual] The add button's appearance and placement on the latest alpha at latest.datasette.io matches the reporter's screenshot in real browsers. (Needs a real deployment and visual comparison against the live site, which the test suite cannot do.)

## Open questions
- Did you have a column selected in the empty filter row when the button was missing? (Assuming: the row was empty, as in the screenshot URL, so the button is hidden by design.)

---
Refined from [simonw/datasette#2482](https://github.com/simonw/datasette/issues/2482) by defectrefiner (claude-haiku-5-5), against commit `775e8ba28525`, in 31 s for $0.009. Approved by a person on 2026-11-23.
