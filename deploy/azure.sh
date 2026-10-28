#!/usr/bin/env bash
# Deploys the API and the worker to Azure Container Apps with the az CLI (deploy/README.md).
#
#   deploy/azure.sh [config]     config: deploy/azure.env (copy of deploy/azure.env.example)
#   DRY_RUN=1 deploy/azure.sh    prints every az command instead, secret values as ***
#
# Safe to run again: it creates what is missing and updates the rest to a new image. Secrets
# come from .env (or SECRETS_FILE) and never reach the terminal or the logs.
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
config=${1:-$root/deploy/azure.env}
secrets_file=${SECRETS_FILE:-$root/.env}
[[ -f $config ]] || { echo "missing $config: copy deploy/azure.env.example" >&2; exit 2; }
[[ -f $secrets_file ]] || { echo "missing $secrets_file: the production values" >&2; exit 2; }
# shellcheck source=/dev/null
source "$config"

dry=${DRY_RUN:-0}
tag=${IMAGE_TAG:-$(git -C "$root" rev-parse --short=12 HEAD)}
image="$AZURE_ACR.azurecr.io/vitrina:$tag"

# .env values that are secrets: each becomes a Container Apps secret (names are at most 20
# characters) and reaches the apps as secretref, never as plain text.
declare -A SECRET_OF=(
  [DATABASE_URL]=database-url
  [SUPABASE_JWT_SECRET]=supabase-jwt
  [SUPABASE_SERVICE_ROLE_KEY]=supabase-service
  [SERVICE_TOKEN]=service-token
  [META_API_KEY]=meta-api-key
  [WHATSAPP_APP_SECRET]=wa-app-secret
  [WHATSAPP_VERIFY_TOKEN]=wa-verify-token
  [WHATSAPP_ACCESS_TOKEN]=wa-access-token
  [BASETEN_API_KEY]=baseten-api-key
  [CONTACT_HASH_PEPPER]=contact-pepper
  [CONTACT_ENCRYPTION_KEY]=contact-key
)
# .env values that are configuration, passed as they are.
PLAIN_FROM_ENV=(
  SUPABASE_URL META_API_BASE_URL MODEL_AGENT MODEL_STRATEGIST MODEL_IMAGE MODEL_SEGMENT
  MODEL_TRANSCRIBE MODEL_EMBED WHATSAPP_PHONE_NUMBER_ID MODAL_GLIMMER_URL BASETEN_MODEL
  SPEND_LIMIT_USD
)

