#!/usr/bin/env bash
set -euo pipefail

ENV="${ENV:-staging}"
PROJECT="${PROJECT_ID:?PROJECT_ID is required (via make converge / ../.env)}"
TIMEOUT_MIN="${CONVERGE_TIMEOUT_MIN:-40}"
STALL_SEC="${CONVERGE_STALL_SEC:-300}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TFVARS_GKE="$ROOT/environments/$ENV/kubernetes-engine/$ENV.tfvars"

tfvar() { sed -nE "s/^$1 *= *\"([^\"]+)\".*/\1/p" "$TFVARS_GKE"; }

CLUSTER="$(tfvar env)-$(tfvar cluster_name)"
LOCATION="$(tfvar zone)"
KUBECONFIG_TMP="$(mktemp)"
export KUBECONFIG="$KUBECONFIG_TMP"
trap 'rm -f "$KUBECONFIG_TMP"' EXIT
gcloud container clusters get-credentials "$CLUSTER" --location "$LOCATION" --project "$PROJECT" --dns-endpoint >/dev/null

QUERY='{range .items[*]}{.metadata.name}{"\t"}{.status.sync.status}{"\t"}{.status.health.status}{"\t"}{.status.operationState.phase}{"\t"}{.status.operationState.finishedAt}{"\t"}{.operation.initiatedBy}{"\n"}{end}'
nudged=" "
deadline=$(( $(date +%s) + TIMEOUT_MIN * 60 ))

echo ">> Waiting up to ${TIMEOUT_MIN}m for every controllers/configs Application to be Synced and Healthy"
while :; do
  rows="$(kubectl -n argocd get applications -l 'layer in (controllers,configs)' -o jsonpath="$QUERY" 2>/dev/null || true)"
  total="$(printf '%s' "$rows" | grep -c . || true)"
  pending="$(printf '%s\n' "$rows" | awk -F'\t' 'NF && !($2 == "Synced" && $3 == "Healthy")')"
  if [ "$total" -gt 0 ] && [ -z "$pending" ]; then
    echo ">> Converged: $total Applications Synced and Healthy"
    exit 0
  fi
  now="$(date +%s)"
  while IFS=$'\t' read -r name sync health phase finished running; do
    [ -n "$name" ] || continue
    [ "$sync" = "OutOfSync" ] && [ "$phase" = "Succeeded" ] && [ -z "$running" ] && [ -n "$finished" ] || continue
    case "$nudged" in *" $name "*) continue ;; esac
    age=$(( now - $(date -u -d "$finished" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "$finished" +%s) ))
    [ "$age" -ge "$STALL_SEC" ] || continue
    echo "   - $name: OutOfSync ${age}s after a successful sync, requesting a sync"
    kubectl -n argocd annotate application "$name" argocd.argoproj.io/refresh=hard --overwrite >/dev/null
    kubectl -n argocd patch application "$name" --type merge \
      -p '{"operation":{"initiatedBy":{"username":"converge"},"sync":{"syncOptions":["ServerSideApply=true","CreateNamespace=true"]}}}' >/dev/null
    nudged="$nudged$name "
  done <<< "$rows"
  if [ "$now" -ge "$deadline" ]; then
    echo ">> Not converged after ${TIMEOUT_MIN}m:"
    printf '%s\n' "$pending" | awk -F'\t' 'NF {printf "   %-34s %-10s %-12s %s\n", $1, $2, $3, $4}'
    exit 1
  fi
  echo "   $(printf '%s\n' "$pending" | grep -c . || true)/$total not ready yet"
  sleep 30
done
