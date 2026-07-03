# Deploying the LeadPilot backend to Coolify

Target: Coolify at `http://49.13.30.199:3000` → project `5-T49LvnpF8Ul0TsSs1WJ` → env `mB9ujEv9zXB1B_H-q-TuP`.

Decisions locked in:
- **Source:** GitHub repo → Coolify (auto-deploy on push), built from the existing `Dockerfile`.
- **Database:** Supabase Postgres, **direct** connection (port 5432, `sslmode=require`).
- **Audio storage:** Supabase Storage (`STORAGE_MODE=supabase`) — survives redeploys, no volume.

---

## Step 1 — Push `voicesummary-main` to GitHub

`.env`, `.venv`, `local_storage/`, `Audio/` are already gitignored, so no secrets leak.

```bash
cd "voicesummary-main"
git init
git add .
git commit -m "Initial commit: LeadPilot FastAPI backend"
# create the repo (private) — via gh CLI:
gh repo create leadpilot-backend --private --source=. --remote=origin --push
# or create it manually on github.com and:
# git remote add origin git@github.com:<you>/leadpilot-backend.git
# git branch -M main && git push -u origin main
```

## Step 2 — Prepare Supabase (DB + Storage)

1. **DB connection string** — Supabase dashboard → Project Settings → Database → **Connection string → URI (Direct connection, port 5432)**. Shape:
   ```
   postgresql://postgres:<PASSWORD>@db.<PROJECT-REF>.supabase.co:5432/postgres?sslmode=require
   ```
   Use the **direct** URI, not the pgBouncer transaction pooler (6543) — the app opens long-lived connections.
2. **Storage bucket** — Storage → New bucket → name it `audio-calls` (keep it Private).
3. **Service role key** — Project Settings → API → `service_role` secret. Server-side only; never ships to Flutter/web.

## Step 3 — Create the resource in Coolify

In the target environment: **+ New → Resource**.

1. **Source:** *Public/Private Repository* → connect your GitHub (Coolify GitHub App for private repos, or a deploy key) → pick `leadpilot-backend`, branch `main`.
2. **Build Pack:** Coolify detects the `Dockerfile` — leave it on **Dockerfile**.
3. **Port:** set **Ports Exposes = `8000`**.
4. **Health check:** already defined in the Dockerfile (`GET /health`). Nothing to configure.

## Step 4 — Environment variables

Coolify resource → **Environment Variables** → paste (fill in real values):

```
# Database (Supabase direct)
DATABASE_URL=postgresql://postgres:<PASSWORD>@db.<PROJECT-REF>.supabase.co:5432/postgres?sslmode=require

# Sarvam (STT + diarization; also default reasoner)
SARVAM_API_KEYS=<key1>,<key2>
SARVAM_CHAT_MODEL=sarvam-105b
SARVAM_STT_MODEL=saaras:v3
SARVAM_STT_MODE=transcribe
SARVAM_TRANSLATE_MODEL=mayura:v1

# Reasoning provider (sarvam | gemini)
REASONING_PROVIDER=sarvam
# GEMINI_API_KEYS=<key>            # only if REASONING_PROVIDER=gemini
# GEMINI_MODEL=gemini-3.1-pro-preview
# GEMINI_THINKING_LEVEL=low

# Storage → Supabase
STORAGE_MODE=supabase
SUPABASE_URL=https://<PROJECT-REF>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service_role_key>
SUPABASE_STORAGE_BUCKET=audio-calls

# Auth — generate a fresh secret:  python3 -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY=<new-64-char-hex>
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080

# App
APP_HOST=0.0.0.0
APP_PORT=8000
DEBUG=false

# Frontend / CORS  (comma-separated; add every origin that calls this API)
NEXT_PUBLIC_API_URL=<public URL of THIS backend, from Step 5>
CORS_ORIGINS=<web-portal-url>,<any other frontend origin>
```

Notes:
- **Do not** reuse the dev `JWT_SECRET_KEY` — mint a new one; rotating it invalidates all existing tokens.
- Flutter/mobile talk to the API directly with a bearer token, so they don't need a CORS entry — only browser origins do.

## Step 5 — Expose it (reach it over the network)

Two options:

- **Quick, IP-based:** Coolify resource → **Ports Mappings** → `8080:8000`. Then the API is at `http://49.13.30.199:8080` (and `/docs`, `/health`). Set `NEXT_PUBLIC_API_URL=http://49.13.30.199:8080`.
- **Domain (better):** point a subdomain (e.g. `api.yourdomain.com`) A-record at `49.13.30.199`, put it in the resource's **Domains** field. Coolify's Traefik gets a Let's Encrypt cert automatically → `https://api.yourdomain.com`. Set `NEXT_PUBLIC_API_URL` to that.

## Step 6 — Deploy

Click **Deploy**. Watch the build/deploy logs. On success, `/health` should return `{"status":"healthy"}`.

## Step 7 — Migrations (run once)

On first boot, `app/main.py` runs `Base.metadata.create_all`, which creates tables from the models on the empty DB — enough to start. To keep Alembic history consistent for **future** migrations, run once after the first deploy (Coolify → resource → **Terminal / Execute Command**):

```bash
alembic upgrade head
```

Better long-term: make migrations part of every deploy by overriding the start command (Coolify → resource → **Start Command**) to:

```
sh -c "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

## Step 8 — Verify

```bash
curl http://49.13.30.199:8080/health        # or your https domain
# open /docs in a browser to sanity-check the API surface
```

Then point the Flutter app / web portal at the new `NEXT_PUBLIC_API_URL` and test an auth + upload flow end to end.

---

### After this
- Every `git push` to `main` auto-deploys (enable "Auto Deploy" on the resource, on by default).
- Set `CORS_ORIGINS` the moment you know the deployed frontend URL — a wrong/missing origin is the #1 cause of "works in curl, fails in browser".
- Turn on Coolify's scheduled backups only if you add a Coolify-managed Postgres later; with Supabase, backups are Supabase's job.
