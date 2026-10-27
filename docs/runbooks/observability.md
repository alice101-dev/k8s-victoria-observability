# Observability pipeline runbook (collection, storage, alerting)

The alerts of the `observability-pipeline` VMRule (`infrastructure/configs/victoria-metrics-k8s-stack/base/pipeline.yaml`) and the
`otel-collector` VMRule (`infrastructure/configs/otel-collector/base/monitoring.yaml`) link to the sections below. They cover what the upstream rule sets do not: the upstream groups (`vmagent`, `vmsingle`, `vmalert`,
`vlagent`, `vlogs`, `vtraces`, `alertmanager.rules`, `vm-health`, `vl-health`, `vt-health`, `general.rules`) alert on
each component, with their own runbooks linked from the alerts.

The **Observability — Pipeline health** dashboard (Grafana folder **Platform**) shows the whole pipeline on one
page: scrape targets, ingestion of metrics, logs and spans, the OpenTelemetry Collector, delivery backlog and
retries, every place data is
dropped, disk and retention per store, rule evaluation and notifications, kube-dns, and the stack's restarts and
memory. Every section links to the component's own dashboard.

| Signal | Path | Store | Limit |
|---|---|---|---|
| Metrics | vmagent ×2 (scrape, on-disk queue) → VMSingle | 30d, per month partitions | read-only below 2 GB free (`DiskRunsOutOfSpaceIn3Days` warns) |
| Logs, Kubernetes events | vlagent (DaemonSet, on-disk queue), k8s-events collector → VLSingle | 90d, per day partitions | oldest days dropped at 80% disk usage |
| Application metrics, logs, spans | OpenTelemetry SDKs → OTel Collector ×2 (in-memory sending queue) → VMSingle, VLSingle, VTSingle | as the stores | as the stores |
| Alerts | vmalert ×2 → Alertmanager ×2 (gossip) → receivers | – | – |

A spot preemption of the node running a store pauses ingestion and queries for a few minutes: the agents buffer
and resend, the collector keeps what fits in its sending queue. The dashboard then shows the store's targets down, the backlog
rising and draining, and the restart in Pipeline components.

## VictoriaLogsRetentionCutByDiskCap

**Meaning.** VLSingle runs with `retention.maxDiskUsagePercent=80`: at 80% filesystem usage it drops its oldest
daily partitions instead of filling the disk. It has been at that limit for an hour, so it keeps fewer days than
the configured 90 (the alert value). The upstream `DiskRunsOutOfSpace` rule of the `vlogs` group is disabled
because this state is normal operation, not a disk about to fill.

**Diagnose.** Storage row of the dashboard: Oldest data against Retention, Growth per day. A sudden growth is
usually one workload logging much more (LogsQL
`_time:1h | stats by (service.name, kubernetes.pod_namespace, kubernetes.container_name) count() lines | sort by (lines desc)`).

**Fix.** If the shorter retention is acceptable, nothing. Otherwise reduce the noisy logs, or grow the disk:
raise `vlsingle.spec.storage.resources.requests.storage` in `infrastructure/controllers/victoria-metrics-k8s-stack/values-staging.yaml` (the
pd-balanced StorageClass expands online; the 80% limit follows the new size).

## VictoriaTracesRetentionCutByDiskCap

**Meaning.** As for VictoriaLogs, for spans: VTSingle is at its 80% limit and keeps fewer days than the configured
30.

