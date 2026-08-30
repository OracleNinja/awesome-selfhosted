---
description: Show where a KDP book sits in the pipeline and what its current gate says
argument-hint: [path to manifest.json]
allowed-tools: Read, Bash, Glob
---

Report the state of the KDP book whose manifest is at `$1` (if no path is
given, find the manifests under `kdp-studio/books/*/manifest.json` and report
each one).

For each book:

1. Read the manifest and report `book_id`, title, current `stage`, page count,
   trim and paper.
2. Run the gate for its current stage with `--json`, e.g.
   `python3 -m kdp --json qa <manifest>`.
3. Run `python3 -m kdp disclosure <manifest>` and report the statement.
4. State plainly whether the book may advance, and if not, list the blocking
   findings with their codes.

Do not fix anything and do not advance the book. This is a status report.
