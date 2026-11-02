#!/usr/bin/env bash
# Stops a deploy when a container app is not running: `az containerapp update` and `create` return
# once the change is accepted, and a worker that exits at startup looks the same to them.
#
#   deploy/check-running.sh <app> <resource group>
#   DRY_RUN=1 deploy/check-running.sh ...    prints the az commands instead
#
# Running means the latest revision (not the latest ready one, which may be the old revision a
# failed update left in place) reads Running* with a replica, several checks in a row, while its
# containers do not restart. A state this script does not know never counts as running, so the
# worst a surprise in az's output can do is a false alarm, never a false success.
set -uo pipefail

app=$1 group=$2
attempts=${CHECK_ATTEMPTS:-30}
interval=${CHECK_INTERVAL_S:-10}
in_a_row=3
target=(--name "$app" --resource-group "$group")

latest_revision() {
  az containerapp show "${target[@]}" --query properties.latestRevisionName --output tsv
}

revision_field() {
  az containerapp revision show "${target[@]}" --revision "$1" --query "properties.$2" --output tsv
}

restart_count() {
  az containerapp replica list "${target[@]}" --revision "$1" \
    --query 'sum([].properties.containers[].restartCount)' --output tsv
}

if [[ ${DRY_RUN:-0} == 1 ]]; then
  az() { printf 'az'; printf ' %q' "$@"; printf '\n'; }
  latest_revision
  for field in provisioningState runningState replicas; do
    revision_field '<latest revision>' "$field"
  done
  restart_count '<latest revision>'
  exit 0
fi

not_running() {
  echo "$app is not running: $1" >&2
  echo "see: az containerapp logs show --name $app --resource-group $group --follow" >&2
  exit 1
}

streak=0 previous_restarts="" why="nothing checked yet"
for ((attempt = 1; attempt <= attempts; attempt++)); do
  ((attempt == 1)) || sleep "$interval"
  revision=$(latest_revision) || revision=""
  if [[ -z $revision ]]; then
    streak=0 why="az named no latest revision"
    continue
  fi
  provisioning=$(revision_field "$revision" provisioningState) || provisioning=""
  running=$(revision_field "$revision" runningState) || running=""
  replicas=$(revision_field "$revision" replicas) || replicas=""
  restarts=$(restart_count "$revision") || restarts=""
  restarts=${restarts%%.*}
  state="revision $revision: provisioning '$provisioning', running '$running', replicas '$replicas', restarts '$restarts'"
  if [[ $provisioning == *[Ff]ailed* || $running == *[Ff]ailed* ]]; then
    not_running "$state"
  fi
  if [[ $running != Running* || ! $replicas =~ ^[1-9][0-9]*$ || ! $restarts =~ ^[0-9]+$ ]]; then
    streak=0 why=$state
  elif [[ -n $previous_restarts ]] && ((restarts > previous_restarts)); then
    streak=0 why="its containers keep restarting; $state"
  else
    streak=$((streak + 1)) why=$state
  fi
  [[ $restarts =~ ^[0-9]+$ ]] && previous_restarts=$restarts
  if ((streak >= in_a_row)); then
    echo "$app is running: $state"
    exit 0
  fi
  echo "waiting for $app (attempt $attempt of $attempts): $state"
done
not_running "$why"
