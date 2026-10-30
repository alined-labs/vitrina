# Deploying Vitrina

Two parts, in this order:

1. **Back end** (API and worker) on Azure Container Apps, with `deploy/azure.sh`. It prints the API address.
2. **Front end** (the dashboard) on Cloudflare Workers at `https://vitrina.alinedlabs.co`, with the API address built into its `/api/v1/*` proxy. Build and deploy it again whenever the API address changes.

## Back end: Azure Container Apps
The API and the worker run as two container apps from the one image in the repository's
`Dockerfile`. The database and file storage stay on Supabase. The dashboard is deployed
separately and reaches the API through its own `/api/v1/*` proxy, so browsers stay
same-origin.

```text
browser ──► dashboard (vitrina.alinedlabs.co) ──/api/v1/*──► vitrina-api  (external ingress, :8080)
WhatsApp Cloud API ──────────────────────────────webhook──► vitrina-api
                                        jobs table (Supabase) ◄──► vitrina-worker (no ingress)
```

### What you need

- The Azure CLI, signed in: `az login`, then `az account set --subscription <id>`.
- `uv` and this repository, checked out at the commit to deploy.
- A `.env` at the repository root with the production values: the names in `.env.example`,
  with `DATABASE_URL` (Supabase session pooler, port 5432), `SUPABASE_URL`,
  `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` (only for a project still on the legacy
  JWT secret), `META_API_KEY`, the `MODEL_*` IDs, the four `WHATSAPP_*` values, a fresh
  `SERVICE_TOKEN`, and the production `CONTACT_HASH_PEPPER` and `CONTACT_ENCRYPTION_KEY`.
  Set the contact pepper and key once and keep them: contacts stored with one pair cannot be
  read with another. Add a random `VISITOR_PROXY_SECRET` (`openssl rand -hex 32`), which the
  dashboard Worker gets too; without it the API never trusts the dashboard's visitor header.
- `deploy/azure.env`, copied from `deploy/azure.env.example`: resource names, region, sizes,
  the production schema and the settings both apps run with (`REPLAY=false`,
  `WHATSAPP_MOCK=false`, `STORAGE_BACKEND=supabase`, the public addresses).

### Deploy

```sh
DRY_RUN=1 deploy/azure.sh   # every az command it would run, secret values as ***
deploy/azure.sh             # the real thing; run it again to deploy a new commit
```

Before any `az` command, the script checks that `.env` has every value production needs (the
list above; the WhatsApp values while `WHATSAPP_MOCK=false`, the service key while
`STORAGE_BACKEND=supabase`). If one is missing it stops and names it; a dry run names it and
goes on. Only names are printed, never values.

The script is idempotent. In order, it:

1. creates the resource group and a Basic Azure Container Registry, if they are missing;
2. builds the image in Azure (`az acr build`), tagged with the commit, so nothing is built
   locally;
3. migrates the production schema (`DB_SCHEMA`) to head with Alembic;
4. creates the Container Apps environment (Consumption) if it is missing;
5. creates the two apps or updates them to the new image, settings and secrets:
   - `vitrina-api`: external ingress on port 8080, one replica, so the WhatsApp webhook never
     waits for a cold start;
   - `vitrina-worker`: no ingress, one replica, runs `vitrina-worker`;
6. waits for `GET /v1/health` and prints the API address and the WhatsApp webhook URL.

Every secret becomes a Container Apps secret and reaches the apps as a `secretref`. Values are
read from `.env` and passed straight to `az`, never echoed. `VISITOR_PROXY_SECRET` goes to the
API only; the worker never needs it.

### After the first deploy

- Point the dashboard's API proxy at the printed address.
- Create the shop's seller, with the WhatsApp business number as `whatsapp_number`, **before**
  the webhook is set. Inbound messages go to the seller with that number, `POST /v1/sellers` is
  open, and the number is not verified: whoever registers it first receives its chats.
  `scripts/seed-demo.py` creates it with the demo shop's products, facts and photo (the shop in
  `evals/fixtures/sellers/mochilas_ana.json`). First add the owner's sign-in in Supabase
  (Authentication, Users), then run, from the repository root:

  ```sh
  uv run --env-file apps/dashboard/.env.production.local scripts/seed-demo.py \
    --api https://<api address>/v1 --whatsapp-number <business number, E.164> \
    --sign-in <owner email> --photo mochila-azul=<path to the product photo>
  ```

  The password is asked at the prompt and only the public anon key is used. A second run
  changes nothing; a 409 means the number or the account already has a seller.
- In the Meta app, set the WhatsApp webhook to `https://<api address>/v1/webhooks/whatsapp`
  with the same `WHATSAPP_VERIFY_TOKEN`.
- `GET /v1/health/providers` with a seller's token shows every dependency. In production each
  row should read `ok`, not `mocked`.