declare -A values=()
while IFS= read -r line || [[ -n $line ]]; do
  [[ $line =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]] || continue
  name=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
  if [[ $value =~ ^\"(.*)\"$ || $value =~ ^\'(.*)\'$ ]]; then
    value=${BASH_REMATCH[1]}
  fi
  values[$name]=$value
done < "$secrets_file"

secret_args=() env_args=() masked=(registry-password)
for name in $(printf '%s\n' "${!SECRET_OF[@]}" | sort); do
  [[ -n ${values[$name]-} ]] || continue
  secret_args+=("${SECRET_OF[$name]}=${values[$name]}")
  env_args+=("$name=secretref:${SECRET_OF[$name]}")
  masked+=("${SECRET_OF[$name]}")
done
for name in "${PLAIN_FROM_ENV[@]}"; do
  [[ -n ${values[$name]-} ]] && env_args+=("$name=${values[$name]}")
done
env_args+=("DB_SCHEMA=$DB_SCHEMA" "${SETTINGS[@]}")

step() { printf '\n== %s\n' "$*"; }

# One az call. A dry run prints it instead, with every secret value replaced by ***.
run() {
  if [[ $dry != 1 ]]; then
    az "$@"
    return
  fi
  local shown=() arg previous=""
  for arg in "$@"; do
    if [[ $previous == --registry-password ]]; then
      shown+=("***")
    elif [[ $arg == *=* && " ${masked[*]} " == *" ${arg%%=*} "* ]]; then
      shown+=("${arg%%=*}=***")
    else
      shown+=("$arg")
    fi
    previous=$arg
  done
  printf 'az'
  printf ' %q' "${shown[@]}"
  printf '\n'
}

# Whether an Azure resource exists; a dry run assumes a first deploy unless DRY_RUN_EXISTS=1.
exists() {
  if [[ $dry == 1 ]]; then
    [[ ${DRY_RUN_EXISTS:-0} == 1 ]]
    return
  fi
  az "$@" --output none >/dev/null 2>&1
}

step "resource group $AZURE_RESOURCE_GROUP in $AZURE_LOCATION"
run group create --name "$AZURE_RESOURCE_GROUP" --location "$AZURE_LOCATION" --output none

step "registry $AZURE_ACR"
exists acr show --name "$AZURE_ACR" --resource-group "$AZURE_RESOURCE_GROUP" ||
  run acr create --name "$AZURE_ACR" --resource-group "$AZURE_RESOURCE_GROUP" --sku Basic \
    --admin-enabled true --output none

step "image $image, built in Azure from this checkout"
run acr build --registry "$AZURE_ACR" --image "vitrina:$tag" "$root"

step "database migrations on schema $DB_SCHEMA"
if [[ $dry == 1 ]]; then
  printf 'DB_SCHEMA=%q uv run alembic upgrade head\n' "$DB_SCHEMA"
else
  # The environment wins over .env in the settings, so the migration uses these two values.
  (cd "$root" && DATABASE_URL=${values[DATABASE_URL]} DB_SCHEMA=$DB_SCHEMA uv run alembic upgrade head)
fi

step "environment $AZURE_ENVIRONMENT"
exists containerapp env show --name "$AZURE_ENVIRONMENT" --resource-group "$AZURE_RESOURCE_GROUP" ||
  run containerapp env create --name "$AZURE_ENVIRONMENT" --resource-group "$AZURE_RESOURCE_GROUP" \
    --location "$AZURE_LOCATION" --output none

if [[ $dry == 1 ]]; then
  registry_user="<registry user>" registry_password="<registry password>"
else
  registry_user=$(az acr credential show --name "$AZURE_ACR" --query username --output tsv)
  registry_password=$(az acr credential show --name "$AZURE_ACR" --query 'passwords[0].value' --output tsv)
fi

# deploy_app <name> <cpu> <memory> [create-only flags...]
deploy_app() {
  local app=$1 cpu=$2 memory=$3
  shift 3
  if exists containerapp show --name "$app" --resource-group "$AZURE_RESOURCE_GROUP"; then
    run containerapp secret set --name "$app" --resource-group "$AZURE_RESOURCE_GROUP" \
      --secrets "${secret_args[@]}" --output none
    run containerapp update --name "$app" --resource-group "$AZURE_RESOURCE_GROUP" \
      --image "$image" --cpu "$cpu" --memory "$memory" --min-replicas 1 --max-replicas 1 \
      --set-env-vars "${env_args[@]}" --output none
  else
    run containerapp create --name "$app" --resource-group "$AZURE_RESOURCE_GROUP" \
      --environment "$AZURE_ENVIRONMENT" --image "$image" \
      --registry-server "$AZURE_ACR.azurecr.io" --registry-username "$registry_user" \
      --registry-password "$registry_password" \
      --cpu "$cpu" --memory "$memory" --min-replicas 1 --max-replicas 1 "$@" \
      --secrets "${secret_args[@]}" --env-vars "${env_args[@]}" --output none
  fi
}

step "API $API_APP: public ingress on 8080"
deploy_app "$API_APP" "$API_CPU" "$API_MEMORY" --ingress external --target-port 8080

step "worker $WORKER_APP: no ingress, one replica takes the jobs"
deploy_app "$WORKER_APP" "$WORKER_CPU" "$WORKER_MEMORY" --command vitrina-worker

step "check"
if [[ $dry == 1 ]]; then
  echo "curl -fsS https://<api fqdn>/v1/health"
  exit 0
fi
fqdn=$(az containerapp show --name "$API_APP" --resource-group "$AZURE_RESOURCE_GROUP" \
  --query properties.configuration.ingress.fqdn --output tsv)
for attempt in $(seq 1 30); do
  if curl -fsS "https://$fqdn/v1/health"; then
    printf '\n\nAPI:              https://%s\nWhatsApp webhook: https://%s/v1/webhooks/whatsapp\n' \
      "$fqdn" "$fqdn"
    exit 0
  fi
  echo "waiting for the API (attempt $attempt)"
  sleep 10
done
echo "the API did not answer /v1/health; see: az containerapp logs show --name $API_APP" \
  "--resource-group $AZURE_RESOURCE_GROUP --follow" >&2
exit 1
