# Runbooks and investigation guide

Every data-tier and platform alert links to its runbook section through the `runbook_url` annotation:

- [postgres.md](postgres.md): CloudNativePG cluster `deus-db` (availability, replication, locks, disk, backups)
- [kubernetes.md](kubernetes.md): Kubernetes events, node restarts (spot preemptions), scheduling, OOM kills
- [platform.md](platform.md): Argo CD (sync failures, drift, health, Git), External Secrets (sync, stores),
  cert-manager (certificate readiness, renewal and expiry, issuers)
- [observability.md](observability.md): the observability pipeline itself (OpenTelemetry Collector, log and span
  ingestion stopped, retention cut by the disk limit, log lines too long)

This page describes how to investigate in general: where each signal is stored, the field names to query,
and the order that finds a root cause fastest. It is written for on-call engineers and for AI assistants
that query the stack through the MCP servers.

## Where the signals are

| Signal | Store | Grafana datasource (uid) | MCP server |
|---|---|---|---|
| Metrics, SLO recording rules, alerts | VictoriaMetrics (VMSingle) + vmalert | `VictoriaMetrics` | `victoriametrics` (query, query_range, alerts, rules) |
| Application logs (OTLP), container output, PostgreSQL logs | VictoriaLogs (VLSingle) | `VictoriaLogs` | `victorialogs` (query, stats_query, field_names) |
| Kubernetes events (stream `{service.name="kubernetes-events"}`) | VictoriaLogs (VLSingle) | `VictoriaLogs` | `victorialogs` |
| Traces | VictoriaTraces (VTSingle, Jaeger API) | `VictoriaTraces` | `victoriatraces` (services, traces, trace, dependencies) |
| Span analytics (LogsQL on spans) | VictoriaTraces | `VictoriaTracesLogsQL` | `victoriatraces-logsql` (stats_query, stats_query_range, field_values) |
| Dashboards, annotations | Grafana | – | `grafana` (search_dashboards, get_dashboard_summary) |
| Live sessions, locks, statistics | PostgreSQL (`deus-db`) | – | `postgres` (list_active_queries, list_locks, execute_sql; read-only, `pg_monitor`) |

Dashboards (Grafana folder **Platform**): `cloudnative-pg` (CloudNativePG, one row per concern: Queries and
errors, Locks and blocking, Top statements and table health, Database efficiency, Replication, Backups),
`cluster-events` (the Kubernetes events of every namespace), `platform-gitops-secrets` (Argo CD applications and
syncs, ExternalSecrets and certificates) and `observability-pipeline` (whether the stack itself collects, stores and
alerts: when a signal looks missing, check it first). Service dashboards and SLOs arrive with the application.

## Investigation order

1. **Impact.** Which service or user journey fails or slows down? Request rate, errors and latency per service
   from `http_server_request_duration_seconds_*` (labels `service_name`, `http_route`,
   `http_response_status_code`).
2. **A failing request.** Search the traces of the service (`VictoriaTraces`, or LogsQL on spans) for its slowest
   or failed requests and follow the spans down; database spans carry the statement and the error.
3. **The logs of that request.** Every application log record carries the `trace_id` and `span_id` of its
   request, so `trace_id:<id>` returns the lines of every service for one request.
