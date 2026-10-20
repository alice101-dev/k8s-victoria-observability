# k8s-victoria-observability

Production-grade observability for Go microservices on GKE — **metrics, logs and traces** with
VictoriaMetrics, VictoriaLogs, VictoriaTraces and Grafana. Everything inside the cluster is deployed
with **GitOps (Argo CD)** on infrastructure provisioned by **Terraform**.

```text
k8s-victoria-observability/
├── observability/   # VictoriaMetrics / VictoriaLogs / VictoriaTraces + Grafana (Argo CD app-of-apps, Helm values)
├── apps/            # 6 Go microservices + Redis + PostgreSQL 18 (source, Dockerfile, Helm chart, data tier)
└── terraform/       # VPC, Cloud NAT, firewall, GKE (Spot), Artifact Registry, Argo CD bootstrap
```

Details: [`observability/`](observability/README.md) · [`apps/`](apps/README.md) · [`terraform/`](terraform/README.md)

## Architecture

```text
                       ┌─────────────────────── GKE (zonal, private nodes, Spot node pool) ───────────────────────┐
Git ──► Argo CD ──►    │  ns: shop                                  ns: monitoring                                │
        app-of-apps    │  api-gateway ─► order ─► user / product /  ┌─ VLAgent (DaemonSet) ─► vlinsert ─► vlstorage  │
                       │                 payment / notification     │                                    ▲ vlselect   │
                       │  Redis 8 · PostgreSQL 18 (CloudNativePG)   ├─ VMAgent ×2 ─► vmauth ─► vminsert ─► vmstorage  │
                       │      │ OTLP/HTTP spans                     │                                    ▲ vmselect   │
                       │      └────────────────────────────────────►├─ vtinsert ─► vtstorage ◄─ vtselect (Jaeger API)  │
                       │                                            ├─ VMAlert ×2 (MetricsQL + LogsQL) ─► Alertmanager │
                       │                                            └─ Grafana (VM, VictoriaLogs, Jaeger datasources)  │
                       └───────────────────────────────────────────────────────────────────────────────────────────┘
```

## Stack

