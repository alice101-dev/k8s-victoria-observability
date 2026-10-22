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
│   ├── vm-k8s-stack.yaml       # main configuration: VMSingle / VLSingle / VTSingle / Grafana
│   ├── mcp-{victoriametrics,victorialogs,victoriatraces,victoriatraces-logsql,grafana}.yaml   # MCP servers
│   └── otel-k8s-events.yaml    # events collector: k8s_events receiver → VLSingle (OTLP), one stream per namespace
├── k8s-events/                 # Kustomize: "Cluster — Events" dashboard, event and node alerts, collector scrape
└── manifests/                  # Kustomize: Grafana admin password generator + ExternalSecret
    └── mcp-grafana/            # Grafana service account token + caller token (ExternalSecrets), VMPodScrape
```

## Operator vs. plain Helm charts

| Layer | Tool | Reason |
|---|---|---|
| Operator + CRDs | `victoria-metrics-operator` chart, separate Application | Controlled CRD upgrades and removal order, as recommended by the k8s-stack docs |
| Data components (VMSingle, VLSingle, VLAgent, VTSingle, VMAgent, VMAlert, VMAlertmanager) | Operator CRs rendered by `victoria-metrics-k8s-stack` | Storage rollouts, config reloads, service discovery via VMServiceScrape/VMPodScrape, VMRule |
| Grafana, kube-state-metrics, node-exporter, dashboards, rules | k8s-stack subcharts and sync-job | Single source of versions; official dashboards and alerting rules |

The operator CRDs let application teams ship their own scrape configs, alerts and dashboards next to
their workloads (see `apps/k8s/data/monitoring.yaml` and `apps/k8s/services/templates/monitoring.yaml`).

## Topology

| Signal | Components | Replicas | Storage | Retention |
|---|---|---|---|---|
| Metrics | VMSingle | 1 | 30Gi pd-balanced | 30d |
| Scraping | VMAgent StatefulSet, deduplicated at 30s on VMSingle | 2 | 2 × 10Gi persistent queue (8GiB cap) | until delivered |
| Alerting | VMAlert → VMAlertmanager (gossip cluster); alert state persisted to VictoriaMetrics | 2 / 2 | 2 × 10Gi (silences, notification log) | – |
| Logs | VLSingle + VLAgent DaemonSet | 1 (+1 agent per node) | 20Gi pd-balanced | 90d, oldest days dropped beyond 16GiB |
| Traces | VTSingle | 1 | 20Gi pd-balanced | 30d, oldest days dropped beyond 16GiB |
| UI | Grafana 13 | 1 | 10Gi for runtime state (dashboards and datasources provisioned from Git) | – |

Each signal runs as one single-node process: simpler than a cluster (no insert/select/storage tiers, load
balancers or PDBs) and far below its limits here. The case studies in the VictoriaMetrics docs run single-node
with 100k+ active series and tens of thousands of samples per second; VictoriaLogs compresses logs ~10-50x, so
2 TB of raw logs a month stays within one node. Move to cluster mode only when one node runs out of CPU, memory
or disk throughput.

Trade-off: no high availability for queries and ingestion. When a storage pod restarts or its Spot VM is
preempted (about 2 minutes), dashboards and alert evaluation pause. Nothing is lost on the collection side:
both vmagents keep samples in their persistent queues and vlagent buffers on its node; the OpenTelemetry SDKs
retry spans and drop them only once their queue is full. Durability comes from GCE persistent disks.

**Disk sizing.** The disks start small and grow online (raise `storage` in `values/vm-k8s-stack.yaml`; the
standard-rwo StorageClass allows volume expansion):

- VictoriaMetrics deletes data per monthly partition, so the disk holds up to retention + 1 month: about 2
  months of data for the 30-day retention. Today ~4.5k samples/s (137k active series every 30s) ≈ 7-10GB a
  month. The `DiskRunsOutOfSpaceIn3Days` alert (vmhealth rules) warns early; `storage.minFreeDiskSpaceBytes`
  stops ingestion before the disk is completely full.
- VictoriaLogs and VictoriaTraces keep per-day partitions, so the disk holds the retention.
  `retentionMaxDiskSpaceUsageBytes` (16GiB of 20Gi) drops the oldest days first: the effective retention is
  shorter than 90 or 30 days while the disk is too small. Raise both numbers together.

VMAgent runs in `statefulMode`, so its remote-write persistent queue lives on a PVC instead of an
emptyDir. If VMSingle is unreachable, buffered samples (about 1.5 days at the current ~60KB/s per
replica) survive a pod being rescheduled, for example after a Spot preemption.

VMSingle protections (see the docs, *Resource usage limits* and *Cardinality limiter*):

| Setting | Value | Purpose |
|---|---|---|
| `search.maxConcurrentRequests` / `search.maxMemoryPerQuery` | 8 / 128MB | A single wide query (dashboards, AI assistants via MCP) cannot OOM VMSingle |
| `storage.maxHourlySeries` / `storage.maxDailySeries` | 500k / 1.5M | Cardinality explosions are dropped and logged instead of exhausting memory |
| `storage.minFreeDiskSpaceBytes` | 2GB | Read-only mode before the disk fills up completely |
| `dedup.minScrapeInterval` | 30s | Deduplicate the two HA vmagent replicas |
| Slow query log (default) | >5s | Slow queries land in VictoriaLogs |

## Data flow

```text
Pod stdout (JSON) ──► VLAgent (DaemonSet) ──► VLSingle ◄── Grafana (VictoriaLogs plugin)
App /metrics ◄── VMAgent ×2 (VMServiceScrape/VMPodScrape) ──► VMSingle ◄── Grafana
App OTLP/HTTP ──► VTSingle ◄── Grafana (Jaeger datasource on /select/jaeger, VictoriaLogs plugin for LogsQL on spans)
VMRule ──► VMAlert ×2 ──(metrics: VMSingle, logs: VLSingle via internal vmauth)──► Alertmanager ×2
```

Endpoints (namespace `monitoring`):

| Purpose | URL |
|---|---|
| OTLP traces | `http://vtsingle-vmks.monitoring.svc:10428/insert/opentelemetry/v1/traces` |
| OTLP logs | `http://vlsingle-vmks.monitoring.svc:9428/insert/opentelemetry/v1/logs` |
| Remote write | `http://vmsingle-vmks.monitoring.svc:8428/api/v1/write` |
| PromQL / MetricsQL | `http://vmsingle-vmks.monitoring.svc:8428/prometheus` |
| LogsQL | `http://vlsingle-vmks.monitoring.svc:9428/select/logsql/query` |
| LogsQL on spans | `http://vtsingle-vmks.monitoring.svc:10428/select/logsql/query` |
| Jaeger API | `http://vtsingle-vmks.monitoring.svc:10428/select/jaeger` |

