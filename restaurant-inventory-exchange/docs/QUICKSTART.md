# Quickstart: from nothing to a live app

Twenty minutes, mostly waiting for Supabase to provision. You need a GitHub
account (you have one), a Supabase account, and a Vercel account. Both are free
for this.

Nothing here asks you to run a server, a terminal, or a build.

---

## Step 1 — Create the Supabase project

1. Go to **[supabase.com/dashboard](https://supabase.com/dashboard)** and sign in
   with GitHub.
2. Click **New project**.
3. Fill in:
   - **Name**: `inventory-exchange`
   - **Database Password**: click **Generate a password**, then **copy it and
     save it somewhere**. You will not be shown it again. (You do not need it
     for this app; you need it if you ever connect to the database directly.)
   - **Region**: whichever is closest to your restaurants.
4. Click **Create new project** and let it provision. This takes 2–3 minutes.

---

## Step 2 — Create the database

1. In the left sidebar, click **SQL Editor**.
2. Open **[`supabase/bootstrap.sql`](../supabase/bootstrap.sql)** in this
   repository, click the **Raw** button, select all, and copy.
3. Paste the whole thing into the SQL editor.
4. Click **Run** (or press Cmd/Ctrl + Enter).

You should see **Success. No rows returned**.

That one file created every table, every row level security policy, every
business function, the four restaurants, and a starting catalog of eleven items.

**Check it worked.** Paste this and run it:

```sql
select
  (select count(*) from public.locations)       as locations,
  (select count(*) from public.inventory_items) as catalog_items,
  (select string_agg(name, ', ' order by name) from public.locations) as restaurants;
```

Expect `4`, `11`, and `287 Taco Shop, Hibachio 1, Hibachio 2, Hibachio 3`.

---

## Step 3 — Turn off email confirmation (for now)

Supabase's built-in mail service is rate limited and slow, and it will get in
your way while you are setting up.

1. Sidebar → **Authentication** → **Sign In / Providers**.
2. Open the **Email** provider.
3. Turn **Confirm email** **off**. Save.

This does not weaken the app. A brand new account still lands in the pending
queue and can see nothing at all until an admin approves it. Turn confirmation
back on later once you have your own SMTP configured, per
[DEPLOYMENT.md](DEPLOYMENT.md).

---

## Step 4 — Copy your two keys

1. Sidebar → **Project Settings** (the gear) → **API**.
2. Copy the **Project URL**. It looks like `https://abcdefgh.supabase.co`.
3. Copy the **anon** / **public** key. In newer projects this is labelled
   **Publishable key** and starts with `sb_publishable_`; in older ones it is a
   long `eyJ...` string. Either works.

Keep both handy for the next step.

**Do not copy the `service_role` / secret key.** It bypasses every security
policy in the database. It has no place in this app.

---

## Step 5 — Deploy to Vercel

1. Go to **[vercel.com/new](https://vercel.com/new)** and sign in with GitHub.
2. Find **`awesome-selfhosted`** in the repository list and click **Import**.
   (If it is not listed, click **Adjust GitHub App Permissions** and grant
   access to it.)
3. On the configure screen:
   - **Root Directory** — click **Edit** and select `restaurant-inventory-exchange`.
   - **Framework Preset** — should say **Vite**. Leave it.
   - **Build and Output Settings** — leave them. `vercel.json` already sets them.
4. Expand **Environment Variables** and add exactly two:

   | Name | Value |
   | --- | --- |
   | `VITE_SUPABASE_URL` | the Project URL from step 4 |
   | `VITE_SUPABASE_ANON_KEY` | the anon / publishable key from step 4 |

5. Click **Deploy**. It takes about a minute.

When it finishes you get a URL like `https://awesome-selfhosted-xyz.vercel.app`.
**That is your live app.** It is HTTPS, and it is public.

> If the app is on a branch rather than the repository's default branch, Vercel
> needs to know: **Project Settings → Git → Production Branch**, set it to the
> branch, then **Deployments → Redeploy**.

---

## Step 6 — Make yourself the first admin

There is no default account and no universal admin password. You create your own
account, then promote it once from your own database.

1. Open your Vercel URL.
2. Tap **Request access**.
3. Enter your name, your email and a password of at least 8 characters. Tap
   **Request access**.
4. You land on **Waiting for approval**. **Pick your restaurant from the list**
   before going further. This sets the location your account works at, which
   saves you a step later.
5. Back in Supabase → **SQL Editor**, run this with your own email:

   ```sql
   select public.bootstrap_admin('you@example.com');
   ```

6. Reload the app in your browser.

You are now an active admin. The home screen greets you by name and an
**Admin** row appears.

If you skipped 4 and the home screen says "No location set yet": go to
**Admin → Users**, tap your own name, and choose a **Location**.

---

## Step 7 — Your locations are already there

Hibachio 1, Hibachio 2, Hibachio 3 and 287 Taco Shop were created in step 2.

Go to **Admin → Locations** to rename them, deactivate one, or add more. Nothing
in the app is tied to those four names.

---

## Step 8 — Add your employees

**Admin → Users → Invite someone.** Enter their name, email, location and role,
then **Send invitation**.

Send them the app URL however you normally reach them: text, WhatsApp, taped to
the wall. When they sign up with that exact email address they are active
straight away with the role and location you chose. There is no code or link to
leak, because the invitation is matched on the email address itself.

Roles:

- **Employee** — records transfers, confirms what arrives, sees their location's history.
- **Manager** — the same, plus the staff roster for their own location.
- **Admin** — everything, including corrections and the audit trail.

Anyone who signs up without an invitation lands under
**Admin → Users → Pending access requests** for you to approve or reject.

---

## Step 9 — Put it on the home screen

**iPhone / iPad.** Open the URL **in Safari** (not Chrome — only Safari can
install web apps on iOS). Tap the share button, scroll down, tap **Add to Home
Screen**, then **Add**. It opens full screen with no browser bars.

**Android.** Open the URL in Chrome. Tap the menu, then **Add to Home screen**.

**Desktop.** Chrome and Edge show an install icon at the right of the address bar.

---

## Test it end to end

Record the transfer from the original brief.

1. Open the app as an employee (or as yourself) whose location is **Hibachio 2**.
2. Tap **Take something**.
3. Tap **287 Taco Shop**.
4. Tap **32 oz Cups**.
5. Tap **+** once so the number reads **2**.
6. Tap **Record transfer**.

You should see **Transfer recorded.** and `287 Taco Shop → Hibachio 2`.

Now check that it is a real, permanent record:

- **History** shows it, tagged **Awaiting confirmation**.
- Sign in as someone at **287 Taco Shop** → **Receive / confirm** → it is under
  **Taken from us** → tap **Yes, that left here**. It flips to **Confirmed**.
- **Admin → Balances** shows `287 Taco Shop is up on Hibachio 2 — 32 oz Cups, 2 sleeves`.
- **Admin → Audit trail** shows `Transfer recorded` and `Transfer confirmed` with
  names and timestamps.

And confirm it in the database itself. In the Supabase SQL editor:

```sql
select t.recorded_at,
       f.name as from_location,
       l.name as to_location,
       i.name as item,
       ti.quantity,
       ti.unit,
       u.full_name as recorded_by
from public.transfers t
join public.locations f on f.id = t.from_location_id
join public.locations l on l.id = t.to_location_id
join public.transfer_items ti on ti.transfer_id = t.id
join public.inventory_items i on i.id = ti.item_id
join public.app_users u on u.id = t.recorded_by
order by t.recorded_at desc;
```

---

## Afterwards

**Turn email confirmation back on** once you have SMTP configured, and set
**Authentication → URL Configuration → Site URL** to your Vercel URL so
confirmation links come back to the right place. Details in
[DEPLOYMENT.md](DEPLOYMENT.md).

**Take a backup** before you rely on this. Supabase free plan: **Database →
Backups**. Transfers are the whole point of the system.

**A custom domain**, if you want one: Vercel **Project Settings → Domains**.