| Area | Choice | Why |
|---|---|---|
| Deployment model | `victoria-metrics-k8s-stack` Helm chart rendering **VictoriaMetrics Operator** CRs; operator installed as a separate app | Official production pattern; the operator handles ordered storage rollouts, config reloads and self-service scrape/rule CRDs |
| Topology | **Cluster mode** for VMCluster, VLCluster and VTCluster (insert/select/storage ×2, PDBs, topology spread) | Each tier scales out independently; durability comes from GCE persistent disks (no application-level replication); vmauth load-balances vminsert/vmselect |
| Log collection | VLAgent `k8sCollector` DaemonSet | Native VictoriaLogs agent, automatic JSON parsing, checkpoints |
| Trace ingestion | OpenTelemetry SDK → `vtinsert` (OTLP/HTTP) | No extra hop; the SDK batches and retries |
| Grafana datasources | Prometheus + `victoriametrics-metrics-datasource`, `victoriametrics-logs-datasource`, built-in **Jaeger** for traces | Jaeger API is the documented VictoriaTraces integration |
| GitOps | Argo CD app-of-apps with sync waves and an Application health check | Operators and CRDs become healthy before the resources that depend on them |
| Secrets | External Secrets password generator + CloudNativePG-generated credentials | No secret values in Git |
| PostgreSQL 18 | CloudNativePG (`18.6-standard-trixie`) | Production-grade operator with a built-in metrics exporter |
| Cluster | Zonal GKE Standard, private nodes, Dataplane V2, Workload Identity, Spot nodes | Low cost; Cloud Logging/Managed Prometheus disabled for workloads (no double ingest) |
| AI assistants | Read-only MCP servers for metrics, logs, traces and Grafana | Ask questions in natural language from any MCP client (Cursor, VS Code, …) ([details](observability/README.md#ai-assistants-mcp)) |

## Correlating signals in Grafana

- **Trace → logs:** "Logs for this span" runs `trace_id:="<id>"` against VictoriaLogs.
- **Log → trace:** the `trace_id` field of every application log links to the trace (derived field).
- **Trace → metrics:** RED metrics of the span's service (`service.name` ↔ `job`).
- **Dashboard "Shop — Microservices Overview":** RED per service and route, service-to-service calls,
  business metrics, error/warning log volume and the latest error logs.
- **Alerting:** metric rules (error rate, p95 latency, payment declines) and **log-based** rules
  (LogsQL) evaluated by vmalert.

## Getting started

Prerequisites: `gcloud`, Terraform 1.15.x (`tfenv`), `kubectl`, Docker with `buildx`, `make`, and a
fork/clone of this repository pushed to GitHub.

```bash
# 0. Local configuration (git-ignored)
cp .env.example .env                       # set PROJECT_ID=<your GCP project>
gcloud config set project <PROJECT_ID>
gcloud auth application-default login

# 1. Infrastructure + GitOps bootstrap (repo URL is read from `git remote origin`)
#    Private repo: export TF_VAR_gitops_repo_password=<read-only GitHub token>
make -C terraform init
make -C terraform apply-all

# 2. Cluster credentials (DNS endpoint, IAM-gated — no bastion/VPN needed)
gcloud container clusters get-credentials staging-observability \
  --location asia-southeast2-a --project "$(gcloud config get-value project)" --dns-endpoint

# 3. Build and push the application images, then commit the new tag (Argo CD rolls it out)
make -C apps auth
make -C apps release
git add apps/k8s/services/values.yaml && git commit -m "release shop" && git push

# 4. Watch the rollout
kubectl -n argocd get applications
```

Sync order (enforced by sync waves):

```text
observability:  external-secrets (-3) → vm-operator (-2) → observability-config (-1) → vm-stack (0) → MCP servers (1)
apps:           cloudnative-pg (-2)   → shop-data (-1)   → shop-services (0)
```

## Accessing the UIs

No load balancers or public endpoints are created; use port-forwarding.

```bash
# Grafana → http://localhost:3000 (user: admin)
kubectl -n monitoring port-forward svc/grafana 3000:80
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo

# Argo CD → http://localhost:8080 (user: admin)
kubectl -n argocd port-forward svc/argocd-server 8080:80
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

## Teardown

```bash
make -C terraform teardown
```

The script deletes the Argo CD applications in reverse sync-wave order, waits until every PVC is
gone (so no orphaned persistent disks keep billing), then destroys the Terraform workspaces.

## Staging defaults vs. production

This setup runs as a low-cost staging environment; the observability configuration itself follows
production practices. To harden it for production:

| Staging default | Production |
|---|---|
| Metrics, logs and traces sharded across storage nodes without replication (durability from persistent disks) | Add storage nodes for capacity; `replicationFactor: 2` with at least 3 vmstorage nodes, or `VMDistributed`/`VLDistributed` (independent clusters) for full data HA |
| All nodes are Spot VMs | Add a small on-demand node pool for stateful workloads (vm/vl/vtstorage, PostgreSQL) |
| Zonal cluster | `regional_cluster = true` |
| 100% trace sampling | Lower `otel.samplerArg` in `apps/k8s/services/values.yaml` (e.g. `0.1`) or add tail sampling with an OpenTelemetry Collector |
| VictoriaMetrics Operator admission webhook disabled | Install cert-manager and set `admissionWebhooks.certManager.enabled: true` |
| Alertmanager `blackhole` receiver | Configure Slack/PagerDuty receivers with credentials from an ExternalSecret |
| Single PostgreSQL instance, no backups | `instances: 3` plus barman-cloud backups to GCS |
| Single Grafana replica with SQLite on a PVC | External PostgreSQL database for Grafana and 2+ replicas |

## Secrets and configuration

- No secrets, project IDs or repository URLs are committed. `PROJECT_ID` lives in a git-ignored
  `.env`; the image registry (which contains the project ID) is computed by Terraform and injected
  into Argo CD, so Git only stores the image tag.
- Terraform state is local per workspace and git-ignored.
- `make check-project` refuses to plan, apply or tear down when the active `gcloud` project differs
  from `PROJECT_ID`.

## License

[MIT](LICENSE)