- Check the production settings: `WHATSAPP_MOCK=false` (the mock phone's routes have no sign-in)
  and a private storage bucket (photos are served only through signed links).

### Operating

```sh
az containerapp logs show --name vitrina-api --resource-group vitrina --follow
az containerapp revision list --name vitrina-api --resource-group vitrina --output table
az containerapp revision activate --revision <previous> --name vitrina-api --resource-group vitrina
```

To roll back, activate the previous revision, or run `IMAGE_TAG=<older commit> deploy/azure.sh`
after `git checkout <older commit>`.

### Notes

- **Sizes.** The API gets the smallest Consumption size (0.25 vCPU, 0.5 GiB). The worker
  starts at 0.5 vCPU and 1 GiB because creative jobs hold images in memory. Each pair must be
  one of the allowed combinations (0.25/0.5Gi, 0.5/1.0Gi, 0.75/1.5Gi, …).
- **Client addresses.** The ingress appends the caller's address to any `X-Forwarded-For` the
  caller sent, so only the rightmost entry comes from Azure. uvicorn trusts loopback and
  private peers (`FORWARDED_ALLOW_IPS`) and takes the rightmost entry they did not add, so
  per-visitor limits such as the public showcase's see the caller, not the proxy. Never set
  `*`: uvicorn then reads the leftmost entry, which the caller wrote. If the ingress ever
  connects from outside those ranges, uvicorn ignores the header and the ingress counts as
  the client: the limit gets coarser, never forgeable. After a deploy, the API's log should show
  your own address for your `curl …/v1/health`.
- **CORS.** Browsers call through the dashboard's proxy, which needs no CORS. `CORS_ORIGINS`
  allows a direct call from the dashboard's origin as well.
- **Registry access.** The registry's admin user pulls the image (`--registry-username`,
  `--registry-password`). A managed identity with the `AcrPull` role is the stricter
  alternative if the subscription allows role assignments.

## Front end: Cloudflare Workers

The dashboard (`apps/dashboard`, Next.js 16) runs as one Worker built with the OpenNext adapter (`@opennextjs/cloudflare`). `apps/dashboard/wrangler.jsonc` names it `vitrina-dashboard` and attaches the custom domain `vitrina.alinedlabs.co`; Wrangler creates the DNS record and the certificate on deploy.

### What you need

- Node 20.9 or later and pnpm, and this repository at the commit to deploy.
- Cloudflare access to the account that holds the `alinedlabs.co` zone: `pnpm exec wrangler login` in `apps/dashboard`, or a `CLOUDFLARE_API_TOKEN` with Workers Scripts and Workers Routes edit rights on that zone.
- `apps/dashboard/.env.production.local` (git-ignored). The build reads these values and fixes them into the bundle:

  ```sh
  API_PROXY_TARGET=https://<api address printed by deploy/azure.sh>
  NEXT_PUBLIC_SUPABASE_URL=<the Supabase project URL>
  NEXT_PUBLIC_SUPABASE_ANON_KEY=<the project's anon (publishable) key>
  ```

  Sign-in is email and password, so Supabase needs no redirect URL for the domain.

### Deploy

```sh
cd apps/dashboard
pnpm install --frozen-lockfile
pnpm run cf:preview   # optional: the same Worker locally on http://localhost:8787
pnpm run cf:deploy    # next build, the OpenNext bundle, then wrangler deploy
pnpm exec wrangler secret put VISITOR_PROXY_SECRET   # once: paste the API's value
```

The secret reaches the running Worker at once, with no new deploy. Until it is set, the proxy sends no visitor address and the API counts every visitor at the proxy's address.

### Check

- `https://vitrina.alinedlabs.co` sends a signed-out visitor to `/login`; signing in shows the panel.
- `https://vitrina.alinedlabs.co/foto` opens without signing in (the public showcase).
- `curl -fsS https://vitrina.alinedlabs.co/api/v1/health` answers through the proxy.

### Operating

```sh
pnpm exec wrangler deployments list   # recent versions
pnpm exec wrangler rollback           # back to the previous version
pnpm exec wrangler tail               # live logs
```

Next.js ships security fixes in monthly releases announced in advance on its blog
(nextjs.org/blog, tag "security"), plus out-of-band ones for critical issues. When one comes out,
look up `next` in the GitHub Advisory Database for the version in `apps/dashboard/package.json`.
For a critical or high advisory, raise `next` and
`eslint-config-next` to the patched 16.4 release, add it to `minimumReleaseAgeExclude` in
`pnpm-workspace.yaml` (pnpm otherwise holds back a release that new), and deploy the
dashboard within 48 hours.

### Notes

- **Visitor address.** The proxy does not forward `X-Forwarded-For` or `CF-Connecting-IP` to the API, so the middleware sends the visitor's address as `X-Vitrina-Visitor`, together with `X-Vitrina-Proxy-Secret`, the `VISITOR_PROXY_SECRET` it shares with the API. Both always overwrite what the browser sent. The API trusts the address only when the secret matches, so the public showcase's five-an-hour limit counts each visitor, and a call made straight to the API is limited by its own address.
- **Versions.** Next.js 16.4.3 with `@opennextjs/cloudflare` 1.20.14 and Wrangler 4.154.0. Earlier adapters refused or broke Next.js 16.4, and 1.20.14 fixes truncated responses under concurrent requests. The dashboard keeps `middleware.ts`: Next.js 16 renames it to `proxy.ts`, which runs only on Node.js, and the adapter does not support Node middleware yet. CI builds the Worker bundle on every pull request (`pnpm run cf:build`).
- **Outbound fetches.** `global_fetch_strictly_public` keeps the Worker from fetching private addresses; the API address must be public, as Container Apps' external ingress is.