4. **The database side, at the same time.** PostgreSQL logs (errors with SQLSTATE, lock waits naming the
   blocking pid, slow statements) and the CloudNativePG dashboard rows. Live sessions and locks through the
   `postgres` MCP server or psql ([postgres.md](postgres.md#live-diagnosis)).
5. **Changes and the platform.** Rollout and restart annotations, Argo CD syncs and failed syncs (Platform —
   GitOps & Secrets dashboard), and the Kubernetes events (Cluster — Events dashboard, or LogsQL
   `{service.name="kubernetes-events"}`): scheduling failures, probe failures, OOM kills, node restarts. The API
   server keeps events for one hour; VictoriaLogs for 90 days.

## Field reference

**Application logs** (OpenTelemetry log records through the collector), one stream per service
`{service.name="<service>", k8s.namespace.name="deus"}`: the message, severity, `trace_id`, `span_id`, the record's
attributes and the resource attributes added by the collector (`k8s.pod.name`, `k8s.deployment.name`,
`k8s.node.name`, `service.version`). Credential-like attribute values are masked (`****`). List the exact field
names with the `field_names` MCP tool or the Grafana field browser.

**PostgreSQL logs** (CloudNativePG JSON), stream
`{kubernetes.pod_namespace="deus", kubernetes.container_name="postgres"}`, cluster filter
`"kubernetes.pod_labels.cnpg.io/cluster":in(deus-db)`, instance `kubernetes.pod_name`:
`record.error_severity` (`LOG`, `ERROR`, `FATAL`, `PANIC`), `record.sql_state_code`, `record.message`,
`record.detail`, `record.query`, `record.query_id`, `record.process_id`, `record.user_name`,
`record.application_name` (the service name for application sessions), `record.database_name`.

**Backup sidecar logs**: `{kubernetes.pod_namespace="deus", kubernetes.container_name="plugin-barman-cloud"}`.

**Metrics**:

| Prefix | Source | Useful labels |
|---|---|---|
| `slo:*` | SLO recording rules | `job`, `slo` (`availability`, `latency`), `kind` (`service`, `journey`) |
| `http_server_request_duration_seconds_*`, `http_client_request_duration_seconds_*` | services (OTLP, Prometheus naming) | `service_name`, `http_route`, `http_request_method`, `http_response_status_code`, `k8s_pod_name` |
| `otelcol_*` | OpenTelemetry Collector | `job` (`monitoring/otel-collector`, `monitoring/k8s-events`), `exporter`, `receiver` |
| `cnpg_*` | CloudNativePG exporter, every instance | `pod`, `datname`, `usename`, `application_name`, `state` |
| `cnpg_pg_stat_statements_top_*` | custom query, primary only | `queryid`, `query` (normalized text) |
| `cnpg_pg_stat_user_tables_*` | custom query, primary only | `datname`, `relname` |
| `cnpg_pg_blocking_*` | custom query, primary only: sessions holding a lock others wait for | `pid`, `usename`, `application_name`, `state`, `query_id` |
| `barman_cloud_cloudnative_pg_io_*` | Barman Cloud plugin | `pod` |

## Recipes

```text
# LogsQL: every log line of one request across services
{k8s.namespace.name="deus"} trace_id:<trace id>

# LogsQL: PostgreSQL errors by SQLSTATE in the last hour
_time:1h {kubernetes.pod_namespace="deus", kubernetes.container_name="postgres"}
  record.error_severity:in(ERROR, FATAL, PANIC) | stats by (record.sql_state_code, record.message) count() n

# LogsQL: lock waits with the pids holding the lock
_time:1h {kubernetes.pod_namespace="deus", kubernetes.container_name="postgres"} record.message:"still waiting for"
  | fields _time, record.application_name, record.message, record.detail, record.query

# LogsQL: everything one PostgreSQL backend logged (e.g. the blocker pid from a lock wait)
_time:1h {kubernetes.pod_namespace="deus", kubernetes.container_name="postgres"} record.process_id:<pid>

# MetricsQL: sessions per service and state on the primary
sum by (application_name, state) (cnpg_backends_total{namespace="deus"} * on (pod) group_left
  (cnpg_pg_replication_in_recovery{namespace="deus"} == 0))

# MetricsQL: sessions holding locks others wait for (no series while nothing waits)
max_over_time(cnpg_pg_blocking_blocked_sessions{namespace="deus"}[1h])
```

**Spans** (LogsQL on VictoriaTraces): `resource_attr:service.name`, `name` (operation), `kind` (1 internal,
2 server, 3 client, 4 producer, 5 consumer), `duration` (ns), `status_code` (2 = error), `status_message`,
`trace_id`, `parent_span_id`, and attributes such as `span_attr:http.route`,
`span_attr:http.response.status_code`, `span_attr:db.query.text`, `span_attr:server.address`.

```text
# LogsQL on spans: p95 and 5xx share per endpoint
_time:1h "resource_attr:k8s.namespace.name":=deus kind:=2
  | stats by ("resource_attr:service.name", name) count() calls,
      count() if ("span_attr:http.response.status_code":~"^5") e5, quantile(0.95, duration) p95

# LogsQL on spans: every operation inside the traces of one endpoint, by total time
_time:1h trace_id:in(_time:1h kind:=2 name:="POST /api/<route>" | fields trace_id)
  | stats by ("resource_attr:service.name", name) count() calls, sum(duration) total | sort by (total desc)
```

## Pitfalls

- LogsQL word filters match whole words: `"lock"` does not match `ShareLock`. Use a phrase
  (`"still waiting for"`), a prefix (`lock*`) or a regexp (`~"(?i)lock"`).
- The CloudNativePG exporter refreshes its query results every 20s (`metricsQueriesTTL`) and is scraped every
  30s. A lock wait of a few seconds rarely shows in the metrics; the PostgreSQL logs record every wait longer
  than 1s (`log_lock_waits`).
- `cnpg_*` series exist for every instance. The primary is the pod with `cnpg_pg_replication_in_recovery == 0`.
- `57014 canceling statement due to user request` is not a server timeout: the client cancelled the query,
  usually because the application's own timeout expired. The trace shows which call gave up.
- Timestamps in Grafana, the MCP servers and the logs are UTC unless an offset is given.
- A span with `status_code` 2 is not always a failure: HTTP client spans mark 4xx answers as errors (OTel
  semantic conventions) and the payment provider span marks declined cards. The Traces dashboard counts
  neither as failed.
- In LogsQL `format` patterns, `<a:b>` reads `a` as a modifier: copy span attributes to a plain name first
  (`| copy "span_attr:server.address" as peer | format "<peer>" as x`).
