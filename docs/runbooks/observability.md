# Observability pipeline runbook (collection, storage, alerting)

The alerts of the `observability-pipeline` VMRule (`observability/platform/pipeline.yaml`) link to the sections
below. They cover what the upstream rule sets do not: the upstream groups (`vmagent`, `vmsingle`, `vmalert`,
`vlagent`, `vlogs`, `vtraces`, `alertmanager.rules`, `vm-health`, `vl-health`, `vt-health`, `general.rules`) alert on
each component, with their own runbooks linked from the alerts.

The **Observability — Pipeline health** dashboard (Grafana folder **Platform**) shows the whole pipeline on one
page: scrape targets, ingestion of metrics, logs and spans, delivery backlog and retries, every place data is
dropped, disk and retention per store, rule evaluation and notifications, kube-dns, and the stack's restarts and
memory. Every section links to the component's own dashboard.

| Signal | Path | Store | Limit |
|---|---|---|---|
| Metrics | vmagent ×2 (scrape, on-disk queue) → VMSingle | 30d, per month partitions | read-only below 2 GB free (`DiskRunsOutOfSpaceIn3Days` warns) |
| Logs, Kubernetes events | vlagent (DaemonSet, on-disk queue), k8s-events collector → VLSingle | 90d, per day partitions | oldest days dropped at 80% disk usage |
| Spans | service OpenTelemetry SDKs (OTLP/HTTP) → VTSingle | 30d, per day partitions | oldest days dropped at 80% disk usage |
| Alerts | vmalert ×2 → Alertmanager ×2 (gossip) → receivers | – | – |

A spot preemption of the node running a store pauses ingestion and queries for a few minutes: the agents buffer
and resend, spans beyond the SDK queue are lost. The dashboard then shows the store's targets down, the backlog
rising and draining, and the restart in Pipeline components.

## VictoriaLogsRetentionCutByDiskCap

**Meaning.** VLSingle runs with `retention.maxDiskUsagePercent=80`: at 80% filesystem usage it drops its oldest
daily partitions instead of filling the disk. It has been at that limit for an hour, so it keeps fewer days than
the configured 90 (the alert value). The upstream `DiskRunsOutOfSpace` rule of the `vlogs` group is disabled
because this state is normal operation, not a disk about to fill.

**Diagnose.** Storage row of the dashboard: Oldest data against Retention, Growth per day. A sudden growth is
usually one workload logging much more (Shop — Logs, or LogsQL
`_time:1h | stats by (kubernetes.pod_namespace, kubernetes.container_name) count() lines | sort by (lines desc)`).

**Fix.** If the shorter retention is acceptable, nothing. Otherwise reduce the noisy logs, or grow the disk:
raise `vlsingle.spec.storage.resources.requests.storage` in `observability/values/vm-k8s-stack.yaml` (the
pd-balanced StorageClass expands online; the 80% limit follows the new size).

## VictoriaTracesRetentionCutByDiskCap

**Meaning.** As for VictoriaLogs, for spans: VTSingle is at its 80% limit and keeps fewer days than the configured
30.

**Fix.** Lower the sampling ratio (`otel.samplerArg` in `apps/k8s/services/values.yaml`) or grow
`vtsingle.spec.storage` in `observability/values/vm-k8s-stack.yaml`.

## LogsIngestionStopped

**Meaning.** VLSingle stored no log line for 20 minutes (10-minute rate window plus 10 minutes `for`). Something
always logs in the cluster, so the collection or ingestion path is broken, even if each component reports up.

**Diagnose.** Ingestion and Dropped data rows of the dashboard: are the vlagent pods collecting (lines collected
by vlagent) and is their backlog rising (VLSingle refuses or is unreachable)? `kubectl -n monitoring get pods -l
app.kubernetes.io/name=vlagent`, `kubectl -n monitoring logs ds/vlagent-vmks`, and VLSingle's logs and
`vl_storage_is_read_only` (read-only when the disk is full despite the limit, e.g. a stuck merge).

**Fix.** Restart the failing component; the agents' disk queues resend what they buffered.

## TracesIngestionStopped

**Meaning.** VTSingle received no span over OTLP for 20 minutes while the shop serves requests. The services'
exporters cannot deliver: DNS or network to `vtsingle-vmks.monitoring.svc:10428`, or VTSingle rejects them.

**Diagnose.** The services log exporter failures (`traces export: ... no such host` or `connection refused`):
Shop — Logs, message search `traces export`. DNS row of the dashboard. `kubectl -n monitoring get pods -l
app.kubernetes.io/name=vtsingle` and its logs.

**Fix.** Restore VTSingle or the network path; spans of the outage are lost (the SDK only queues a few seconds).

## LogLinesTooLong

**Meaning.** Severity `info` (not sent to Slack). vlagent or VLSingle skipped log lines longer than the insert
limit (`-insert.maxLineSizeBytes`, 256 KiB by default) in the last hour; those lines are not stored.

**Diagnose.** Dropped data row: the cause names the component that skipped them. Find the writer by the line
count around the time: one container usually dumps a large object or a stack trace as a single line.

**Fix.** Make the application log smaller lines; raise the limit only if such lines are needed.
