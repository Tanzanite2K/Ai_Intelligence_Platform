# FrontierAtlas web app (frontend)

Next.js 16 (App Router) · React 19 · TypeScript (strict) · Tailwind CSS 4 · shadcn/ui-style components (Radix) ·
Lucide · Recharts · TanStack Query · Zod.

The app only **reads** the FastAPI `/api/v1` API. It never holds provider keys; the single setting
`NEXT_PUBLIC_API_BASE_URL` is the public URL of the API.

## Run (Windows)

From the project root: `RUN_APP.cmd` (development) or `RUN_APP.cmd -Production`. It checks Node.js 20.9+,
installs dependencies on first run, starts the API on `127.0.0.1:8000` (read-only database) and the web app on
`127.0.0.1:3000`, then opens the browser.

## Run manually

```bash
# terminal 1 - API (project root)
set API_HOST=127.0.0.1        # PowerShell: $env:API_HOST="127.0.0.1"
.venv\Scripts\python -m src.main serve

# terminal 2 - web app
cd frontend
copy .env.example .env.local  # NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
npm ci
npm run dev                   # http://127.0.0.1:3000
```

## Checks

```bash
npm run typecheck   # tsc --noEmit (strict)
npm run lint        # eslint (next core-web-vitals + typescript rules)
npm run build       # production build
npm run check       # all three
```

## Structure

| Path | Purpose |
|---|---|
| `src/lib/api/schemas.ts` | Zod schemas mirroring `src/api/v1/schemas.py`; every response is validated |
| `src/lib/api/client.ts` | Central typed client (`api.*`), typed `ApiError` (network / http / contract / rate_limited) |
| `src/lib/api/hooks.ts` | TanStack Query hooks |
| `src/lib/nav.ts` | Sidebar sections; pages of later phases are shown disabled, never as dead links |
| `src/components/ui/` | shadcn/ui-style primitives (button, card, badge, skeleton, tooltip, dropdown, sheet) |
| `src/components/layout/` | App shell, collapsible sidebar, mobile drawer, topbar, quick search, theme, API status |
| `src/components/dashboard/` | Overview: KPIs, charts, recent records, ingestion runs, source health |
| `src/components/common/` | Loading / empty / error / limitation states, status badges |
| `src/components/explore/` | Explorers (config per type), global search, filters, pagination, result rows/cards |
| `src/components/detail/` | Record detail page: all stored fields with provenance badges, evidence, freshness, source, organisation, related |
| `src/lib/url-state.ts` | Search / filter / sort / page state in the URL (shareable, bookmarkable) |
| `src/components/entities/` | Organisation explorer and profile, relationship overview, read-only review queue |
| `src/components/operations/` | Ingestion monitor, source health, dead letter queue (read-only) |
| `src/components/settings.tsx` | Theme and API connection status |

Deployment requirements and the security review: `../docs/DEPLOYMENT.md`.

## Data honesty

All numbers come from the API. When the data cannot support a view (e.g. all records were collected on a single
day), the API returns a note and the UI shows it instead of drawing a synthetic trend. Records without validated
organisation tags are labelled "untagged".

## OneDrive note

`node_modules` (~450 packages) and `.next` are large and change often. If OneDrive sync slows down, mark the
`frontend\node_modules` and `frontend\.next` folders "Free up space"/exclude them, or move the project outside OneDrive.
