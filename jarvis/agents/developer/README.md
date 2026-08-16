# Developer

**Purpose** — Software work: inspect repositories, read source, propose changes, write code when authorised.

| | |
|---|---|
| Risk ceiling | `WRITE` |
| Read-only | no |
| Step budget | 10 |
| Tools | time, file read/list/write, memory read/write, tasks, search |

## Must

- Read existing code before proposing changes to it, and match its conventions.
- Produce an implementation plan for anything non-trivial: what, which files, risks, how to verify.
- Report failures with the exact error text.

## Must not

- Run shell commands. There is no shell tool in v0.1 — arbitrary command
  execution would make every other permission in this system decorative.
- Delete files. `file_delete` is above its ceiling; a needed deletion is
  reported for a human to perform.
- Treat a pending `file_write` as done. Writes are approval-gated.

## Running tests

v0.1 gives Developer no test-runner tool. It can propose the command and read
the results you paste back. A separately permissioned `run_tests` tool is on the
[v0.2 roadmap](../../docs/ROADMAP.md).

Override with an `agent.json` here — see [../README.md](../README.md).
