# Deployment Guide

## Prerequisites

- [Fly.io CLI](https://fly.io/docs/hands-on/install-flyctl/) installed and authenticated (`fly auth login`)
- A Supabase project with `DATABASE_URL` and `SUPABASE_JWT_SECRET` available
- Node.js 18+ for the frontend build

---

## Backend (FastAPI) → Fly.io

### 1. Create the app (first time only)

```bash
fly apps create signal-engine-api
```

### 2. Set secrets

```bash
fly secrets set \
  DATABASE_URL="postgresql://postgres.xxxx:password@aws-0-eu-central-1.pooler.supabase.com:5432/postgres" \
  SUPABASE_JWT_SECRET="your-jwt-secret-from-supabase-settings" \
  ANTHROPIC_API_KEY="sk-ant-..." \
  ALLOWED_ORIGINS="https://your-frontend-domain.com"
```

> Get `DATABASE_URL` from Supabase → Settings → Database → Connection string (URI)  
> Get `SUPABASE_JWT_SECRET` from Supabase → Settings → API → JWT Settings

### 3. Deploy

```bash
fly deploy
```

### 4. Verify

```bash
fly logs
curl https://signal-engine-api.fly.dev/health
```

---

## Frontend (React/Vite) → Static hosting

The frontend is a static SPA — deploy to Vercel, Netlify, or Fly.io static hosting.

### Build

```bash
cd dashboard/frontend
cp .env.local.example .env.local
# Edit .env.local with your Supabase URL and anon key
npm install
npm run build
# dist/ is ready to deploy
```

### Vercel (recommended)

```bash
cd dashboard/frontend
npx vercel --prod
```

Set these environment variables in the Vercel dashboard:
- `VITE_SUPABASE_URL` — your Supabase project URL
- `VITE_SUPABASE_ANON_KEY` — your Supabase anon/public key

### Proxy API calls

Configure your frontend host to proxy `/api/*` requests to the Fly.io backend.

**Vercel** — add `vercel.json` in `dashboard/frontend/`:
```json
{
  "rewrites": [
    { "source": "/api/:path*", "destination": "https://signal-engine-api.fly.dev/api/:path*" }
  ]
}
```

---

## Supabase Auth setup

1. Supabase Dashboard → Authentication → URL Configuration  
   - Site URL: `https://your-frontend-domain.com`
   - Redirect URLs: `https://your-frontend-domain.com/**`

2. Enable email auth (enabled by default) — no SMTP config needed for magic links if using Supabase's built-in email.

---

## Environment variable reference

| Variable | Where used | Description |
|---|---|---|
| `DATABASE_URL` | Backend | Supabase PostgreSQL connection string |
| `SUPABASE_JWT_SECRET` | Backend | JWT validation secret from Supabase settings |
| `ANTHROPIC_API_KEY` | Backend | Claude API key for AI thesis generation |
| `ALLOWED_ORIGINS` | Backend | Comma-separated list of allowed CORS origins |
| `VITE_SUPABASE_URL` | Frontend | Supabase project URL |
| `VITE_SUPABASE_ANON_KEY` | Frontend | Supabase anon/public API key |