## Grafana

- Datasources: **VictoriaMetrics** (default, Prometheus type), **VictoriaMetrics (DS)**
  (`victoriametrics-metrics-datasource` 0.26.1), **VictoriaLogs** (`victoriametrics-logs-datasource`
  0.33.1), **VictoriaTraces** (built-in Jaeger: trace search, trace view, service map), **VictoriaTraces
  (LogsQL)** (the VictoriaLogs plugin on VTSingle: VictoriaTraces stores spans like VictoriaLogs stores logs,
  so LogsQL aggregates them, e.g. p95 and failures per endpoint, operation, SQL statement or attribute),
  **Alertmanager**.
- Correlation: `trace_id` in logs links to traces; spans link to logs (`trace_id:="<id>"`) and to the
  RED metrics of their service.
- Dashboards: VictoriaMetrics/Logs/Traces single-node, vmagent, vmalert, operator, Kubernetes views,
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
| `vm-mcp` ([mcp-victoriametrics](https://github.com/VictoriaMetrics/mcp-victoriametrics)) | VMSingle | `query`, `query_range`, `metrics`, `labels`, `series`, `rules`, `alerts`, `tsdb_status`, `explain_query` |
| `vl-mcp` ([mcp-victorialogs](https://github.com/VictoriaMetrics/mcp-victorialogs)) | VLSingle | `query` (LogsQL), `hits`, `stats_query_range`, `facets`, `field_values`, `streams` |
| `vt-mcp` ([mcp-victoriatraces](https://github.com/VictoriaMetrics-Community/mcp-victoriatraces)) | VTSingle | `services`, `traces`, `trace`, `dependencies` |
| `vt-logsql-mcp` ([mcp-victorialogs](https://github.com/VictoriaMetrics/mcp-victorialogs) on VTSingle) | VTSingle (LogsQL) | `stats_query`, `stats_query_range`, `query`, `field_names`, `field_values` over spans: latency, failures and time share per service, endpoint, operation or attribute |
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