**Fix.** Sample traces (head sampling in the services' SDKs, or tail sampling in the collector) or grow
`vtsingle.spec.storage` in `infrastructure/controllers/victoria-metrics-k8s-stack/values-staging.yaml`.

## LogsIngestionStopped

**Meaning.** VLSingle stored no log line for 20 minutes (10-minute rate window plus 10 minutes `for`). Something
always logs in the cluster, so the collection or ingestion path is broken, even if each component reports up.

**Diagnose.** Ingestion and Dropped data rows of the dashboard: are the vlagent pods collecting (lines collected
by vlagent) and is their backlog rising (VLSingle refuses or is unreachable)? `kubectl -n monitoring get pods -l
app.kubernetes.io/name=vlagent`, `kubectl -n monitoring logs ds/vlagent-vmks`, and VLSingle's logs and
`vl_storage_is_read_only` (read-only when the disk is full despite the limit, e.g. a stuck merge).

**Fix.** Restart the failing component; the agents' disk queues resend what they buffered.

## TracesIngestionStopped

**Meaning.** VTSingle stored no span over OTLP for 20 minutes while the OpenTelemetry Collector accepts spans.
The collector cannot deliver to `vtsingle-vmks.monitoring.svc:10428` (DNS, network, VTSingle down) or VTSingle
rejects the spans.

**Diagnose.** OpenTelemetry Collector row of the dashboard (Exported, Sending queue), the collector's logs
(`kubectl -n monitoring logs deploy/otel-collector`), the DNS row, `kubectl -n monitoring get pods -l
app.kubernetes.io/name=vtsingle` and VTSingle's logs.

**Fix.** Restore VTSingle or the network path. The collector resends what is in its sending queue; spans beyond it
are lost (OtelCollectorQueueFull).

## LogLinesTooLong

**Meaning.** Severity `info` (not sent to Slack). vlagent or VLSingle skipped log lines longer than the insert
limit (`-insert.maxLineSizeBytes`, 256 KiB by default) in the last hour; those lines are not stored.

**Diagnose.** Dropped data row: the cause names the component that skipped them. Find the writer by the line
count around the time: one container usually dumps a large object or a stack trace as a single line.

**Fix.** Make the application log smaller lines; raise the limit only if such lines are needed.

## OtelCollectorDown

**Meaning.** No replica of the OpenTelemetry Collector gateway is scraped as up for 5 minutes. Applications cannot
deliver metrics, logs or spans; their SDKs retry briefly and then drop.

**Diagnose.** `kubectl -n monitoring get pods -l app.kubernetes.io/instance=otel-collector`, its events and logs.
A configuration error shows in the logs at startup; validate the rendered configuration with
`otelcol-contrib validate` ([docs/observability.md](../observability.md#opentelemetry-collector-gateway)).

**Fix.** Revert the change that broke it, or fix scheduling (both replicas on a preempted node).

## OtelCollectorRefusingTelemetry

**Meaning.** The collector refuses data for 10 minutes: the memory limiter reached 80% of the container limit and
pushes back. Senders retry and then drop.

**Diagnose.** OpenTelemetry Collector row: Received (refused per signal) and the memory of the collector pods in
Pipeline components. A sudden rise in received data points to one service (`k8s.deployment.name` on its records).

**Fix.** Raise the memory limit or the replica count in `infrastructure/controllers/otel-collector`, or reduce what the noisy service
sends.

## OtelCollectorExportFailing

**Meaning.** An exporter keeps failing to deliver to its store for 10 minutes. The data waits in the sending
queue and is retried.

**Diagnose.** The exporter names the store (`otlp_http/victoriametrics`, `otlp_http/victorialogs`,
`otlp_http/victoriatraces`). The collector's logs carry the HTTP status: 4xx means the store rejects the data
(too many labels, too long lines), 5xx or connection errors mean the store is down or overloaded.

**Fix.** Restore the store, or fix what it rejects (drop or shorten the offending attributes in the collector).

## OtelCollectorQueueFull

**Meaning.** An exporter's sending queue was full and new items were dropped in the last 5 minutes. They are lost.

**Diagnose.** Sending queue panel of the dashboard and OtelCollectorExportFailing: the store behind the exporter
has been unavailable or too slow for longer than the queue holds.

**Fix.** Restore the store. For longer outages, enable a persistent sending queue (`file_storage` extension on a
volume) or raise the queue size.
