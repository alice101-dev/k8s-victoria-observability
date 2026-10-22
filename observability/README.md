# observability — VictoriaMetrics, VictoriaLogs, VictoriaTraces, Grafana

Deployed entirely by Argo CD (root Application `observability`, created by the Terraform
`argocd-bootstrap` workspace). Nothing here is installed with `helm install` or `kubectl apply`.

```text
observability/
├── argocd/                     # Helm chart rendering the child Applications (app-of-apps)
│   ├── values.yaml             # repoURL/targetRevision (injected by the root app) + pinned chart versions
│   └── templates/
│       ├── external-secrets.yaml      # wave -3  External Secrets Operator
│       ├── vm-operator.yaml           # wave -2  VictoriaMetrics Operator + CRDs
│       ├── observability-config.yaml  # wave -1  Grafana admin secret (ExternalSecret)
│       ├── vm-stack.yaml              # wave  0  victoria-metrics-k8s-stack
│       ├── mcp.yaml                   # wave  1  MCP servers for AI assistants (metrics, logs, traces, Grafana)
│       └── k8s-events.yaml            # wave  1  Kubernetes events → VictoriaLogs (OpenTelemetry Collector)
├── values/                     # Helm values, referenced through Argo CD multi-source ($values)
│   ├── external-secrets.yaml
│   ├── vm-operator.yaml
│   ├── vm-k8s-stack.yaml       # main configuration: VMCluster / VLCluster / VTCluster / Grafana
│   ├── mcp-{victoriametrics,victorialogs,victoriatraces,victoriatraces-logsql,grafana}.yaml   # MCP servers
│   └── otel-k8s-events.yaml    # events collector: k8s_events receiver → vlinsert (OTLP), one stream per namespace
├── k8s-events/                 # Kustomize: "Cluster — Events" dashboard, event and node alerts, collector scrape
└── manifests/                  # Kustomize: Grafana admin password generator + ExternalSecret
    └── mcp-grafana/            # Grafana service account token + caller token (ExternalSecrets), VMPodScrape
```

## Operator vs. plain Helm charts

| Layer | Tool | Reason |
|---|---|---|
| Operator + CRDs | `victoria-metrics-operator` chart, separate Application | Controlled CRD upgrades and removal order, as recommended by the k8s-stack docs |
| Data components (VMCluster, VLCluster, VLAgent, VTCluster, VMAgent, VMAlert, VMAlertmanager) | Operator CRs rendered by `victoria-metrics-k8s-stack` | Ordered storage rollouts, PDBs, config reloads, service discovery via VMServiceScrape/VMPodScrape, VMRule |
| Grafana, kube-state-metrics, node-exporter, dashboards, rules | k8s-stack subcharts and sync-job | Single source of versions; official dashboards and alerting rules |

The operator CRDs let application teams ship their own scrape configs, alerts and dashboards next to
their workloads (see `apps/k8s/data/monitoring.yaml` and `apps/k8s/services/templates/monitoring.yaml`).

## Topology

| Signal | Components | Replicas | Storage | Retention |
|---|---|---|---|---|
| Metrics | VMCluster (vminsert / vmselect / vmstorage) behind a vmauth load balancer (`requestsLoadBalancer`) | 2 / 2 / 2 (+2 vmauth) | 2 × 10Gi pd-balanced | 14d |
| Scraping | VMAgent StatefulSet, deduplicated at 30s on vmselect/vmstorage | 2 | 2 × 10Gi persistent queue (8GiB cap) | until delivered |
| Alerting | VMAlert → VMAlertmanager (gossip cluster); alert state persisted to VictoriaMetrics | 2 / 2 | 2 × 10Gi (silences, notification log) | – |
| Logs | VLCluster (vlinsert / vlselect / vlstorage) + VLAgent DaemonSet | 2 / 2 / 2 | 2 × 10Gi | 7d, capped at 8GiB per node |
| Traces | VTCluster (vtinsert / vtselect / vtstorage) | 2 / 2 / 2 | 2 × 10Gi | 7d, capped at 8GiB per node |
| UI | Grafana 13 | 1 | 10Gi for runtime state (dashboards and datasources provisioned from Git) | – |

