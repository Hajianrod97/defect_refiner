# Applied facet did not result in an "x" icon to dismiss it

**Severity:** medium · **Points:** 2 · **Triage:** bug

With a column facet applied to a value containing spaces and punctuation (Contract Type = 'Non-Purchasing Contract (Rents, etc.)'), no 'x' remove control appears on the selected facet value. The reporter suspects the punctuation breaks the selected-value check. Not reproduced, and the code path that decides this is only partly visible.

## Steps to reproduce
1. Import the SF Supplier Contracts dataset (data.sfgov.org cqi5-hm2d) with datasette-socrata
2. Open the table and enable a facet on the 'Contract Type' column
3. Click the value 'Non-Purchasing Contract (Rents, etc.)' to apply it
4. Look for the 'x' icon next to the selected value in the facet list

**Expected:** The applied facet value shows an 'x' control that removes that filter.
**Actual:** No 'x' icon is shown for the applied facet value.

## Cause
Unconfirmed. The reporter's punctuation hypothesis is not supported by the code I read: the query string is parsed with parse_qs and the value is compared as str, which should match. More likely causes are that the selected branch in _facet_results.html does not render the remove link in this case, or that the x is facet-level and the facet is metadata-defined (hideable false). Someone should rend

**Where:**
- `datasette/facets.py` lines 313-322: Selected state is computed by checking (key, str(row['value'])) against parsed query-string pairs. Only column or column__exact keys match, so a filter applied under another key would not be marked selected. Value-with-punctuation handling is not obviously wrong here, since parse_qs decodes the URL.
- `datasette/facets.py` lines 327-340: Each facet value dict gets 'selected' and 'toggle_url'. The selected flag decides which template branch renders the value.
- `datasette/templates/_facet_results.html` lines 16-19: Selected values use a separate <li class='facet-selected'> branch that contains the remove link. The template is truncated at that line, so the link markup and the x icon could not be inspected here.
- `datasette/facets.py` lines 10 (template) / 292: The facet-level 'cross' remove link is shown only when facet_info.hideable is true, which is source != 'metadata'. If the screenshot's x is the facet-level control, a metadata-defined facet would explain the missing icon. Speculative.

## Fix approach
First reproduce with a local table containing that value and a facet on it, then inspect the rendered HTML. If the selected branch lacks the remove link, add it. If the comparison fails for this value, normalise the comparison in facets.py. If the x is facet-level and hidden by hideable, decide whether metadata facets should be removable.

## Tests
**Regression test** (`tests/test_table_html.py`): Create a small table inside the test (not the shared facetable fixture) with a row whose value is 'Non-Purchasing Contract (Rents, etc.)' in a column such as contract_type, and a second row with another value. Request /<db>/<table>?_facet=contract_type&contract_type=Non-Purchasing+Contract+%28Rents%2C+etc.%29 using ds_client, parse the HTML with Soup, and assert soup.select_one('.facet-info[data-column="contract_type"] li a.cross') is not None. This fails now if the remove link is not rendered for the selected value. Then follow its href and assert the unfiltered JSON returns all rows.

**Reuse:** ds_client fixture from conftest, Soup (BeautifulSoup) import already in tests/test_table_html.py, test_facet_remove_exact_filter pattern in tests/test_table_html.py lines 548-569, ColumnFacet and Request.fake pattern in tests/test_facets.py lines 173-188

**Scenarios:**
- [automate] Same special-character value applied as a metadata-defined facet (table_config facets) rather than via _facet query argument; the remove link should still render or the hideable behaviour should be confirmed as intended. (tests/test_facets.py)
- [automate] ColumnFacet with the value 'Non-Purchasing Contract (Rents, etc.)' selected via the request query string: result selected is True and toggle_url drops only that value while keeping _facet. (tests/test_facets.py)
- [automate] Value containing a plus sign, ampersand or percent sign is URL-encoded correctly in the remove link href so following it clears the filter. (tests/test_table_html.py)
- [automate] Applied facet with a value that has no special characters still shows the remove link, confirming the fix does not regress the simple case already covered by test_facet_remove_exact_filter. (tests/test_table_html.py)
- [automate] Multiple applied facet values where one is special-character: only the selected one's remove link is removed and the others remain filtered correctly. (tests/test_facets.py)
- [manual] Visual check that the x icon is actually visible and clickable in a browser on a real deployment with the SF data. (Needs a browser to check CSS rendering and layout of the svg icon, which the test suite cannot do; tests/test_playwright.py exists but is not suited to this data-specific check.)

## Open questions
- Was the value applied by clicking a facet value, or by editing the URL by hand (which parameter name was used)? (Assuming: applied by clicking the facet value in the UI)
- Is the 'x' in the reporter's mental model the per-value remove control or the facet-level remove control next to the facet name? (Assuming: per-value control next to the selected value)
- Which Datasette version was used? (Assuming: the current version in this repository)

---
Refined from [simonw/datasette#2011](https://github.com/simonw/datasette/issues/2011) by defectrefiner (claude-haiku-5-5), against commit `775e8ba28525`, in 31 s for $0.007. Approved by a person on 2026-11-23.
