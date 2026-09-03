# Restaurant Inventory Exchange

A record of what moves between restaurant locations. When Hibachio 2 borrows two
sleeves of 32 oz cups from 287 Taco Shop, someone opens this on their phone and
taps four times. Nobody has to remember it and nobody has to text about it.

```
287 Taco Shop  →  Hibachio 2
32 oz Cups · 2 sleeves
Taken by John Smith · Today 3:42 PM
```

## What it is

A progressive web app on Supabase. React and TypeScript in the browser,
Postgres and Supabase Auth behind it, no server of your own to run or patch.
It installs to a phone home screen from a normal URL, with no app store.

The whole employee experience is four buttons: take something, give something,
receive and confirm, history.

## How it is put together

| Layer | Choice | Why |
| --- | --- | --- |
| Frontend | React 19, TypeScript, Vite, `vite-plugin-pwa` | Small, standard, installable |
| Data and auth | Supabase (Postgres, Auth, row level security) | No servers to administer |
| Authorisation | Postgres RLS policies and `SECURITY DEFINER` functions | The browser is never the authority |
| Hosting | Any static host, Vercel by default | The built app is static files |

Two decisions are worth stating plainly.

**Transfers are records, not rows to edit.** There is no `UPDATE` or `DELETE`
policy anywhere on `transfers`, `transfer_items` or `transfer_confirmations`.
Not for employees, not for managers, not for admins. A correction is a new row
in `transfer_adjustments` carrying the previous value, the new value, a reason
and who made it, and the read models fold that into an "effective quantity"
while the original stays visible on the page.

**Every write goes through a function that re-checks the caller.** The browser
holds only the anon key, which grants nothing on its own. Reads are filtered by
RLS, and writes go through RPCs that load the caller's own row and verify role
and location before touching anything. Deleting the frontend's permission
checks would change what the app *shows*, not what it can *do*.

## Roles

| | Admin | Manager | Employee |
| --- | --- | --- | --- |
| Record transfers | ✓ | ✓ | ✓ |
| Confirm what arrives at their location | ✓ | ✓ | ✓ |
| See their location's history | all locations | ✓ | ✓ |
| See the staff roster | everyone | own location | themselves |
| Approve and disable accounts | ✓ | | |
| Manage locations and the catalog | ✓ | | |
| Correct or void a transfer | ✓ | | |
| Read the audit trail | ✓ | | |

There is no universal admin password. The first admin is promoted by running one
SQL statement against your own database, and every admin after that is made by
an existing admin.

## Running it locally

```bash
npm install
cp .env.example .env.local     # fill in your Supabase URL and anon key
npm run dev
```

```bash
npm test          # 83 tests: database policies, RPCs, and the mobile workflow
npm run typecheck
npm run build
```

The database tests are not mocks. They apply the real migrations from
`supabase/migrations/` to an embedded Postgres and then act as actual signed-in
users, so a policy that would leak another location's transfers fails the suite.

## Layout

```
supabase/migrations/   schema, RLS policies, views, business functions, seed data
supabase/tests/        the Supabase-provided objects, recreated for the test runner
src/routes/            one file per screen
src/lib/               Supabase client, data access, formatting
src/ui/                the whole design system, about ten components
tests/db/              policies and RPCs, against real Postgres
tests/ui/              the take flow, the confirm queue, admin approval
```

## Deploying

**[docs/QUICKSTART.md](docs/QUICKSTART.md)** is the shortest path to a live URL:
create a Supabase project, paste one file into its SQL editor, import the
repository into Vercel with two environment variables, and promote yourself to
admin. About twenty minutes, most of it waiting.

[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) is the longer reference: email and SMTP,
migrations with the CLI, other hosts, backups, and troubleshooting.

`supabase/bootstrap.sql` is a generated bundle of every migration, so a new
project needs one paste instead of seven. Rebuild it with `npm run db:bundle`
after changing anything in `supabase/migrations/`; a test fails if it drifts.
