# Development

## Prerequisites

- Python 3.11 or newer
- PostgreSQL 16 or newer (client *and* server)

## First-time setup

```bash
cd nexus/backend

python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Create the role and both databases. The second one is used by the test suite,
which truncates every table:

```sql
CREATE ROLE nexus LOGIN PASSWORD 'nexus';
CREATE DATABASE nexus      OWNER nexus;
CREATE DATABASE nexus_test OWNER nexus;
```

Then configure and migrate:

```bash
cp .env.example .env       # edit NEXUS_DATABASE_URL if your setup differs
.venv/bin/alembic upgrade head
```

## Running

```bash
.venv/bin/python -m nexus.main
```

- API docs: <http://127.0.0.1:8000/api/docs>
- Health: <http://127.0.0.1:8000/api/v1/health>

For readable logs during development, set `NEXUS_LOG_FORMAT=console`.

## The checks

Run all four before pushing; CI runs the same commands.

```bash
.venv/bin/ruff format --check .   # formatting
.venv/bin/ruff check .            # lint + security rules (bandit subset)
.venv/bin/mypy nexus              # type checking
.venv/bin/python -m pytest        # tests
```

Useful variations:

```bash
.venv/bin/python -m pytest -k config          # one area
.venv/bin/python -m pytest -m "not integration"  # skip tests needing PostgreSQL
.venv/bin/python -m pytest -x -vv             # stop at first failure, verbose
```

`NEXUS_ENV_FILE=/dev/null` in front of a command ignores your local `.env`,
which is how CI runs and a good way to check that a change does not depend on
your machine's configuration.

## Continuous integration

`.github/workflows/nexus-ci.yml` runs on every push and pull request that
touches `nexus/**`:

| Job | Gate |
|---|---|
| `quality` | `ruff format --check`, `ruff check` (including bandit security rules), `mypy` |
| `test` | The full suite against PostgreSQL 16 **and** 17 |
| `migrations` | `upgrade head`, `alembic check` (models must match the schema), then `downgrade base` and re-apply |
| `security` | `pip-audit --strict` over runtime *and* dev dependencies |
| `startup` | Production must refuse the development signing key, and must accept a correctly configured production environment |

Two of these deserve explanation:

- **`alembic check`** catches the most common migration mistake: changing a
  model and forgetting to generate the migration. Autogenerate compares the
  models against the live schema, and any difference fails the build.
- **`pip-audit`** fails on a dependency with a known advisory. This is why
  `requirements-dev.txt` carries lower bounds that look arbitrary
  (`pytest>=9.0.3`, `setuptools>=83.0.0`) — they are the first patched
  versions. When the audit fails, upgrade the package and run the suite; do not
  add an ignore unless the advisory genuinely does not apply, and say why in
  the file.

## Testing strategy

- Tests use a **real PostgreSQL database**, never SQLite and rarely mocks. The
  schema depends on JSONB, INET, identity columns and `ON DELETE` semantics
  that SQLite does not implement.
- The schema is built by running the real migrations, so every run also tests
  the migration path.
- Test isolation is by `TRUNCATE … RESTART IDENTITY CASCADE` between tests.
  The suite refuses to run unless the database name contains `test`.
- Tests that use the `session` fixture run inside a transaction that is always
  rolled back, so provoking an `IntegrityError` is safe.
- Write failure tests, not only happy paths. A dependency being down, a
  malformed input, a forged header, and a secret in an error string are all
  first-class test subjects.

Point the suite elsewhere with `NEXUS_TEST_DATABASE_URL`.

## Adding a database migration

1. Change the model in `nexus/db/models/`.
2. Make sure the model is imported in `nexus/db/models/__init__.py` — Alembic
   discovers tables through `Base.metadata`, and a model that is never imported
   is a table autogenerate will propose to **drop**.
3. Generate a draft:

   ```bash
   .venv/bin/alembic revision --autogenerate -m "what changed"
   ```

4. **Read and edit it.** Autogenerate is a first draft, not an author. It misses
   data migrations, gets index ordering wrong, and never writes a comment
   explaining why a constraint exists. Rename the revision id to something
   readable (`0002_devices_and_events`) while you are there.
5. Write a working `downgrade`. A migration you cannot reverse is a deployment
   you cannot roll back.
6. Verify both directions and that the models match the schema:

   ```bash
   .venv/bin/alembic upgrade head
   .venv/bin/alembic downgrade -1
   .venv/bin/alembic upgrade head
   .venv/bin/alembic check     # must report no new operations
   ```

## Project layout

```
nexus/backend/
  nexus/
    core/        configuration, logging, errors, RBAC, app context
    db/          engine and session management, ORM models
    api/         middleware, exception handlers, dependencies, v1 routes
    services/    domain logic callable from both the API and workers
    app.py       application factory
    main.py      process entry point
  migrations/    Alembic environment and versions
  tests/         pytest suite
```

The rule that keeps this from tangling: `core` depends on nothing else in the
project, `db` depends on `core`, `services` depend on `core` and `db`, and `api`
depends on all three. Nothing depends on `api`.

## Conventions

- Comments explain *why*, not *what*. The code already says what it does.
- Domain code raises `AppError` subclasses, never `HTTPException`.
- Never log a secret; never put internal error text into a response body.
- Every state-changing action that matters gets an audit event.
- Formatting is `ruff format`; do not hand-format around it.