Metrics, logs and traces are sharded across storage nodes without application-level replication;
durability comes from GCE persistent disks, which the VictoriaMetrics cluster docs describe as the most
cost-efficient option (`replicationFactor: N` also requires at least `2N-1` vmstorage nodes). When a
storage node restarts or its Spot VM is preempted, ingestion is rerouted to the remaining nodes and
queries return partial results until it is back. Scale out by increasing the storage `replicaCount`.

VMAgent runs in `statefulMode`, so its remote-write persistent queue lives on a PVC instead of an
emptyDir. If vminsert is unreachable, buffered samples (about 1.5 days at the current ~60KB/s per
replica) survive a pod being rescheduled, for example after a Spot preemption.

VMCluster protections (see the cluster docs, *Resource usage limits* and *Cardinality limiter*):

| Setting | Value | Purpose |
|---|---|---|
| `search.maxConcurrentRequests` / `search.maxMemoryPerQuery` (vmselect) | 8 / 96MB | A single wide query (dashboards, AI assistants via MCP) cannot OOM vmselect |
| `storage.maxHourlySeries` / `storage.maxDailySeries` (vmstorage) | 500k / 1.5M | Cardinality explosions are dropped and logged instead of exhausting memory |
| `storage.minFreeDiskSpaceBytes` (vmstorage) | 2GB | Keep ≥20% free disk; read-only mode before the disk fills up |
| `dedup.minScrapeInterval` (vmselect + vmstorage) | 30s | Deduplicate the two HA vmagent replicas |
| Slow query log (vmselect default) | >5s | Slow queries land in VictoriaLogs |

## Data flow

```text
Pod stdout (JSON) ──► VLAgent (DaemonSet) ──► vlinsert ──► vlstorage ◄── vlselect ◄── Grafana (VictoriaLogs plugin)
App /metrics ◄── VMAgent ×2 (VMServiceScrape/VMPodScrape) ──► vminsert ──► vmstorage ◄── vmselect ◄── Grafana
App OTLP/HTTP ──► vtinsert ──► vtstorage ◄── vtselect /select/jaeger ◄── Grafana (Jaeger datasource)
VMRule ──► VMAlert ×2 ──(metrics: vmselect, logs: vlselect via internal vmauth)──► Alertmanager ×2
```

Endpoints (namespace `monitoring`):

| Purpose | URL |
|---|---|
| OTLP traces | `http://vtinsert-vmks.monitoring.svc:10481/insert/opentelemetry/v1/traces` |
| Remote write | `http://vminsert-vmks.monitoring.svc:8480/insert/0/prometheus/api/v1/write` |
| PromQL / MetricsQL | `http://vmselect-vmks.monitoring.svc:8481/select/0/prometheus` |
| LogsQL | `http://vlselect-vmks.monitoring.svc:9471/select/logsql/query` |
| Jaeger API | `http://vtselect-vmks.monitoring.svc:10471/select/jaeger` |

## Grafana

- Datasources: **VictoriaMetrics** (default, Prometheus type), **VictoriaMetrics (DS)**
  (`victoriametrics-metrics-datasource` 0.26.1), **VictoriaLogs** (`victoriametrics-logs-datasource`
  0.33.1), **VictoriaTraces** (built-in Jaeger: trace search, trace view, service map), **VictoriaTraces
  (LogsQL)** (the VictoriaLogs plugin on vtselect: VictoriaTraces stores spans like VictoriaLogs stores logs,
  so LogsQL aggregates them, e.g. p95 and failures per endpoint, operation, SQL statement or attribute),
  **Alertmanager**.
- Correlation: `trace_id` in logs links to traces; spans link to logs (`trace_id:="<id>"`) and to the
  RED metrics of their service.
