# Coolify Deployment Guide — Orchestrator API

> **Last updated:** 2026-02-24
>
> Step-by-step guide to deploy the FastAPI orchestrator to Coolify using the existing Dockerfile.

---

## Prerequisites

1. A running **Coolify** instance (self-hosted or cloud)
2. The GitHub repo `https://github.com/ConferInc/orchestrator.git` (already pushed)
3. Your **Supabase credentials** ready

---

## Step 1: Connect GitHub Repo to Coolify

1. Log in to your **Coolify dashboard**
2. Go to **Projects** → click **+ New** → **+ New Resource**
3. Select **Public Repository** (or Private if you connected GitHub)
4. Paste the repository URL:

   ```
   https://github.com/ConferInc/orchestrator.git
   ```

5. Select branch: **`main`**
6. Click **Continue**

---

## Step 2: Configure the Build

Coolify will auto-detect the `Dockerfile`. Verify these settings:

| Setting | Value |
|---|---|
| **Build Pack** | `Dockerfile` |
| **Dockerfile Location** | `/Dockerfile` |
| **Branch** | `main` |
| **Port Exposes** | `8100` |
| **Port Mappings** | Leave default (Coolify handles reverse proxy) |

> [!IMPORTANT]
> Make sure "Port Exposes" is set to **8100** — this is the port the FastAPI server listens on inside the container.

---

## Step 3: Set Environment Variables

Go to the **Environment Variables** section in Coolify and add the following:

### Required Variables

| Variable | Value | Description |
|---|---|---|
| `SUPABASE_URL` | `https://wpgbagzljljnczmhbcdq.supabase.co` | Your Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJhbGciOiJIUz...` | Your Supabase service role key |
| `WEBHOOK_SECRET` | *(generate a strong random string)* | Secret for validating incoming webhooks |
| `ORCHESTRATOR_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### Optional Variables

| Variable | Default | Description |
|---|---|---|
| `WEBHOOK_HOST` | `0.0.0.0` | Host to bind to (keep as `0.0.0.0` for Docker) |
| `WEBHOOK_PORT` | `8100` | Port for the API server |
| `PARALLEL_MAX_CONCURRENCY` | `4` | Max parallel pipeline sources |
| `NEO4J_REALTIME_POLL_INTERVAL` | `5` | Realtime worker polling interval (seconds) |

> [!CAUTION]
> Never hardcode secrets in the Dockerfile or commit them to Git. Always use Coolify's environment variable UI.

---

## Step 4: Configure Domain & HTTPS

1. In Coolify resource settings, go to **Domains**
2. Add your domain, e.g.:

   ```
   orchestrator.yourdomain.com
   ```

3. Coolify will **automatically provision an SSL certificate** via Let's Encrypt
4. Your API will be available at: `https://orchestrator.yourdomain.com`

> [!IMPORTANT]
> After deployment, update your Supabase webhook URLs to point to:
>
> ```
> https://orchestrator.yourdomain.com/webhooks/supabase
> ```

---

## Step 5: Health Check Configuration

The Dockerfile already includes a health check. Coolify will use it automatically:

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8100/health || exit 1
```

You can also verify manually after deployment:

```bash
curl https://orchestrator.yourdomain.com/health
```

Expected response:

```json
{ "status": "healthy" }
```

---

## Step 6: Deploy

1. Click **Deploy** in Coolify
2. Watch the build logs — you should see:

   ```
   Step 1/7 : FROM python:3.11-slim AS base
   Step 2/7 : WORKDIR /app
   Step 3/7 : RUN apt-get update ...
   Step 4/7 : COPY pyproject.toml ./
   Step 5/7 : RUN pip install ...
   Step 6/7 : COPY . .
   Step 7/7 : CMD ["python", "-m", "orchestrator", "serve"]
   ```

3. Wait for the green **"Running"** status

---

## Step 7: Verify Deployment

### 7a. Check the health endpoint

```bash
curl https://orchestrator.yourdomain.com/health
```

### 7b. Check the API docs

Open in browser:

```
https://orchestrator.yourdomain.com/docs
```

This should show the Swagger UI with all API endpoints.

### 7c. Test a webhook

```bash
curl -X POST https://orchestrator.yourdomain.com/webhooks/supabase \
  -H "Content-type: application/json" \
  -H "x-webhook-secret: your-secret-value-here" \
  -d '{
    "type": "INSERT",
    "table": "recipes",
    "schema": "gold",
    "record": {"id": "test-uuid", "title": "Test Recipe"},
    "old_record": null
  }'
```

---

## Step 8: Enable Auto-Deploy (Optional)

1. In Coolify resource settings, enable **"Auto Deploy"**
2. Every push to `main` on GitHub will trigger a new deployment
3. This works for both `origin` and `upstream` repos

---

## Architecture After Deployment

```
┌──────────────────────────┐
│     Supabase Database    │
│  gold.recipes webhook ───┼──── POST /webhooks/supabase ──┐
│  gold.products webhook ──┼──── POST /webhooks/supabase ──┤
│  ... (13 webhooks total)─┼──── POST /webhooks/supabase ──┤
└──────────────────────────┘                               │
                                                           ▼
                                              ┌────────────────────┐
                                              │   Coolify Server    │
                                              │  ┌──────────────┐  │
                                              │  │   Docker      │  │
                                              │  │  Container    │  │
                                              │  │              │  │
                                              │  │  FastAPI API  │  │
                                              │  │  :8100        │  │
                                              │  │              │  │
                                              │  │  Prefect      │  │
                                              │  │  Flows        │  │
                                              │  └──────────────┘  │
                                              │                    │
                                              │  HTTPS via Caddy   │
                                              │  (auto SSL)        │
                                              └────────────────────┘
                                                           │
                                              ┌────────────┴──────────┐
                                              │      Neo4j Graph      │
                                              └───────────────────────┘
```

---

## Troubleshooting

| Issue | Fix |
|---|---|
| **Build fails at `pip install`** | Check that `pyproject.toml` is in the root of the repo |
| **Health check fails** | Verify `WEBHOOK_PORT` env var is `8100` and port is exposed |
| **Webhooks return 401** | Ensure `WEBHOOK_SECRET` env var in Coolify matches the `x-webhook-secret` header value in your Supabase webhooks |
| **Container starts but no response** | Check Coolify logs for Python startup errors; verify `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set |
| **"Module not found" errors** | The `pip install -e .` in Dockerfile should handle this; check build logs |
| **Auto-deploy not triggering** | Ensure Coolify has a webhook set up to your GitHub repo (or manually click Deploy) |

---

## Updating Environment Variables After Deploy

If you need to change env vars (e.g., rotate `WEBHOOK_SECRET`):

1. Go to Coolify → your resource → **Environment Variables**
2. Update the value
3. Click **Restart** (not Rebuild) — env var changes don't need a rebuild
