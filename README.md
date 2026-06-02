# The Longest Table — Vienna

Community fundraiser event app. Public site, admin console, captain portal, ordering, donations.

Live: https://longest-table-vienna-403607223387.us-east4.run.app/

## Local development

```bash
pip install -r requirements.txt
python app.py
# open http://localhost:5055
```

DB auto-creates at `data/longest_table.db`. Edit settings in the admin UI; defaults live in `models.py`.

## Admin sign-in

- Go to `/admin/login`
- Enter an authorized email (set in admin Settings → `admin_emails`; defaults to `rob@sagecg.com`)
- Either request a magic-link email **or** enter the bypass passcode (default `7777`, overridable via the `ADMIN_PASSCODE` env var) to sign in instantly

## Deploy

Pushing to `main` triggers Cloud Build → Cloud Run automatically (see `cloudbuild.yaml`).

```bash
git add -A
git commit -m "your change"
git push
```

Manual fallback (requires `gcloud` auth):

```bash
./deploy.sh
```

### Useful env vars on Cloud Run
- `RESEND_API_KEY` — for outbound email (without it, emails are only logged to stdout)
- `ADMIN_PASSCODE` — optional override of the `7777` admin bypass
- `FLASK_SECRET` — session signing key (set this in prod)

## Files
- `app.py` — Flask app + blueprints
- `public_routes.py`, `admin_routes.py`, `captain_routes.py` — route blueprints
- `auth.py` — magic-link + passcode admin auth
- `mailer.py` — Resend integration (dev mode = log to stdout)
- `models.py` — SQLite schema + setting defaults
- `store.py` — DB access layer
- `assigner.py` — table assignment algorithm
- `templates/`, `static/` — UI
- `Dockerfile`, `cloudbuild.yaml`, `deploy.sh` — deploy