- Dashboards: VictoriaMetrics/Logs/Traces cluster, vmagent, vmalert, operator, Kubernetes views,
  node-exporter and Redis (the CloudNativePG dashboard is vendored with fixes in `apps/k8s/data/dashboards`),
  plus **Cluster — Events** (generated with the Shop dashboards, shipped by `k8s-events`). Grafana has two folders:
  **Shop** (application and data tier) and **Platform** (everything else); the Windows, AIX, macOS and
  multi-cluster dashboards of the mixins are disabled. Dashboard and rule URLs are **pinned to release tags**
  (the chart defaults point at `master`). Application dashboards are loaded from ConfigMaps labelled
  `grafana_dashboard: "1"` in any namespace.
- The admin password is generated by External Secrets and stays stable across Argo CD syncs.

```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo
```

## AI assistants (MCP)

Read-only [Model Context Protocol](https://modelcontextprotocol.io) servers let AI assistants
(Cursor, VS Code, JetBrains, …) query the stack in natural language — e.g. *"why did p95 latency of
order-service rise in the last 10 minutes? check its error logs and slowest traces"*.

| Service (`monitoring`) | Backend | Main tools |
|---|---|---|
| `vm-mcp` ([mcp-victoriametrics](https://github.com/VictoriaMetrics/mcp-victoriametrics)) | vmselect | `query`, `query_range`, `metrics`, `labels`, `series`, `rules`, `alerts`, `tsdb_status`, `explain_query` |
| `vl-mcp` ([mcp-victorialogs](https://github.com/VictoriaMetrics/mcp-victorialogs)) | vlselect | `query` (LogsQL), `hits`, `stats_query_range`, `facets`, `field_values`, `streams` |
| `vt-mcp` ([mcp-victoriatraces](https://github.com/VictoriaMetrics-Community/mcp-victoriatraces)) | vtselect | `services`, `traces`, `trace`, `dependencies` |
| `vt-logsql-mcp` ([mcp-victorialogs](https://github.com/VictoriaMetrics/mcp-victorialogs) on vtselect) | vtselect (LogsQL) | `stats_query`, `stats_query_range`, `query`, `field_names`, `field_values` over spans: latency, failures and time share per service, endpoint, operation or attribute |
| `grafana-mcp` ([mcp-grafana](https://github.com/grafana/mcp-grafana)) | Grafana API | `search_dashboards`, `get_dashboard_summary`, `run_panel_query`, `list_datasources`, `query_prometheus` (VictoriaMetrics), `query_loki_logs` / `query_loki_stats` (VictoriaLogs, LogsQL), `alerting_rules_read`, `generate_deeplink` |

The Victoria servers query the databases directly and embed their product documentation
(`documentation` tool). `grafana-mcp` works through Grafana: it sees dashboards and datasources the
way users do, and its links open in your port-forwarded Grafana. Its Tempo tools do not apply to the
Jaeger-based VictoriaTraces datasource, so traces go through `vt-mcp`.

All five run in Streamable HTTP mode (`/mcp`) behind ClusterIP services. The Victoria servers have
**no built-in authentication** and `grafana-mcp` requires a bearer token — keep them private and
connect through port-forwarding:

```bash
kubectl -n monitoring port-forward svc/vm-mcp 8090:8080 &
kubectl -n monitoring port-forward svc/vl-mcp 8091:8080 &
kubectl -n monitoring port-forward svc/vt-mcp 8092:8080 &
kubectl -n monitoring port-forward svc/vt-logsql-mcp 8095:8080 &
kubectl -n monitoring port-forward svc/grafana-mcp 8093:8000 &
# Bearer token required by grafana-mcp
kubectl -n monitoring get secret mcp-grafana-server-token -o jsonpath='{.data.token}' | base64 -d; echo
```

Then register the endpoints in your MCP client as Streamable HTTP servers (key names vary slightly
per client):

```json
{
  "mcpServers": {
    "victoriametrics": { "type": "http", "url": "http://localhost:8090/mcp" },
    "victorialogs":    { "type": "http", "url": "http://localhost:8091/mcp" },
    "victoriatraces":  { "type": "http", "url": "http://localhost:8092/mcp" },
    "victoriatraces-logsql": { "type": "http", "url": "http://localhost:8095/mcp" },
    "grafana": {
      "type": "http",
      "url": "http://localhost:8093/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

Credentials for `grafana-mcp`, all generated by External Secrets (nothing in Git):

| Secret | Generator | Purpose |
|---|---|---|
| `mcp-grafana-sa-token` | `Grafana` — service account `mcp-grafana`, role **Viewer** | Token the server uses against the Grafana API; rotated every 24h (old token revoked, 7-day TTL), re-read per request so no restart is needed |
| `mcp-grafana-server-token` | `Password`, created once | Bearer token MCP clients must send (`MCP_GRAFANA_SERVER_TOKEN`) |

Write tools are disabled (`--disable-write`) and only the tool categories relevant to this stack are
enabled (`--enabled-tools` in `values/mcp-grafana.yaml`). DNS-rebinding protection only accepts
`Host: localhost:8093`, so keep that local port or change `--allowed-hosts`. To let an assistant create or edit
dashboards, set `disableWrite: false` and the service account role to `Editor`.

A fifth server runs next to the database it reads: `postgres-mcp` in the `shop` namespace
([MCP Toolbox for Databases](https://github.com/googleapis/mcp-toolbox), `apps/k8s/data/postgres-mcp.yaml`).
It answers what the metrics sample too coarsely: which sessions are running or waiting right now, who holds
the locks, which transactions stay open (`list_active_queries`, `list_locks`, `long_running_transactions`,
`list_query_stats`, `list_table_stats`, `database_overview`, `replication_stats` and one read-only statement at
a time with `execute_sql`). It logs in as `mcp_readonly`, a CloudNativePG managed role that is only in
`pg_monitor`: statistics, sessions and settings, no table data, no cancelling or ending sessions. Every session
is read-only with a 10s statement timeout, and a NetworkPolicy admits no pod at all, so port-forwarding is
the only way in:

```bash
kubectl -n shop port-forward svc/postgres-mcp 8094:5000 &
# MCP client: { "postgres": { "type": "http", "url": "http://localhost:8094/mcp" } }
```

Query results (including log lines) are sent to the model provider of your AI client — keep that in
mind for sensitive data. Disable tools with `mcp.disable.tools` (Victoria servers) or
`--enabled-tools` (grafana-mcp) in the values files.

## GKE-specific settings

- Scraping of `kube-controller-manager`, `kube-scheduler` and `etcd` is disabled (managed control plane).
- `kube-proxy` scraping is disabled (Dataplane V2 / eBPF); `kube-dns` is scraped instead of CoreDNS.
- Workload logs and metrics are not sent to Cloud Logging / Managed Prometheus (configured in Terraform).
- PVCs use the `standard-rwo` StorageClass and `persistentVolumeClaimRetentionPolicy: Delete`, so
  persistent disks are removed together with the custom resources.

## GitOps notes

- The VictoriaMetrics Operator **admission webhook is disabled**. Its certificate is generated by
  Helm `genCA` at render time; because Argo CD re-renders charts without `lookup`, the CA bundle and
  the serving certificate can drift apart and every CR gets rejected. Validation still happens through
  the CRD OpenAPI schema and the operator's reconcile status. To enable the webhook, install
  cert-manager and set `admissionWebhooks.certManager.enabled: true`.
- On the first bootstrap, the `apps` root may retry a few times until the CRDs from this stack
  (External Secrets, VictoriaMetrics Operator) exist; this is handled by the sync retry policy.
- The k8s-stack sync-job (dashboards and rules) is a Helm hook, which Argo CD runs as a **PostSync**
  hook on every `vm-stack` sync. It needs internet egress (Cloud NAT) to `raw.githubusercontent.com`.

## Upgrading

1. Bump the chart versions in `argocd/values.yaml` and/or `clusterVersion` / `global.versions` and the
   pinned dashboard and rule URLs in `values/vm-k8s-stack.yaml`.
2. Review the changelog: <https://docs.victoriametrics.com/helm/victoria-metrics-k8s-stack/changelog/>.
3. Open a PR, merge, and let Argo CD sync. Operator CRDs are upgraded as well (rendered as templates
   and applied with server-side apply).
