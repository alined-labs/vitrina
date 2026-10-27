# Dashboard

Next.js 15 seller dashboard for Vitrina (plan.md §1, §2), owned by MX1. It is a pnpm package outside the uv workspace and talks to the API only through the TypeScript client generated from `specs/001-vitrina-api/contracts/openapi.yaml`.

## Run

```sh
cd apps/dashboard
pnpm install --frozen-lockfile
cp .env.example .env.local   # optional: without Supabase settings it runs in local mode
pnpm dev                     # http://localhost:3000; start the API with `make api` from the repo root
```

The browser calls `/api/v1/*`, which `next.config.ts` proxies to `API_PROXY_TARGET` (default `http://localhost:8000`), so the API needs no CORS settings. `API_PROXY_TARGET` and the `NEXT_PUBLIC_*` settings are read when `pnpm dev` starts and at `pnpm build`: rebuild after changing them.

## Client

`scripts/gen-client` (or `make gen-client`) regenerates `src/lib/api/schema.d.ts` from the frozen contract; never edit that file. `scripts/gen-client --check` fails when it is stale. Use the typed client from `@/lib/api/client`:

```ts
const { data, error } = await api.GET("/products", { params: { query: { limit: 20 } } });
```

## Checks

`pnpm run check` runs the type check, ESLint and the client drift check; `pnpm build` must pass too. Dependency build scripts are off unless approved in `pnpm-workspace.yaml` (`allowBuilds`).

## Layout

- `src/app/(panel)/` signed-in shell: sidebar, header, one route per section of the loop; `[seccion]` is a placeholder that real pages override.
- `src/app/login/` Supabase email and password sign-in.
- `src/middleware.ts` session refresh and the redirect to `/login`.
- `src/lib/api/` the generated schema and the typed client; `src/lib/supabase/` browser and server clients.
