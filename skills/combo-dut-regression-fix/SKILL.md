---
name: combo-dut-regression-fix
description: Use when running a DUT's full simulation regression and fixing failing testcases until all pass — e.g. "<repo>の<dut>のリグレッション", "runsim all.txt を回して落ちたケースを直して", "regression and error fixing for a DUT", "回帰テストを全部Passさせて". Drives ../bin/runsim.py case_list + --chk loop under verif/<dut>/sim/work and logs each attempt.
---
# DUT Regression & Error Fixing

Run a DUT's full regression, log each attempt, and fix failing testcases until
every result line starts with `[Pass]`.

## 1. Pick the target repo and DUT

Confirm with the user **which repo** and **which DUT** unless already given.

- The **launch root** is the multi-repo root. It is *not* itself a git working
  tree — never run `git` there. Repos are immediate child directories such as
  `<launch root>/<repo>`; do all git operations inside that concrete repo.
- DUTs live under `<repo>/verif/<dut>/`. List candidates if unsure:
  ```bash
  ls <launch root>/<repo>/verif
  ```

## 2. Go to the sim work directory

```bash
cd <launch root>/<repo>/verif/<dut>/sim/work
```

All `runsim.py` calls below assume this CWD (it uses the `../bin` and `../list`
relative paths).

## 3. Run the regression

First find the case list — it is **not always `all.txt`**. List `../list/`.
Choose the intended full-regression list if it is identifiable. If only one list
exists, use it and note its name. If several plausible lists exist and the right
one is ambiguous, ask the user which to run.

```bash
ls ../list/
../bin/runsim.py --case_list ../list/<list>.txt
```

This can be long-running — run it in the background (or with a generous timeout)
and wait for completion before checking. (Some small DUTs finish in seconds.)

If the simulator dies inside the harness sandbox before producing logs (for
example, immediate exit code 159/SIGSYS with no stdout/stderr), rerun the same
`runsim.py` command with that harness's approved sandbox escape or permission
escalation before debugging testcases. A common symptom is only DPIC/make output
from the run and no `[Pass]` or `[Fail]` lines from `--chk`.

## 4. Collect results

```bash
../bin/runsim.py --chk
```

`--chk` only reads existing logs and normally needs no sandbox escape. Use the
same approved `runsim.py` permission mode only if the harness requires it.

## 5. Check every line starts with `[Pass]`

Each result line is `[Pass] <case>` or `[Fail] <case>`. The run is clean only
when **every** line starts with `[Pass]`.

## 6. Append the attempt to the log

Append the full `--chk` output for this attempt to `report_regression_result_<yymmdd>.txt`
(date = today, e.g. `regression_result_260620.txt`) in the work dir. Prefix each
attempt with a header like `=== attempt N (HH:MM) ===` so attempts accumulate,
never overwrite.

## 7. Pass → done. Fail → fix and loop

- **All `[Pass]`**: report the final attempt count and stop.
- **Any `[Fail]`**: for each failing testcase, open its log, find the root cause,
  fix the testcase (or the relevant source), then **go back to step 3**. Re-run
  the full regression each iteration so fixes don't regress other cases.

## Notes

- Do **not** `git commit` unless the user explicitly asks (standing rule).
- Keep iterating until all pass or the user stops you; report what failed and why
  on each loop so progress is visible.
