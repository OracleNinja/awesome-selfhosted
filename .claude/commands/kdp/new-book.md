---
description: Scaffold a new KDP book project directory
argument-hint: [book-id] [title]
allowed-tools: Read, Write, Bash, Glob
---

Scaffold a new book at `kdp-studio/books/$1/`.

1. Create the directory, plus `assets/`, `build/` and `reports/`.
2. Write a starting `manifest.json` with `stage: "research"`, the book id `$1`,
   the title `$2`, and sensible coloring-book defaults (trim `8.5x11`, paper
   `bw_white`, bleed true, `single_sided_art` true). Leave `pages` empty and
   `page_count` at 0 — the concept stage fills them in.
3. Confirm it loads: `python3 -m kdp --json concept kdp-studio/books/$1/manifest.json`.
   It will not pass yet; an empty spec is expected at this point. Report what
   it says is missing.

Then stop and tell the user the research stage comes next. Do not invent market
research, a strategy, a theme, or page subjects — those belong to
`kdp-market-researcher` and `kdp-book-strategist`, and inventing them here
would skip the two gates that check them.
