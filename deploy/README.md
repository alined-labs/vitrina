# Deploying the back end to Azure Container Apps

The API and the worker run as two container apps from the one image in the repository's
`Dockerfile`. The database and file storage stay on Supabase. The dashboard is deployed
separately and reaches the API through its own `/api/v1/*` proxy, so browsers stay
same-origin.

```text
browser ──► dashboard (vitrina.alinedlabs.co) ──/api/v1/*──► vitrina-api  (external ingress, :8080)
WhatsApp Cloud API ──────────────────────────────webhook──► vitrina-api
                                        jobs table (Supabase) ◄──► vitrina-worker (no ingress)
```

## What you need

- The Azure CLI, signed in: `az login`, then `az account set --subscription <id>`.
- `uv` and this repository, checked out at the commit to deploy.
- A `.env` at the repository root with the production values: the names in `.env.example`,
  with `DATABASE_URL` (Supabase session pooler, port 5432), `SUPABASE_URL`,
  `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET` (only for a project still on the legacy
  JWT secret), `META_API_KEY`, the `MODEL_*` IDs, the four `WHATSAPP_*` values, a fresh
  `SERVICE_TOKEN`, and the production `CONTACT_HASH_PEPPER` and `CONTACT_ENCRYPTION_KEY`.
  Set the contact pepper and key once and keep them: contacts stored with one pair cannot be
  read with another.
- `deploy/azure.env`, copied from `deploy/azure.env.example`: resource names, region, sizes,
  the production schema and the settings both apps run with (`REPLAY=false`,
  `WHATSAPP_MOCK=false`, `STORAGE_BACKEND=supabase`, the public addresses).

## Deploy

```sh
DRY_RUN=1 deploy/azure.sh   # every az command it would run, secret values as ***
deploy/azure.sh             # the real thing; run it again to deploy a new commit
```

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
read from `.env` and passed straight to `az`, never echoed.

## After the first deploy

- Point the dashboard's API proxy at the printed address.
- In the Meta app, set the WhatsApp webhook to `https://<api address>/v1/webhooks/whatsapp`
  with the same `WHATSAPP_VERIFY_TOKEN`.
- `GET /v1/health/providers` with a seller's token shows every dependency. In production each
  row should read `ok`, not `mocked`.

## Operating

```sh
az containerapp logs show --name vitrina-api --resource-group vitrina --follow
az containerapp revision list --name vitrina-api --resource-group vitrina --output table
az containerapp revision activate --revision <previous> --name vitrina-api --resource-group vitrina
```

To roll back, activate the previous revision, or run `IMAGE_TAG=<older commit> deploy/azure.sh`
after `git checkout <older commit>`.

## Notes

- **Sizes.** The API gets the smallest Consumption size (0.25 vCPU, 0.5 GiB). The worker
  starts at 0.5 vCPU and 1 GiB because creative jobs hold images in memory. Each pair must be
  one of the allowed combinations (0.25/0.5Gi, 0.5/1.0Gi, 0.75/1.5Gi, …).
- **Client addresses.** uvicorn trusts the platform ingress's forwarded address
  (`FORWARDED_ALLOW_IPS=*`), so per-visitor limits such as the public showcase's see the
  visitor, not the proxy. Only the ingress can reach the container, which makes `*` safe there.
- **CORS.** Browsers call through the dashboard's proxy, which needs no CORS. `CORS_ORIGINS`
  allows a direct call from the dashboard's origin as well.
- **Registry access.** The registry's admin user pulls the image (`--registry-username`,
  `--registry-password`). A managed identity with the `AcrPull` role is the stricter
  alternative if the subscription allows role assignments.
