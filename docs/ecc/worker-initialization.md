# Worker initialization and landing records

Phase 2O. Two frictions in the worker lifecycle were costing a round trip and a
false alarm on every work item. This records what was fixed, what was measured,
and one thing that deliberately was **not** fixed.

## The base problem, correctly stated

Earlier phases described P2F-DEFECT-5 as the harness caching a stale
"session-start ref". That was wrong, and the wrong description invited the wrong
fix.

Measured: the ref every worker worktree starts on is exactly `master` — the
repository's default branch, unmoved since the container cloned it. The harness
branches a new worktree from the **default branch**, which is entirely
reasonable behaviour. The problem is only that sustained work happens on a
long-lived feature branch, so the gap grows without bound. It reached **39
commits** by the end of Phase 2N; workers spawned during that phase were 35, 36
and 37 commits behind.

Consequences of stating it correctly:

- This will not be fixed upstream, because it is not broken upstream.
- `ecc-worker-base.js prepare` is the permanent adapter, not a workaround.
- No amount of configuration removes the need to re-base before a worker reads
  anything.

## Why worker initialization costs two turns

Three constraints intersect:

1. A worker must not read repository state before `prepare` re-bases it, or it
   reads a tree months out of date.
2. `prepare` will not move a worktree with pending changes.
3. A first turn that writes nothing risks the harness reclaiming the worktree,
   which destroys its branch too (P2K-DEFECT-1).

### The elimination that was attempted

Phase 2O tried to remove the extra turn. The worktree does exist on disk before
the first turn ends — verified — so the Lead can re-base it and place a brief
inside it while the worker waits. A worker was spawned with instructions to poll
for `.WORKER-BRIEF.md` and act on it within the same turn.

**The worker refused, and the refusal was correct.** Its reasoning: instructions
delivered as a file inside the workspace cannot be authenticated; the prompt
pressed it to skip its normal check-in; and it was told to assert a fact it had
not verified. That is the shape of a prompt-injection payload regardless of
whether the content is benign. It verified what it could read-only, reported
accurately, and declined the write.

The objection is structural, not stylistic. The Lead has Bash and no
confinement, and a confined worker can write inside its own worktree, so **an
in-worktree file is not an authenticated channel**. Instructions must arrive
through the harness — the spawn prompt or a message — and both cost a turn.

**Decision: the two-turn structure stays.** It cannot be removed without
weakening a property that is working. What was removed is the waste.

### The protocol

1. Spawn with the **full brief in the spawn prompt** (authenticated), plus:
   *your worktree is on the default branch and is not current — do not read
   repository files this turn; write your implementation plan to `PLAN.md`,
   then stop.*
2. `prepare --agent-id <id> --target <branch> --carry-changes` — re-bases and
   carries the plan across. `git checkout -B` refuses when carrying would
   collide, so the unsafe case fails closed, and the change set is compared
   before and after.
3. **Review the plan.** This is a real gate; a misunderstanding is far cheaper
   to catch here than after implementation.
4. Message the worker: *your worktree is current, proceed.*

Turn one now yields a reviewable plan instead of the string `ready`. `PLAN.md`
must still be removed before integration, because `ecc-integrate` requires the
change set to match the approved manifest exactly — and **the Lead removes it,
not the worker.** A confined worker's tools are Read/Grep/Glob/Edit/Write;
`Write` can overwrite a file's contents but cannot unlink a path, so a worker
has no way to delete anything. An early version of this protocol told the worker
to delete its own plan, which is an instruction it cannot follow. A worker
correctly reported the limitation rather than routing around it.

## Landing records

A worker resumed after its work is integrated finds its worktree gone, because
removing it is normal cleanup. Nothing used to say so, and in Phase 2N a worker
reported its work catastrophically lost — with accurate evidence and the wrong
conclusion.

`ecc-integrate` now writes two records on a successful integration:

| record | audience | why there |
|---|---|---|
| `<gitCommonDir>/ecc-worker-base/<id>.landed.json` | tooling | inside the git directory, which a confined worker cannot write, so it cannot be forged |
| `<repoRoot>/.claude/worktrees/agent-<id>.LANDED.md` | the worker | the **sibling** of the missing worktree — the directory a homeless worker actually globs |

Both sit under ignored paths, so neither can reach a manifest.

`reestablish`, `prepare` and the new read-only `status` subcommand now report
`LANDED` — naming the commit the work landed as — instead of `NO_BRANCH` or
`NO_WORKTREE`. Genuine loss still reports as loss; the distinction is the whole
point, so the negative case is asserted too.

## The state store: guarding without touching a managed file

Two defects in the `~/.claude` state-store installation:

1. `schemas/state-store.schema.json` was **missing entirely**, so every write
   through the installed copy threw `ENOENT` during validation. Reads worked,
   which is why it survived several phases unnoticed.
2. `createStateStore` silently ignored unrecognised options. A caller passing
   `repoRoot` — a plausible key the API does not have — received the **default
   production database** rather than an error. That is the worst outcome
   available to a misconfigured caller, and it happened: a Lead script intending
   a repo-scoped database quietly read and wrote the real ledger.

The schema was simply restored; it is not a managed file, and its absence was
pure breakage.

The option guard took a wrong turn worth recording. The obvious fix — add the
check to `scripts/lib/state-store/index.js` — was implemented, and it worked.
It also registered as **install drift**: that file is ECC-managed, the install
manifest records its content hash, and changing it flipped install health to
`warning`. That extra attention item then broke **four ECC tests**, which build
a temporary database but read the real `~/.claude` while asserting on it.
Causation was confirmed by counterfactual: the manifest-matching file passes
21/0, the patched one fails 17/4.

Carrying four permanently red tests to guard against a typo is a bad trade. A
regression baseline nobody trusts is worse than the bug it hides, and "those
four are expected" is how a baseline rots.

So the guard moved to `scripts/lib/ecc-state.js`, a file this workforce owns.
Every ECC script opens the store through `openStateStore`, the managed copy
stays byte-identical to what ECC installed, and drift is back to zero.

The limitation is real and stated rather than hidden: code calling
`createStateStore` directly is still unguarded. That is acceptable only because
the ECC workforce is the sole writer to this database and goes through the
wrapper. `state-store-install.test.js` asserts the managed file stays pristine,
so the tempting shortcut fails loudly if anyone takes it again.

**Separately, an ECC test-isolation defect was found and not fixed** (it is
third-party): a unit test that constructs a temp database should not have its
assertions depend on the developer's real `~/.claude` install state. Any
legitimate local install change breaks it. Recorded, not worked around.

## What was deliberately not done

**Local-vs-CI Electron divergence (P2N-FINDING-2) was left alone.** Phase 2N saw
an e2e suite pass 30/30 locally, three runs in a row, and still fail two tests in
CI on a timing-dependent race. The temptation is to build a harness layer that
reproduces CI timing locally. That was not done: the existing workflow already
caught the failure through CI itself, and a new layer would be speculative
machinery whose own failure modes are likelier than the problem it targets. It
is revisited when there is evidence it would pay for itself.

Minimum test-count enforcement likewise remains **REPORT ONLY**, per
`docs/ecc/test-count-policy.md`. Visibility is doing the job; a gate is not
earning its cost yet.
