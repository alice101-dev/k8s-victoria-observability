#!/usr/bin/env bash
# =============================================================================
# Safe teardown of the test environment:
#   1. Delete the GitOps applications in reverse sync-wave order, so PVCs and their persistent
#      disks are removed. Destroying the cluster directly leaves PVC disks behind (still billed).
#   2. terraform destroy each workspace in reverse apply order. "services" is kept
#      (APIs stay enabled; disable_on_destroy = false).
#   3. List leftover billable resources.
# =============================================================================
set -euo pipefail

ENV="${ENV:-staging}"
PROJECT="${PROJECT_ID:?PROJECT_ID is required (via make teardown / ../.env)}"
AUTO_APPROVE="${AUTO_APPROVE:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TFVARS_GKE="$ROOT/environments/$ENV/kubernetes-engine/$ENV.tfvars"

# Child Applications in reverse sync-wave order: workloads first, operators and CRDs last
# (operators must still run to process their custom resource finalizers).
CHILD_APPS=(shop-services shop-data plugin-barman-cloud cloudnative-pg cert-manager mcp-grafana mcp-victoriatraces-logsql mcp-victoriatraces mcp-victorialogs mcp-victoriametrics vm-stack observability-config vm-operator external-secrets)
ROOT_APPS=(apps observability)
# Kept: services (enabled APIs) and secret-manager (secret values added by hand, a few cents a month).
DESTROY_ORDER=(argocd-bootstrap cloud-storage artifact-registry kubernetes-engine firewall cloud-nat vpc)

tfvar() { sed -nE "s/^$1 *= *\"([^\"]+)\".*/\1/p" "$TFVARS_GKE"; }

[ "$(gcloud config get-value project 2>/dev/null)" = "$PROJECT" ] || { echo "active gcloud project is not $PROJECT"; exit 1; }

CLUSTER="$(tfvar env)-$(tfvar cluster_name)"
LOCATION="$(tfvar zone)"

echo ">> [1/3] Delete GitOps workloads in cluster $CLUSTER"
if gcloud container clusters describe "$CLUSTER" --location "$LOCATION" --project "$PROJECT" >/dev/null 2>&1; then
  KUBECONFIG_TMP="$(mktemp)"
  export KUBECONFIG="$KUBECONFIG_TMP"
  trap 'rm -f "$KUBECONFIG_TMP"' EXIT
  gcloud container clusters get-credentials "$CLUSTER" --location "$LOCATION" --project "$PROJECT" --dns-endpoint >/dev/null

  # Remove root apps without cascading (finalizer dropped) so selfHeal cannot recreate children.
  for app in "${ROOT_APPS[@]}"; do
    if kubectl -n argocd get application "$app" >/dev/null 2>&1; then
      kubectl -n argocd patch application "$app" --type json -p '[{"op":"remove","path":"/metadata/finalizers"}]' >/dev/null 2>&1 || true
      kubectl -n argocd delete application "$app" --wait=true
    fi
  done
  # Delete child apps one by one with cascade (the Argo CD finalizer deletes their resources).
  for app in "${CHILD_APPS[@]}"; do
    if kubectl -n argocd get application "$app" >/dev/null 2>&1; then
      echo "   - delete $app"
      kubectl -n argocd delete application "$app" --wait=true --timeout=15m
    fi
  done

  echo ">> Waiting for all PVCs to be deleted (persistent disks released)..."
  for _ in $(seq 1 60); do
    left="$(kubectl get pvc -A --no-headers 2>/dev/null | wc -l | tr -d ' ')"
    [ "$left" = "0" ] && break
    echo "   PVCs left: $left"; sleep 10
  done
  kubectl get pvc -A --no-headers 2>/dev/null | grep . && echo "WARNING: PVCs remain — check before continuing." || echo "   PVCs gone."
else
  echo "   Cluster not found — skipping."
fi

echo ">> [2/3] terraform destroy"
for ws in "${DESTROY_ORDER[@]}"; do
  dir="$ROOT/environments/$ENV/$ws"
  if [ -f "$dir/terraform.tfstate" ] && [ "$(terraform -chdir="$dir" state list 2>/dev/null | wc -l | tr -d ' ')" != "0" ]; then
    echo "================ destroy $ws ================"
    terraform -chdir="$dir" destroy -var-file="$ENV.tfvars" ${AUTO_APPROVE:+-auto-approve}
  else
    echo "   $ws: empty state — skipping"
  fi
done

echo ">> [3/3] Leftover billable resources"
gcloud compute disks list --project "$PROJECT" --filter="name~^pvc-" --format="table(name,zone.basename(),sizeGb)" || true
gcloud compute addresses list --project "$PROJECT" --format="table(name,region.basename(),status)" || true
echo "Done. Delete any leftover pvc-* disk with: gcloud compute disks delete <name> --zone <zone>"
