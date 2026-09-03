# Deploying

Start to finish this takes about twenty minutes. You need a Supabase account
and somewhere to host static files. Nothing here needs a server you maintain.

---

## 1. Create the Supabase project

1. Sign in at [supabase.com](https://supabase.com) and choose **New project**.
2. Give it a name, set a database password, and pick the region closest to your
   restaurants.
3. Wait for provisioning to finish, then open **Project Settings → API** and
   copy two values:
   - **Project URL**, like `https://abcdefgh.supabase.co`
   - **anon public** key

The `anon` key is meant to be public. Every request it signs is still filtered
by row level security. The **service_role** key on that same page is not: it
bypasses every policy. Never put it in this app, in a `VITE_` variable, or in
anything the browser downloads.

---

## 2. Run the database migrations

Two ways. Either works; the CLI is better once you have more than one
environment.

### With the Supabase CLI

```bash
npm install -g supabase
supabase login
supabase link --project-ref <your-project-ref>
supabase db push
```

`supabase db push` applies everything in `supabase/migrations/` in order.

### From the dashboard

Open **SQL Editor** and run each file in `supabase/migrations/` in filename
order, one at a time:

```
0001_schema.sql              tables, enums, constraints
0002_security_helpers.sql    the functions the policies call
0003_rls.sql                 row level security, and the grants
0004_views.sql               the read models the app queries
0005_rpc.sql                 transfers, confirmations, corrections
0006_admin_rpc.sql           users, locations, catalog, invitations
0007_seed_reference_data.sql the four restaurants and a starting catalog
```

Do **not** run anything from `supabase/tests/`. That directory recreates objects
Supabase already provides, and only exists so the test suite can run the real
migrations against an embedded Postgres.

Afterwards, confirm the seed worked: **Table Editor → locations** should list
Hibachio 1, Hibachio 2, Hibachio 3 and 287 Taco Shop. Rename them, deactivate
them, or add more from inside the app later. Nothing in the schema is tied to
those four.

---

## 3. Configure authentication

In **Authentication → Providers**, leave **Email** enabled and turn every other
provider off. Password sign-in is all this app uses.

In **Authentication → URL Configuration**, set:

- **Site URL** to the address you will deploy to, e.g. `https://exchange.example.com`
- **Redirect URLs** to that same address (add `http://localhost:5173` while developing)

In **Authentication → Providers → Email**, decide on **Confirm email**:

- **On** (recommended for production). A new account must click a link in their
  inbox before they can sign in. Supabase's built-in mail service is rate
  limited to a handful of messages an hour, so for real use connect your own
  SMTP under **Project Settings → Authentication → SMTP Settings**.
- **Off** (fine while you are still setting things up). Accounts work
  immediately after signup. This does not weaken the app's authorisation:
  a new account still lands in the pending queue with access to nothing until
  an admin approves it.

Set a minimum password length of at least 8 under **Authentication → Policies**.

---

## 4. Set the environment variables

Locally:

```bash
cp .env.example .env.local
```

```
VITE_SUPABASE_URL=https://abcdefgh.supabase.co
VITE_SUPABASE_ANON_KEY=eyJhbGciOi...
```

On your host, set the same two names. On Vercel that is
**Project Settings → Environment Variables**, for Production, Preview and
Development. Vite inlines `VITE_` variables at build time, so after changing
them you must redeploy, not just restart.

If either value is missing the app shows a short setup screen instead of
failing with a stack trace.

---

## 5. Deploy the frontend

The build output is static files. Any static host works.

### Vercel

```bash
npm install -g vercel
vercel
```

Or connect the repository in the Vercel dashboard. Settings:

| Setting | Value |
| --- | --- |
| Framework preset | Vite |
| Root directory | `restaurant-inventory-exchange` |
| Build command | `npm run build` |
| Output directory | `dist` |

The app uses client-side routing, so every path must serve `index.html`. Vercel
does this for the Vite preset automatically. On Netlify add a `_redirects` file
containing `/* /index.html 200`. On Cloudflare Pages set the SPA fallback to
`index.html`.

### Anywhere else

```bash
npm run build
# upload dist/ to your host
```

Serve it over HTTPS. Service workers, and therefore installing to a home
screen, only work on a secure origin.

---

## 6. Create the first admin

There is no default account and no universal admin password. You make yourself
an admin once, by hand, against your own database.

1. Open the deployed app and choose **Request access**.
2. Sign up with your own name, email and password. Confirm the email if you
   turned confirmation on.
3. In the Supabase dashboard open **SQL Editor** and run:

   ```sql
   select public.bootstrap_admin('you@example.com');
   ```

4. Reload the app. You are an active admin, and **Admin** appears on the home
   screen.

`bootstrap_admin` is deliberately not granted to signed-in users. The SQL editor
connects as `postgres`, which is why it works there and nowhere else. It refuses
an address that has not signed up yet.

Then, still as the admin:

1. **Admin → Locations** — rename the seeded restaurants or add your own.
2. **Admin → Users** — set your own location, if the bootstrap left it empty.
3. **Admin → Inventory catalog** — adjust items, categories and units.

---

## 7. Getting employees in

Two routes, both safe to leave open at the same time.

**Invitation (preferred).** In **Admin → Users → Invite someone**, enter a name,
email, location and role. When that person signs up with that exact address they
are active immediately with the role and location you chose. There is no token
to leak: the invitation is matched on the verified email address, so only
someone who controls that mailbox can take it up. Send them the app's URL by
whatever means you like.

If you have SMTP configured you can also invite from
**Authentication → Users → Invite user** in the Supabase dashboard; the two
work together, because the app's own invitation is what assigns the role and
location.

**Request access.** Anyone can open the app, tap **Request access**, sign up and
say which restaurant they work at. They land in the pending queue with access to
nothing but the list of location names. They appear under
**Admin → Users → Pending access requests** where you set their role and
location and tap **Approve**. Rejecting disables the account rather than
deleting it, so the record of the request survives.

Turning an account off takes effect on their next request. A disabled account
can sign in and see nothing.

---

## 8. Installing on a phone

**iPhone and iPad.** Open the URL in Safari, tap the share button, then
**Add to Home Screen**. It opens without browser chrome and behaves like an app.
Chrome on iOS cannot install web apps; it has to be Safari.

**Android.** Open the URL in Chrome. A prompt to install usually appears; if it
does not, use the menu and **Add to Home screen**.

**Desktop.** Chrome and Edge show an install icon at the right of the address
bar.

The app shell is cached, so it opens instantly. It does not fake being online:
recording a transfer needs a connection, because the record has to reach the
database to exist.

---

## Keeping it healthy

**Backups.** Supabase takes daily backups on paid plans. On the free plan use
**Database → Backups** to download one periodically, or run `supabase db dump`
on a schedule. Transfers are the whole point of this system; do not run it for
long without a backup you have tested restoring.

**Upgrades.** New database changes arrive as new files in
`supabase/migrations/`. Apply them the same way as in step 2. Never edit a
migration that has already run on a database.

**Watching it.** **Admin → Audit trail** shows every change with its old and new
values. **Admin → Activity** filters transfers by location, person, item, date
range, direction and confirmation state. **Admin → Balances** shows what each
restaurant is up on another after everything that went back the other way.

---

## Troubleshooting

**"This build has no Supabase project attached."** The two `VITE_` variables are
missing or empty at build time. Set them on the host and redeploy.

**Sign-in works but the app sits on "Waiting for approval".** That is correct
for a new account. Approve it under **Admin → Users**, or run
`bootstrap_admin` if it is the first one.

**"You do not have access to do that."** The database refused the write. That is
row level security doing its job. Check the account's role and location under
**Admin → Users**.

**A confirmation button will not work.** A transfer is confirmed by the
*other* location, never the one that recorded it. Something you gave is
confirmed by the receiver; something you took is acknowledged by the shop you
took it from.

**Emails are not arriving.** Supabase's built-in mail service is heavily rate
limited and is not meant for production. Configure your own SMTP under
**Project Settings → Authentication → SMTP Settings**, or turn off email
confirmation and use invitations.
