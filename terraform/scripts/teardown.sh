#!/usr/bin/env bash
set -euo pipefail

ENV="${ENV:-staging}"
PROJECT="${PROJECT_ID:?PROJECT_ID is required (via make teardown / ../.env)}"
AUTO_APPROVE="${AUTO_APPROVE:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TFVARS_GKE="$ROOT/environments/$ENV/kubernetes-engine/$ENV.tfvars"

ADDONS_ORDER=(mcp-grafana mcp-victoriatraces-logsql mcp-victoriatraces mcp-victorialogs mcp-victoriametrics grafana k8s-events otel-collector victoria-metrics-k8s-stack plugin-barman-cloud cloudnative-pg victoria-metrics-operator external-secrets cert-manager)
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

  if kubectl -n argocd get application root >/dev/null 2>&1; then
    kubectl -n argocd patch application root --type json -p '[{"op":"remove","path":"/metadata/finalizers"}]' >/dev/null 2>&1 || true
    kubectl -n argocd delete application root --wait=true
  fi
  kubectl -n argocd delete applicationsets --all --cascade=orphan --wait=true
  for layer in apps configs; do
    echo "   - delete layer $layer"
    kubectl -n argocd delete applications -l layer=$layer --wait=true --timeout=15m
  done
  for app in "${ADDONS_ORDER[@]}"; do
    if kubectl -n argocd get application "$app" >/dev/null 2>&1; then
      echo "   - delete $app"
      kubectl -n argocd delete application "$app" --wait=true --timeout=15m
    fi
  done
  kubectl -n argocd delete applications -l layer=addons --wait=true --timeout=15m

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
