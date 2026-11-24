# write_wrapper cannot cancel an alter table statement

**Severity:** high · **Points:** 1 · **Triage:** already_fixed

write_wrapper cannot roll back an alter table statement: raising after yield should undo the schema change, but the report says it does not. The changelog entry for issue 2924 / PR 2925 says this was fixed, and a regression test exists. Not run in this review. The reporter's #2871 is not in the repo, so it is unconfirmed that it is the same report.

## Steps to reproduce
1. Register a write_wrapper plugin for a database whose generator yields and then raises an exception.
2. POST an alter operation (add_column, rename_column or rename_table) to /<db>/<table>/-/alter.
3. Compare the schema and data before and after the request.

**Expected:** The alter is rolled back, the response is HTTP 400 with the wrapper's error message, and the schema and data are unchanged.
**Actual:** Per the report, the schema change persists. Not confirmed. Code reading suggests the outer transaction rolls the change back.

## Cause
Most likely there is no remaining bug on this path. The rollback comes from the outer BEGIN IMMEDIATE and 'with conn' in both execution modes. Confidence is moderate to high, from reading code only. Someone should run test_write_wrapper_can_reject_alter_table_after_write on this commit and confirm the sender of #2871 is the same report.

**Where:**
- `datasette/database.py` `_apply_write_wrapper` lines 1291-1332: wrapped() only catches StopIteration around gen.send(result), so an exception raised by the wrapper after the yield propagates to the caller.
- `datasette/database.py` `_execute_write_fn` lines 484-503: The non-threaded path runs fn inside 'with conn' after BEGIN IMMEDIATE, so an exception leaving the block rolls back the whole transaction.
- `datasette/database.py` `_execute_writes` lines 690-693: The write thread path also runs task.fn inside 'with conn' after BEGIN IMMEDIATE, so the same rollback applies there.
- `datasette/views/table_create_alter.py` lines 1361-1363: The inner db_for_write.atomic() is a savepoint released before the wrapper's post-yield raise. The comment says write_wrapper rejection is meant to roll back, which relies on the outer transaction.

## Fix approach
None needed if the regression test passes. If the reporter still sees the bug, check whether their wrapper runs outside _execute_write_fn's transaction (for example transaction=False, or a path that skips _wrap_fn_with_hooks), and investigate that separately.

## Tests
**Regression test** (`tests/test_write_wrapper.py`): Reuse test_write_wrapper_can_reject_alter_table_after_write (parametrized over num_sql_threads, in_memory and operations) as the regression test; it should fail on the current code if the bug is real and pass once fixed. Setup: Datasette with alter-table permission, table t with one row, write_wrapper that yields then raises ValueError('Rejected after write') for database 'demo'. Assertion: POST /demo/t/-/alter returns 400 with errors == ['Rejected after write'], and conn.iterdump() after the request equals the snapshot taken before. Add a direct variant next to test_write_wrapper_exception_thrown_into_generator: db.execute_write_fn running 'alter table' then raising after yield, asserting pytest.raises and an unchanged iterdump(), covering both file and in-memory databases.

**Reuse:** test_write_wrapper_can_reject_alter_table_after_write, test_write_wrapper_exception_thrown_into_generator, test_no_rename_event_on_rollback, ds_for_rename fixture, datasette fixture in tests/test_write_wrapper.py

**Scenarios:**
- [automate] Wrapper raises after yield during a rename-table alter via execute_write_fn; schema and data must be unchanged after rollback. Covers the rename-table path of the parametrized test directly. (tests/test_write_wrapper.py)
- [automate] Wrapper raises after yield on a write that is not a schema change (plain insert); the insert must be rolled back and no error leaks to a different state. (tests/test_write_wrapper.py)
- [automate] Wrapper yields and does not raise during an alter-table request; the alteration must be committed and the request succeeds, so the fix does not block legitimate schema changes. (tests/test_write_wrapper.py)
- [automate] Wrapper that raises after yield when the write goes through execute_write (not execute_write_fn), checking the outer transaction also rolls back an alter. (tests/test_write_wrapper.py)
- [manual] A wrapper running outside _execute_write_fn's transaction (for example transaction=False or a path skipping _wrap_fn_with_hooks) could still leave a schema change applied after an exception. The investigation flags this as the only remaining suspect if the reporter still sees the bug in #2871; it depends on a real deployment and reporter's plugin configuration, so it is left manual. (Manual: needs the reporter's actual plugin and deployment setup from #2871 to reproduce, which the test suite cannot supply.)

## Open questions
- Which Datasette version and num_sql_threads setting did you see this on, and does it still happen on current main? (Assuming: it was on a release before the fix in 2925, so the answer is already fixed.)
- Which alter operation did you send, and what does your write_wrapper do after the yield? (Assuming: it is a bare yield followed by a raise, as in the existing test.)

---
Refined from [simonw/datasette#2924](https://github.com/simonw/datasette/issues/2924) by defectrefiner (claude-haiku-5-5), against commit `775e8ba28525`, in 42 s for $0.010. Approved by a person on 2026-11-23.
