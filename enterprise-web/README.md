# AQATE Enterprise Web

Next.js 14 App Router console for **AI QA Telesale Enterprise**.

## Stack

- Next.js 14 + TypeScript + TailwindCSS
- JWT auth (cookie + localStorage)
- API base: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`)

## Run locally

```bash
cd enterprise-web
cp .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

Demo login: `lead@acme.vn` / `demo1234` (falls back to offline demo data if API is down).

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Dev server (port 3000) |
| `npm run build` | Production build |
| `npm run start` | Start production server |
| `npm run lint` | ESLint |

## Docker

```bash
docker build -t aqate-enterprise-web \
  --build-arg NEXT_PUBLIC_API_URL=http://api:8000 .
docker run -p 3000:3000 aqate-enterprise-web
```

## Routes

`/login` · `/dashboard` · `/calls` · `/calls/[id]` · `/qa` · `/coaching` · `/rules` · `/dna` · `/admin` · `/appeals`
