---
description: Show where a KDP book sits in the pipeline and what its current gate says
argument-hint: [path to manifest.json]
allowed-tools: Read, Bash, Glob
---

Report the state of the KDP book whose manifest is at `$1` (if no path is
given, find every `kdp-studio/books/*/manifest.json` and report each).

For each book:

1. Read the manifest and report `book_id`, title, current `stage`, page count,
   trim, paper, and how many art pages have an approved asset.
2. Run the gates for its current stage with `--json`. `python3 -m kdp gates`
   lists which gates guard which stage.
3. Run `python3 -m kdp disclosure <manifest>` and report the statement.
4. Report the approval state, and whether it is *current* for the book as it
   stands — an approval that predates an edit is void.
5. State plainly whether the book may advance. If not, list the blocking
   findings with their codes, and say which stage owns each fix.

Distinguish **FAIL** (change the book) from **BLOCKED** (a check could not run
— fix the toolchain or supply the missing evidence). Never describe a BLOCKED
gate as passing.

Do not fix anything, do not advance the book, and do not record an approval.
