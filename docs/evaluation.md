# How defectrefiner was measured

The question: does a team of agents groom backlog tickets better than one agent? Three systems groomed the same 38 tickets, and each grooming was scored against how the work was really split. The project then moved to bug reports, where the fix commit gives an exact answer key: see [Bug grooming](#bug-grooming). This page has the numbers; the [README](../README.md) has the summary.

## The answer

For grooming one ticket at a time, **no**. A role-based team (product owner, tech lead, QA, estimator and a facilitator) matched or trailed a single agent on every quality measure, at a third more cost and nearly three times the time, and wrote twice as many stories. The biggest improvement in the whole project came from clearer instructions, not from adding agents.

| Decision | Evidence |
|---|---|
| **One agent per ceremony, not a team per ceremony** | The team tied the single agent on real tickets (53% coverage each), trailed it on written ones (57% vs 67%), and cost $0.024 vs $0.018 per ticket at 97 s vs 35 s. |
| **Fix the instructions before adding roles** | Rules for tests, criteria and questions raised the single agent's testable criteria from 37% to 52% and cut stories per ticket from 3.5 to 2.1, with no new agent. |
| **A strong model before a clever setup** | A 7B local model caught 4 of 15 planted traps however it was set up; Claude Haiku caught 15 of 15 from a single prompt. |
| **Sonnet as the judge for final numbers** | It agreed with blind grades on 86% of items; Haiku and a local 7B model agreed on 76%. |
| **Haiku as the judge during development** | A full scoring pass costs cents instead of about $4. |

## The tickets

38 tickets about [datasette](https://github.com/simonw/datasette), a Python project of about 88,000 lines. Details in [data/README.md](../data/README.md).

- **23 real issues** whose closed checklist is the maintainer's own split of the work. That checklist is the reference answer. Each was rewritten as a rough ticket (a new title and the need in a sentence or two) so the answer isn't in the input.
- **15 written tickets**, each with one planted trap, checked against datasette's code: 5 ask for something that **already exists** (CORS, hiding columns, a query time limit, CSV export, a drop-table API), 5 are **ambiguous** ("Support Excel": import or export?), 3 are **two requests in one**, and 2 **conflict** with how datasette works.

Every system grooms against datasette's code **as it was when the issue was opened**: a 2017 ticket sees 2017's code.

## The systems

All three use the same grooming rules and return the same format: stories (user story, acceptance criteria, points, dependencies), open questions and risks.

- **One prompt**: a single model call with the ticket and the repo's file list. No code reading.
- **One agent**: up to 12 read-only tool calls (`list_files`, `grep`, `read_file`), then the grooming.
- **Team** (`defectrefiner/team.py`, a LangGraph graph): the tech lead reads the code (8 calls) and writes notes; the product owner writes stories and questions from the notes; QA writes criteria; the estimator sizes stories; the facilitator sends work back to one role or finishes. Hard rules (a story over 8 points goes back to be split, a story with no criteria goes to QA) and the facilitator's own reviews have separate limits of two send-backs each.

## The scores

- **Coverage**: for each item of the reference split, a judge answers one narrow question: is this work in the plan (yes, partly, no)? A "partly" counts half. One small question per item is far more reliable than asking for an overall grade.
- **Testable criteria**: whether each story's acceptance criteria could be checked by a tester without guessing.
- **Traps**: keyword checks, no judge. An "already exists" trap only counts when the grooming names the mechanism (`--cors`, `sql_time_limit_ms`): a hedge like "the code may already have a timeout" doesn't.
- **Stories per ticket, time, tokens and cost.**

## Results

All 38 tickets, Claude Haiku 5.5 grooming, Claude Sonnet 5.5 judging:

| | One prompt | One agent | Team |
|---|---|---|---|
| Coverage, real tickets (23) | **56%** | 53% | 53% |
| Coverage, written tickets (15) | 55% | **67%** | 57% |
| Testable criteria | **81%** | 68% | 73% |
| Traps caught | 15/15 | 15/15 | 14/15 |
| Stories per ticket | 1.9 | 2.1 | 4.1 |
| Median time per ticket | 11 s | 35 s | 97 s |
| Cost per ticket | $0.0014 | $0.018 | $0.024 |

With 23 or 15 tickets, a few points either way are noise. What holds up: the team is never ahead, and it costs the most and takes the longest.

### Why the team didn't help

- **Grooming fits in one context.** One ticket and a few files of code fit comfortably in a single agent's context, so splitting the work gives no focus advantage.
- **Handoffs lose information.** Every role reads the ticket, but only the tech lead reads the code; the others get its notes. Anything it found and didn't write down is gone. The single agent writes with all its findings in view.
- **Roles amplify each other.** QA adds a thorough criterion, the estimator sees more work and says 13 points, the split rule sends the story back, and the product owner splits it again. Each step is reasonable; together they push stories per ticket from about 2 to 4.
- **Caution compounds.** Told to leave decisions to the requester, the product owner left a footer as "placeholder until the requester supplies its wording" and missed the link the reference wanted; in another ticket an assumption ("facets come from metadata") became a restriction ("no per-request toggle").
- **The facilitator was busy but not decisive.** 22 send-backs across 10 tickets (11 to the product owner, 7 to the tech lead, 3 to QA, 1 to the estimator). Some caught real problems, such as overlapping stories and scope creep, but coverage didn't move.

The team's one missed trap is arguably handled: asked to save each visitor's sort order "into the database", it stored the sort in the browser instead, which avoids the read-only-database conflict, but never said why.

### Memorization shows up

The one prompt never reads code, yet it has the best coverage on real datasette issues, which the model may have seen in training. On the written tickets, which it can't have seen, reading the code pays clearly: one agent 67%, one prompt 55%. Results on well-known open-source repos overstate what a model would do on private code.

## Instructions before agents

The first single-agent groomings split tests and documentation into stories of their own, asked 5 to 6 questions per ticket, and wrote criteria that deferred to unanswered questions. Four rules fixed most of it, with no new agent: tests and docs go in acceptance criteria; criteria describe observable behaviour and state an assumption instead of deferring; every story is estimated; open questions are only for the requester, ranked, each with a default assumption.

Claude Haiku 5.5 grooming, Haiku judging, all 38 tickets:

| | One prompt, before | after | One agent, before | after |
|---|---|---|---|---|
| Coverage, real tickets | 44% | 52% | 53% | 52% |
| Coverage, written tickets | 51% | 52% | 63% | 64% |
| Testable criteria | 53% | 51% | 37% | **52%** |
| Stories per ticket | 2.9 | 1.9 | 3.5 | 2.1 |
| Test-only or doc-only stories | 18 | 6 | 17 | 8 |
| Questions per ticket (median) | 5 | 3 | 6 | 3 |

## The model matters more than the setup

The same single prompt and single agent with a local model, qwen2.5-coder 7B, judged by Haiku:

| | One prompt | One agent |
|---|---|---|
| Coverage, real / written | 36% / 40% | 33% / 33% |
| Testable criteria | 11% | 13% |
| Traps caught | 4/15 | 4/15 |

The 7B agent averaged 1.4 tool calls out of 12 and searched for "API" instead of "cors"; reading code didn't help it. Haiku in a single prompt beat it at everything.

## Checking the judges

A judge's scores mean nothing until it's checked against answers that are known.

**Known answers.** A plan built from half of each reference split, on 10 tickets: the judge should say "yes" for the half that's in the plan and "no" for the half left out.

| | Qwen 7B | Haiku 5.5 |
|---|---|---|
| Work in the plan judged covered | 27 of 31 (4 "partly") | 31 of 31 |
| Work left out judged covered | 3 of 26 | 0 of 26 (5 "partly") |
| Good criteria judged testable | 3 of 4 | 4 of 4 |
| Bad criteria judged testable | 0 of 4 | 0 of 4 |

**Blind grades.** The 30 groomings of 10 tickets (three systems) were shuffled, stripped of system names and graded item by item by Claude Code (Opus 5.5). Agreement with those grades:

| Judge | Items agreeing | When it disagreed |
|---|---|---|
| **Sonnet 5.5** | **86%** (121 of 141) | stricter 15 times, more generous 5 |
| Haiku 5.5 | 76% (71 of 94) | stricter 14, more generous 9 |
| Qwen 7B | 76% (107 of 141) | 17 each way |

On those 10 tickets the graders ordered the one prompt and the team differently, all within a few points, but none put the team ahead of the single agent. Qwen is usable for rough coverage averages but not for testable criteria, where it passed 92% to 100% of stories that Haiku passed about half of.

The blind grader built the systems, so its grades aren't fully independent; hand grades by a person are the remaining check.

## Bug grooming

The project's next scope: groom bug reports into tickets. Sprint planning was dropped because it needs priorities and deadlines that a public repo doesn't record. Bugs have a better answer key than features: the commit that fixed them.

**The bugs.** 44 closed datasette bugs whose fix commit says which issue it closes ("Fixes #1234"), each fixed in one to four source files. The agent reads the code at the commit just before the fix, so the bug is still there. 33 were reported by outside users, 11 by the maintainer; 5 include a stack trace; 7 were reported after June 2026, too late for the model's training data. Selection and rewrites are in [data/README.md](../data/README.md).

**The score.** Each grooming lists up to five suspects (file, function, lines), most likely first. Each is matched against the lines the fix changed, with no judge:

- **line@k**: one of the first k suspects cites lines within 10 of a changed line.
- **function@k**: it names a function the fix changed, or cites lines inside one.
- **file@k**: it names a file the fix changed.

File-level scoring came first and turned out too easy. Asked with **no code at all**, Haiku named the right file first 75% of the time, from knowing how datasette is laid out. It was just as good on the bugs reported after its training data, so this is knowing the layout, not remembering the issues. Function level was easier than it looks too: datasette's `TableView.data` is about 600 lines long. Line level is the one a model can't pass without reading the code.

Claude Haiku 5.5, all 44 bugs:

| | One agent (reads the code) | No code |
|---|---|---|
| line@1 | **91%** | 5% |
| line@3 | **98%** | 5% |
| function@1 | 93% | 36% |
| file@1 | 98% | 75% |
| line@1, bugs reported after June 2026 (7) | 86% | 0% |
| line@1, no stack trace (39) | 90% | 0% |
| Tool calls, median time, cost per bug | 7.2, 27 s, $0.011 | 0, 4 s, $0.0004 |

Checks on the 91%: the top suspect's cited range is a median of 10 lines wide, and only 2 hits came from a range wider than 30 lines. Of the 4 misses, 3 had the fixed lines as the second suspect; one (#967) looked in the right file but not at the small class the fix changed.

**What this means for more agents.** One agent already finds the cause almost every time, so a pipeline can't show much gain at finding bugs on this set. A harder set (vaguer reports, fixes across many files) would be needed to separate investigators. What a pipeline could still add is the test plan, and time, by running agents in parallel.

**The regression test.** The single agent also proposes a regression test, in free text. It named the test file the maintainer's fix added a test to in **9 of 39** bugs (5 fixes added no test). That's low mostly because it wasn't asked for a file: it named any test file in only 12 of 44, and when it did, it was the right one 9 times out of 10.

### A testing agent in parallel

`defectrefiner/bug_team.py` (LangGraph) adds a test scout that reads the tests **at the same time** as the investigator reads the code, then a test planner that writes the test plan from both, and a template that writes the ticket. The investigator is the single agent, unchanged. The planner gets the scout's raw findings, not a summary: in the feature team, handoffs lost what the code reader didn't write down.

Claude Haiku 5.5, all 44 bugs:

| | One agent | Investigator ‖ test scout |
|---|---|---|
| Regression test in the fix's test file | 9/39 (not asked for a file) | **28/39 (72%)** |
| Names a test file | 12/44 | 44/44 |
| line@1 / line@3 | 91% / 98% | 86% / 100% |
| Median time | 27 s | 34 s (41 s if run one after another) |
| Cost per bug | $0.011 | $0.015 |

- **The test plan is what the second agent adds.** Finding the bug didn't change: same investigator, and 2 bugs either way at line@1 is run-to-run variation.
- **Parallel hides the scout.** It takes 8 s against the investigator's 28 s, so it costs no time. The 7 s added is the planner, which has to wait for both.
- **The 11 wrong test files are mostly near misses.** Datasette tests the same feature in more than one file (the HTML page in `test_html.py`, its JSON in `test_api.py`), and 7 of the 11 picked the other one. In 4 the fix's file appears in one of the planner's scenarios.

## Cost

Grooming is cheap; judging isn't. A full Sonnet scoring pass over the three systems (114 groomings, coverage and testable criteria) cost about $4, while a Haiku pass costs cents. Claude's answers are judged by Haiku during development and by Sonnet for final numbers.

Cost tracking: LangChain's usage callback is a context variable, so calls made in a plain thread pool went uncounted until the runner copied the context into its threads (`run.parallel_map`). After that, tracked Haiku spend matched the Anthropic Console; tracked Sonnet spend ran about 15% under it.

## Lessons from running local and hosted models

- **Small models loop in structured output.** Asked for JSON, qwen 7B repeated list items or a sentence until Ollama stopped it, once after 9 minutes. Length limits in the schema (Ollama enforces `maxLength` and `maxItems` while generating) and a reply-token cap fixed it.
- **Claude's structured outputs don't take those limits.** The SDK moves them into descriptions, so a longer reply came back and was rejected. Over-long text and lists are now trimmed when read, not rejected (`grooming.capped`).
- **qwen2.5-coder writes tool calls as text.** It claims tool support but answers `{"name": "grep", ...}` as plain content; a fallback turns that into a real call.
- **Claude rejects tool calls in a request that declares no tools**, so the agents write their final answer from a text copy of what they found.
- **Cap characters, not just lines.** One `grep` matched a minified JavaScript file and returned 368,000 characters as a single line, making one bug cost $0.12 instead of about $0.01. Lines are now cut at 300 characters. File listings were cut at 60 names, so the test scout never saw `tests/test_write_wrapper.py` (datasette has 105 test files); listings now go to 200. Five open bugs cost $0.31 before these fixes and $0.05 after. The eval runs above used the old tools, so their costs include a few such outliers.
- **Current Claude models reject edited conversation history**, so the agents save tokens with fewer tool calls rather than by trimming old tool results.

## Risks and limitations

The eval scores plans against old issues with known answers. Using AI in real refinement has risks this setup can only partly measure (the list follows practitioner write-ups on AI-assisted backlog refinement):

| Risk | What this eval shows | What it can't show |
|---|---|---|
| **Teams trust AI estimates too much** | Estimates shift with presentation: in the team, the estimator raised points after QA added criteria, with the same underlying work. | How much a team defers to an AI estimate. Estimate stability across repeated runs isn't measured yet (each ticket was groomed once). |
| **Blind to product and organizational context** | Coverage tops out near 50% on epic-sized tickets, whose splits reflect the maintainer's plans; the product owner agent left decisions as placeholders. | Freezes, availability, priorities and politics: none of it is in a ticket or the code. |
| **Inconsistent history gets amplified** | The reference checklists themselves are terse and uneven, which caps every score. | What happens if a system learns from past tickets and decisions. |
| **Teams stop deliberating** | Agents can talk each other into a shared mistake: the team's roles ratcheted stories per ticket from about 2 to 4 with nobody judging the whole. | Any effect on people. |
| **The bottleneck moves to review** | Output volume is measured: the team produced twice as many stories to review as the single agent. | Review time, and how often people accept, edit or reject what the system proposes. |

Others: references come from one open-source project; the hand-written tickets and traps were written by the same person (Claude Code) who built the systems; the real tickets may be in the model's training data. The design response is to keep AI output a pre-read that a person decides on, and to measure that decision step once it exists: the planned review step logs every accept, edit and reject, so acceptance and edit rates can stand in for an answer key in real use.

## Running it

```sh
uv run python -m defectrefiner.run agent --provider anthropic --model claude-haiku-5-5 --tag v2 --workers 4
uv run python -m defectrefiner.run team --workers 4                      # --routing fixed: hard rules only
uv run python -m defectrefiner.run report runs/team-facilitator-claude-haiku-5-5 \
    --judge-provider anthropic --judge-model claude-haiku-5-5 --workers 8
uv run python -m defectrefiner.score --provider anthropic --model claude-haiku-5-5   # check a judge on known answers
uv run python -m defectrefiner.grade runs/<run> runs/<run>                # grade by hand, blind
```

Each grooming is saved under `runs/` with its time, tokens, cost and (for the team) a log of who did what; failed ones are retried on the next run. Scores are cached per judge, and trap checks are recomputed on every report.
