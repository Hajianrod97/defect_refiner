# The ticket set

`tickets.json` holds 38 tickets about [datasette](https://github.com/simonw/datasette). `candidates.json` is what `python -m defectrefiner.tickets` found: every closed, completed issue whose body splits the work into a checklist of 3 or more items (28).

## Real tickets (23)

The maintainer's checklist is the reference answer. Each ticket was **rewritten as a rough ticket**: a new title and the need in a sentence or two, without the breakdown. That keeps the answer out of the input, and makes it harder for a model to recognise an issue it saw in training. Checklist items that were just issue numbers were replaced by those issues' titles.

Kept only where the checklist is a split of a goal the ticket states, so a team could work it out from the ticket and the code. Dropped:

| Issue | Why |
|---|---|
| #71, #956 | Ops chores (a domain set-up, a broken CI step), not product work |
| #150, #153 | The checklist *is* the list of requests: nothing to work out |
| #2456 | A tracking list of older issues, not a split |

## Written tickets (15)

Each carries one planted trap, set against datasette 1.0a19 (commit e2497fdb, 1 June 2025), with the facts checked in that code:

| Kind | Count | A good grooming... |
|---|---|---|
| `already_exists` | 5 | says the feature exists (CORS, hiding columns, query time limit, CSV export, drop-table API) |
| `ambiguous` | 5 | asks the question that decides the work ("Support Excel": import or export?) |
| `two_in_one` | 3 | splits two unrelated requests into separate stories |
| `conflict` | 2 | flags a clash with how datasette works (read-only databases, a SELECT-only SQL editor) |

A trap is checked by keywords, without a judge (see `Trap` in `defectrefiner/tickets.py`). For `already_exists`, a hit must **name** the existing mechanism (`--cors`, `_nocol`, `sql_time_limit_ms`, CSV, `/-/drop`): the first Claude runs showed the generic version counting hedges like "the code may already have a timeout; check first" as catches. Written references are mine, not a maintainer's, so scores on the two sources are reported separately.

# The bug set

`bugs.json` holds 44 closed datasette bugs, each with an answer key: the source files its fix changed. `python -m defectrefiner.defects` found the candidates (`defect_candidates.json`): every closed issue labelled `bug` whose fix is a main-line commit saying "Fixes #N", "Closes #N" or "Fix for #N" (224 of 338; 206 change a source file). It also lists the open bugs (`open_bugs.json`, 27), which have no answer key and are for real use.

The agents see the code at the **parent of the first fix commit**, so the fix diff applies to exactly the code they read.

**Chosen:** all 33 outside reports that qualify, plus 11 maintainer reports that describe a symptom without stating its cause. 166 of the 206 are the maintainer's own, and many name the cause.

| Dropped | Why |
|---|---|
| Fixes touching 5+ source files | Where the bug is gets fuzzy when the fix rewrites half the views |
| #184 | The fix rebuilt error handling across four files |
| #1116 | Reads as a feature request; fixed over three commits |
| #1552 | A feature request labelled bug |
| #1671 | The report is all screenshots |
| #1771, #1899 | CSS and editor-focus behaviour, not code investigation |

**Rewritten** like the feature tickets: symptoms, steps, URLs and error messages kept; the reporter's diagnosis, proposed fixes, links to code lines and file or function names cut. Each bug's `removed` says what went. **Stack traces are kept** (5 bugs, `traceback: true`): real reports have them and a team would read them, but they can point at the file, so scores are reported with and without them. Seven reports were written after June 2026, so they're unlikely to be in a model's training data.
