#!/usr/bin/env bash
set -euo pipefail

ENV="${ENV:-staging}"
PROJECT="${PROJECT_ID:?PROJECT_ID is required (via make teardown / ../.env)}"
AUTO_APPROVE="${AUTO_APPROVE:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TFVARS_GKE="$ROOT/environments/$ENV/kubernetes-engine/$ENV.tfvars"

CONTROLLERS_ORDER=(mailpit kgateway kgateway-crds mcp-grafana mcp-victoriatraces-logsql mcp-victoriatraces mcp-victorialogs mcp-victoriametrics grafana k8s-events otel-collector victoria-metrics-k8s-stack plugin-barman-cloud cloudnative-pg victoria-metrics-operator external-secrets cert-manager)
DESTROY_ORDER=(argocd-bootstrap cloud-storage artifact-registry kubernetes-engine firewall cloud-nat vpc)

tfvar() { sed -nE "s/^$1 *= *\"([^\"]+)\".*/\1/p" "$TFVARS_GKE"; }

release_completed_hooks() {
  kubectl get jobs -A -o jsonpath='{range .items[?(@.metadata.annotations.argocd\.argoproj\.io/hook)]}{.metadata.namespace}{" "}{.metadata.name}{" x"}{.status.succeeded}{.status.failed}{" "}{.metadata.finalizers}{"\n"}{end}' |
    while read -r ns name done finalizers; do
      [ "$done" != x ] && [ "$finalizers" = '["argocd.argoproj.io/hook-finalizer"]' ] || continue
      echo "   - release the hook finalizer of finished job $ns/$name"
      kubectl -n "$ns" patch job "$name" --type json -p '[{"op":"remove","path":"/metadata/finalizers"}]' >/dev/null
    done
}

report_blockers() {
  for app in $(kubectl -n argocd get applications "$@" -o name 2>/dev/null); do
    echo "   ! $app is still deleting; its resources that still exist:"
    kubectl -n argocd get "$app" -o jsonpath='{range .status.resources[*]}{.kind}{"|"}{.group}{"|"}{.namespace}{"|"}{.name}{"\n"}{end}' |
      while IFS='|' read -r kind group ns name; do
        state="$(kubectl get "$kind${group:+.$group}" "$name" ${ns:+-n "$ns"} -o jsonpath='deleting since {.metadata.deletionTimestamp} finalizers {.metadata.finalizers}' 2>/dev/null)" || continue
        echo "     $kind ${ns:+$ns/}$name: $state"
      done
  done
}

delete_applications() {
  release_completed_hooks
  if ! kubectl -n argocd delete applications "$@" --wait=true --timeout=15m; then
    report_blockers "$@"
    exit 1
  fi
}

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
    delete_applications -l "layer=$layer"
  done
  for app in "${CONTROLLERS_ORDER[@]}"; do
    if kubectl -n argocd get application "$app" >/dev/null 2>&1; then
      echo "   - delete $app"
      delete_applications "$app"
    fi
  done
  delete_applications -l layer=controllers

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
