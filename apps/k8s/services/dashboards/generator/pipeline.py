"""Observability — Pipeline health: is the stack collecting, storing and alerting? One page over vmagent, vlagent,
VMSingle, VLSingle, VTSingle, vmalert, Alertmanager and kube-dns, each section linking to the component's own
dashboard."""
from .common import (COUNT, RI, VM, by_name, Dash, dashboard, restart_annotations, right_axis, stat, table,
    thresholds, ts, vm)

FILE = "observability-pipeline.json"
UID = "observability-pipeline"

d = Dash()
NS = 'namespace="monitoring"'
OTLP_SPANS = 'vt_rows_ingested_total{job="vtsingle-vmks", type=~"opentelemetry.*"}'
# Component dashboards installed by the k8s-stack chart (uids of the pinned upstream JSON).
COMPONENT = {
    "vmagent": ("VictoriaMetrics - vmagent", "G7Z9GzMGz"),
    "vmsingle": ("VictoriaMetrics - single-node", "wNf0q_kZk"),
    "vlagent": ("VictoriaLogs - vlagent", "Y5Z9GzMGz"),
    "vlsingle": ("VictoriaLogs - single-node", "OqPIZTX4z"),
    "vtsingle": ("VictoriaTraces - single-node", "10dbc46d-e6fe-46c9-8ee2-57783dd70695"),
    "vmalert": ("VictoriaMetrics - vmalert", "LzldHAVnz"),
    "alertmanager": ("Alertmanager / Overview", "alertmanager-overview"),
    "volumes": ("Kubernetes / Persistent Volumes", "919b92a8e8041bd567af9edab12c840c"),
    "operator": ("VictoriaMetrics - operator", "1H179hunk"),
    "events": ("Cluster — Events", "cluster-events"),
}


def links(*names):
    return [{"title": f"Open {COMPONENT[n][0]}", "url": f"/d/{COMPONENT[n][1]}?${{__url_time_range}}"} for n in names]


def with_links(p, *names):
    p["links"] = links(*names)
    return p


def per_interval(p, interval="5m"):
    p["interval"] = interval
    return p


def bargauge(title, target, unit, th, desc, max_=None):
    defaults = {"unit": unit, "min": 0, "thresholds": th}
    if max_ is not None:
        defaults["max"] = max_
    return {"type": "bargauge", "title": title, "description": desc, "datasource": VM,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "color",
                        "namePlacement": "left", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
            "targets": [target]}


# Every place the pipeline throws data away: (cause, label whose value replaces $1, expression over window W).
# The last one counts bytes (an agent's on-disk queue overflowed), the others count records.
DROPS = [
    ("Metrics rejected by VMSingle: $1", "reason", 'sum by (reason) (increase(vm_rows_ignored_total{job="vmsingle-vmks"}[W]))'),
    ("Metrics over the series limit", "", 'sum(increase({__name__=~"vm(agent)?_(hourly|daily)_series_limit_rows_dropped_total"}[W]))'),
    ("Metric samples dropped by vmagent", "", "sum(increase(vmagent_remotewrite_samples_dropped_total[W]))"),
    ("Metric blocks rejected by VMSingle", "", "sum(increase(vmagent_remotewrite_packets_dropped_total[W]))"),
    ("Log lines too long ($1)", "job", "sum by (job) (increase(vl_too_long_lines_skipped_total[W]))"),
    ("Log lines rejected: $1", "reason", "sum by (reason) (increase(vl_rows_dropped_total[W]))"),
    ("Log blocks rejected by VLSingle", "", "sum(increase(vlagent_remotewrite_packets_dropped_total[W]))"),
    ("Spans rejected: $1", "reason", "sum by (reason) (increase(vt_rows_dropped_total[W]))"),
    ("Recording rule results dropped by vmalert", "", "sum(increase(vmalert_remotewrite_dropped_rows_total[W]))"),
    ("Agent disk queue full, bytes lost ($1)", "job", "sum by (job) (increase(vm_persistentqueue_bytes_dropped_total[W]))"),
]


def drop(cause, label, expr, window):
    return f'label_replace(round({expr.replace("[W]", f"[{window}]")}), "cause", "{cause}", "{label}", "(.*)")'


def all_drops(window, records_only=False):
    rows = DROPS[:-1] if records_only else DROPS
    return "sum by (cause) (" + " or ".join(drop(c, l, e, window) for c, l, e in rows) + ")"


# Per store: (name, metric prefix, job = PVC name). VMSingle turns read-only when free space falls under
# -storage.minFreeDiskSpaceBytes; VLSingle and VTSingle drop their oldest days at 80% (retention.maxDiskUsagePercent).
STORES = (("VictoriaMetrics", "vm", "vmsingle-vmks"), ("VictoriaLogs", "vl", "vlsingle-vmks"),
          ("VictoriaTraces", "vt", "vtsingle-vmks"))


def per_store(fn, by="store"):
    """One series per store from fn(prefix, job) (None: not available for that store), labelled store."""
    parts = [f'label_replace({fn(p, j)}, "store", "{n}", "", "")' for n, p, j in STORES if fn(p, j)]
    return f"max by ({by}) (" + " or ".join(parts) + ")"


def headroom(p, j):
    if p == "vm":
        return f'max(vm_free_disk_space_bytes{{job="{j}"}}) - max(vm_free_disk_space_limit_bytes{{job="{j}"}})'
    return (f'max({p}_max_disk_space_usage_bytes{{job="{j}"}}) - (max({p}_total_disk_space_bytes{{job="{j}"}}) '
            f'- max({p}_free_disk_space_bytes{{job="{j}"}}))')


def growth(p, j):  # bytes per day, from the free space trend of the last day
    return f'clamp_min(max(-deriv({p}_free_disk_space_bytes{{job="{j}"}}[1d])) * 86400, 0)'


def days_left(p, j):  # capped at 10 years: no growth means "never"
    return f"clamp_max(({headroom(p, j)}) / clamp_min({growth(p, j)}, 1), 3650)"


LIMIT = {"vm": "read-only below 2 GB free", "vl": "oldest days dropped at 80%", "vt": "oldest days dropped at 80%"}
DAYS_TH = thresholds(("red", None), ("orange", 3), ("green", 14))
DAYS_MAP = [{"type": "range", "options": {"from": 365, "to": None, "result": {"text": "more than a year", "index": 0}}}]
PVC = lambda j: f'persistentvolumeclaim="{j}", {NS}'

d.row("Overview")
for p in (
    stat("Scrape targets down", "count(up == 0) or vector(0)", "short", thresholds(("green", None), ("red", 1)),
         "Targets vmagent cannot scrape right now (TargetDown alerts when more than 10% of a job is down).", decimals=0),
    stat("Metric samples / s", 'sum(rate(vm_rows_inserted_total{job="vmsingle-vmks"}[5m]))', "short",
         thresholds(("red", None), ("green", 1)), "Samples VMSingle receives (both vmagent replicas; it deduplicates "
         "them at 30s).", decimals=0),
    stat("Log lines / s", 'sum(rate(vl_rows_ingested_total{job="vlsingle-vmks"}[5m]))', "short",
         thresholds(("red", None), ("green", 0.01)), "Log lines and Kubernetes events VLSingle stores. 0 fires "
         "LogsIngestionStopped.", decimals=1),
    stat("Spans / s", f"sum(rate({OTLP_SPANS}[5m]))", "short", thresholds(("orange", None), ("green", 0.01)),
         "Spans VTSingle receives from the services over OTLP. 0 while the shop serves requests fires "
         "TracesIngestionStopped.", decimals=1),
    stat("Records dropped", f"{all_drops('$__range', records_only=True).replace('sum by (cause)', 'sum', 1)} or vector(0)",
         "short", thresholds(("green", None), ("orange", 1)), "Metric samples, log lines and spans the pipeline threw "
         "away in the time range; the Dropped data row says where and why.", decimals=0),
    stat("Rule evaluation errors", "max(sum by (pod) (increase({__name__=~\"vmalert_(alerting|recording)_rules_errors_total\"}"
         "[$__range]))) or vector(0)", "short", thresholds(("green", None), ("red", 1)),
         "Rule evaluations that failed in the time range (per vmalert replica; both evaluate every rule). Failing "
         "rules cannot fire.", decimals=0),
    stat("Notifications failed", "sum(increase(alertmanager_notifications_failed_total[$__range])) "
         "+ sum(increase(vmalert_alerts_send_errors_total[$__range]))", "short", thresholds(("green", None), ("red", 1)),
         "Alerts vmalert could not hand to Alertmanager, plus notifications Alertmanager could not deliver (Slack).",
         decimals=0),
    stat("Days until a store's limit", f"min({per_store(days_left)})", "short", DAYS_TH,
         "Fewest days, among VMSingle, VLSingle and VTSingle, until the disk reaches its limit at the last day's "
         "growth (the Storage row has each store).", decimals=0),
):
    d.add(p, 3, 4)
d.panels[-1]["fieldConfig"]["defaults"]["mappings"] = DAYS_MAP

d.row("Collection — scrape targets")
with_links(d.add(table("Targets down", [vm("up == 0", instant=True, fmt="table")],
                       [{"id": "organize", "options": {
                           "excludeByName": {"Time": True, "Value": True, "__name__": True, "cluster": True,
                                             "prometheus": True, "endpoint": True},
                           "indexByName": {"job": 0, "namespace": 1, "pod": 2, "instance": 3},
                           "renameByName": {"job": "Job", "namespace": "Namespace", "pod": "Pod", "instance": "Target"}}}],
                       desc="Every target whose last scrape failed. Empty is healthy.",
                       defaults={"noValue": "All targets up", "custom": {"align": "auto", "cellOptions": {"type": "auto"}}}),
                 8, 7), "vmagent")
with_links(d.add(ts("Targets down and failed scrapes",
                    [vm("count by (job) (up == 0)", "{{job}} down"),
                     vm(f"sum(rate(vm_promscrape_scrapes_failed_total{RI}))", "failed scrapes / s", "B")],
                    "short", min_=0, decimals=0, overrides=[right_axis("B", "short")],
                    desc="Down targets per job (left) and failed scrapes across both vmagent replicas (right)."),
                 8, 7), "vmagent")
with_links(d.add(bargauge("Samples per scrape, top 10 jobs",
                          vm("topk(10, sum by (job) (scrape_samples_post_metric_relabeling))", "{{job}}", instant=True),
                          "short", thresholds(("blue", None)),
                          "Series each job adds per scrape: where the cardinality comes from."), 8, 7), "vmagent")

d.row("Ingestion — metrics, logs and spans")
with_links(d.add(ts("Metrics into VMSingle",
                    [vm(f'sum by (type) (rate(vm_rows_inserted_total{{job="vmsingle-vmks"}}{RI}))', "{{type}}"),
                     vm(f'sum(rate(vm_new_timeseries_created_total{{job="vmsingle-vmks"}}{RI}))', "new series / s", "B")],
                    "short", min_=0, overrides=[right_axis("B", "short")],
                    desc="Samples per second by protocol (left) and new series per second (right: churn, e.g. after "
                         "rollouts)."), 8, 7), "vmsingle", "vmagent")
with_links(d.add(ts("Logs into VLSingle",
                    [vm(f'sum(rate(vl_rows_ingested_total{{job="monitoring/vlagent-vmks"}}{RI}))', "collected by vlagent"),
                     vm(f'sum(rate(vl_rows_ingested_total{{job="vlsingle-vmks"}}{RI}))', "stored by VLSingle", "B"),
                     vm(f'sum(rate(vl_bytes_ingested_total{{job="vlsingle-vmks"}}{RI}))', "bytes / s", "C")],
                    "short", min_=0, overrides=[right_axis("C", "Bps")],
                    desc="Lines per second read from container logs by vlagent and stored by VLSingle (left; Kubernetes "
                         "events add to the stored lines), and the volume (right)."), 8, 7), "vlsingle", "vlagent")
with_links(d.add(ts("Spans into VTSingle",
                    [vm(f"sum(rate({OTLP_SPANS}{RI}))", "spans / s"),
                     vm(f'sum(rate(http_server_requests_total{{namespace="shop"}}{RI}))', "shop requests / s", "B")],
                    "short", min_=0, overrides=[right_axis("B", "reqps")],
                    desc="Spans per second over OTLP (left) next to the shop's request rate (right): the two move "
                         "together; spans at 0 with traffic means the exporters cannot deliver."), 8, 7), "vtsingle")
with_links(d.add(ts("Delivery backlog",
                    [vm("sum(vmagent_remotewrite_pending_data_bytes)", "vmagent → VMSingle"),
                     vm("sum(vlagent_remotewrite_pending_data_bytes)", "vlagent → VLSingle", "B")],
                    "bytes", min_=0,
                    desc="Data the agents hold because the store does not take it fast enough or is down. It drains "
                         "after a store restart; growing for long means the store is overloaded or unreachable. "
                         "Beyond the on-disk queue size the oldest data is dropped."), 12, 7), "vmagent", "vlagent")
with_links(d.add(ts("Delivery retries and errors",
                    [vm(f"sum(rate(vmagent_remotewrite_retries_count_total{RI}))", "vmagent retries"),
                     vm(f"sum(rate(vlagent_remotewrite_retries_count_total{RI}))", "vlagent retries", "B"),
                     vm(f"sum(rate(vmalert_remotewrite_errors_total{RI}))", "vmalert remote write errors", "C"),
                     vm(f"sum(rate(otelcol_exporter_send_failed_log_records{RI}))", "Kubernetes events send failures", "D")],
                    "short", min_=0,
                    desc="Failed sends per second towards the stores. Retried from the agents' queues; persistent "
                         "retries fire TooManyRemoteWriteErrors (vmagent, vlagent) or RemoteWriteErrors (vmalert)."),
                 12, 7), "vmagent", "vlagent", "vmalert")

d.row("Dropped data")
with_links(d.add(per_interval(ts("Dropped per 5 minutes",
                                 [vm(f"{drop(c, l, e, '$__interval')} > 0", "{{cause}}", chr(65 + i))
                                  for i, (c, l, e) in enumerate(DROPS)],
                                 "short", stack=True, bars=True, decimals=0, legend_calcs=("sum",),
                                 desc="Every place data is thrown away, by cause: rejected by a store (bad timestamps, "
                                      "too many fields, series limits), lines longer than the insert limit, blocks an "
                                      "agent gave up on, and bytes lost when an agent's disk queue was full.")),
                 16, 8), "vmsingle", "vlsingle", "vlagent", "vtsingle")
d.add(table("Dropped in the time range", [vm(f"{all_drops('$__range')} > 0", instant=True, fmt="table")],
            [{"id": "organize", "options": {"excludeByName": {"Time": True},
                                            "renameByName": {"cause": "Cause", "Value": "Count"}}}],
            overrides=[COUNT("Count", 90)], sort=[{"displayName": "Count", "desc": True}],
            defaults={"noValue": "Nothing dropped", "custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
            desc="Totals per cause in the time range (the queue cause counts bytes)."), 8, 8)

d.row("Storage — disk and retention")
STORE_COLUMNS = [("store", "Store"), ("value", "Retention"), ("Value #B", "Oldest data"), ("Value #C", "Disk"),
                 ("Value #D", "Used"), ("limit", "Limit"), ("Value #E", "Room before the limit"),
                 ("Value #F", "Growth per day"), ("Value #G", "Days until the limit")]
d.add(table("Stores", [
    vm(per_store(lambda p, j: f'max by (value) (flag{{job="{j}", name="retentionPeriod"}})', "store, value"),
       ref="A", instant=True, fmt="table"),
    vm(per_store(lambda p, j: None if p == "vm" else f'time() - max({p}_storage_log_min_timestamp_seconds{{job="{j}"}})'),
       ref="B", instant=True, fmt="table"),
    vm(per_store(lambda p, j: f"max(kubelet_volume_stats_capacity_bytes{{{PVC(j)}}})"), ref="C", instant=True, fmt="table"),
    vm(per_store(lambda p, j: f"max(1 - kubelet_volume_stats_available_bytes{{{PVC(j)}}} / kubelet_volume_stats_capacity_bytes{{{PVC(j)}}})"),
       ref="D", instant=True, fmt="table"),
    vm(per_store(lambda p, j: f'label_replace({headroom(p, j)}, "limit", "{LIMIT[p]}", "", "")', "store, limit"),
       ref="E", instant=True, fmt="table"),
    vm(per_store(growth), ref="F", instant=True, fmt="table"),
    vm(per_store(days_left), ref="G", instant=True, fmt="table")],
    [{"id": "merge", "options": {}},
     {"id": "organize", "options": {"excludeByName": {"Time": True, "Value #A": True},
                                    "indexByName": {f: i for i, (f, _) in enumerate(STORE_COLUMNS)},
                                    "renameByName": dict(STORE_COLUMNS)}}],
    overrides=[by_name("Store", [{"id": "custom.width", "value": 150}]),
               by_name("Retention", [{"id": "custom.width", "value": 100}]),
               by_name("Oldest data", [{"id": "unit", "value": "dtdurations"}, {"id": "decimals", "value": 1},
                                       {"id": "noValue", "value": "whole months (partitions)"}]),
               by_name("Disk", [{"id": "unit", "value": "bytes"}, {"id": "custom.width", "value": 100}]),
               by_name("Used", [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 1},
                                {"id": "custom.width", "value": 90}]),
               by_name("Limit", [{"id": "custom.width", "value": 210}]),
               by_name("Room before the limit", [{"id": "unit", "value": "bytes"}]),
               by_name("Growth per day", [{"id": "unit", "value": "bytes"}]),
               by_name("Days until the limit", [{"id": "decimals", "value": 0}, {"id": "mappings", "value": DAYS_MAP},
                                                {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                                {"id": "thresholds", "value": DAYS_TH}])],
    desc="Configured retention against the oldest data actually kept, the disk, and how long until each store hits "
         "its limit at the growth of the last day. VMSingle turns read-only below 2 GB free (DiskRunsOutOfSpaceIn3Days "
         "warns). VLSingle and VTSingle then drop their oldest days instead: the oldest data shrinks below the "
         "retention (RetentionCutByDiskCap alerts) and growing the disk (storage in vm-k8s-stack.yaml) brings it back."),
      24, 6)
d.panels[-1]["links"] = links("vmsingle", "vlsingle", "vtsingle", "volumes")
with_links(d.add(ts("Room before the limit", [vm(per_store(headroom), "{{store}}")], "bytes", min_=0,
                    desc="Free space left before each store's limit; a steady fall shows the growth."), 12, 7),
           "vmsingle", "vlsingle", "vtsingle")
with_links(d.add(bargauge("Volumes in monitoring",
                          vm(f"max by (persistentvolumeclaim) (1 - kubelet_volume_stats_available_bytes{{{NS}}} "
                             f"/ kubelet_volume_stats_capacity_bytes{{{NS}}})", "{{persistentvolumeclaim}}", instant=True),
                          "percentunit", thresholds(("green", None), ("orange", 0.75), ("red", 0.9)),
                          "Every persistent volume of the stack: the three stores, the agents' disk queues, "
                          "Alertmanager and Grafana. VLSingle and VTSingle stay around 80% once full (by design).",
                          max_=1), 12, 7), "volumes")

d.row("Alerting — rules and notifications")
p = d.add(stat("Watchdog", 'max(ALERTS{alertname="Watchdog", alertstate="firing"})', "short",
               thresholds(("red", None), ("green", 1)), "The always-firing Watchdog alert: firing means vmalert "
               "evaluates rules and writes their state. Not firing means the alerting pipeline itself is down.",
               decimals=0, no_value="Not firing"), 4, 7)
p["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {"1": {"text": "Firing", "index": 0}}}]
with_links(d.add(stat("Alertmanager peers", "max(alertmanager_cluster_members)", "short",
                      thresholds(("red", None), ("orange", 1), ("green", 2)),
                      "Members of the Alertmanager gossip cluster (2 replicas). With 1, notifications still go out "
                      "but are not deduplicated across replicas.", decimals=0), 4, 7), "alertmanager")
with_links(d.add(per_interval(ts("Rule errors and missed evaluations",
                                 [vm("max by (group) (sum by (group, pod) (increase({__name__=~\"vmalert_(alerting|recording)_rules_errors_total\"}"
                                     "[$__interval]))) > 0", "{{group}} errors"),
                                  vm("max by (group) (sum by (group, pod) (increase(vmalert_iteration_missed_total[$__interval]))) > 0",
                                     "{{group}} missed", "B")],
                                 "short", stack=True, bars=True, decimals=0, legend_calcs=("sum",),
                                 desc="Rule groups whose evaluation failed (query error, datasource down) or ran late "
                                      "(an evaluation took longer than the interval).")), 8, 7), "vmalert")
with_links(d.add(ts("Notifications", [
    vm(f"sum by (integration) (rate(alertmanager_notifications_total{RI})) > 0", "{{integration}} sent"),
    vm(f"sum by (integration, reason) (rate(alertmanager_notifications_failed_total{RI})) > 0",
       "{{integration}} failed ({{reason}})", "B"),
    vm(f"sum(rate(vmalert_alerts_send_errors_total{RI})) > 0", "vmalert → Alertmanager errors", "C")],
    "short", min_=0, overrides=[{"matcher": {"id": "byRegexp", "options": ".*(failed|errors).*"},
                                "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}],
    desc="Notifications Alertmanager sent and failed per integration, and alerts vmalert could not deliver to "
         "Alertmanager."), 8, 7), "alertmanager", "vmalert")

d.row("DNS (kube-dns)")
DNS_ERRORS = "skydns_skydns_dns_error_count_total"
d.add(ts("DNS requests", [vm(f"sum by (system) (rate(skydns_skydns_dns_request_count_total{RI})) > 0", "{{system}} requests"),
                          vm(f'sum(rate({DNS_ERRORS}{{cause="nxdomain"}}{RI}))', "NXDOMAIN", "B"),
                          vm(f'sum by (cause) (rate({DNS_ERRORS}{{cause!="nxdomain"}}{RI})) > 0', "{{cause}} errors", "C")],
         "reqps", min_=0, overrides=[{"matcher": {"id": "byRegexp", "options": ".* errors"},
                                      "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}],
         desc="Queries kube-dns answers. NXDOMAIN is mostly normal: with ndots 5 every external name is first tried "
              "with the cluster search domains. Other errors (server failures) mean resolution breaks; a failing "
              "name such as an exporter endpoint shows up as \"no such host\" in the logs."), 8, 7)
d.add(ts("DNS latency", [vm(f"histogram_quantile({q}, sum by (le) (rate(skydns_skydns_dns_request_duration_seconds_bucket{RI})))",
                            f"p{int(q * 100)}", chr(65 + i)) for i, q in enumerate((0.5, 0.99))],
         "s", min_=0, desc="Time kube-dns takes to answer."), 8, 7)
d.add(ts("kube-dns health probes",
         [vm(f"histogram_quantile(0.99, sum by (le) (rate(kubedns_probe_kubedns_latency_ms_bucket{RI}))) / 1000", "kubedns p99"),
          vm(f"histogram_quantile(0.99, sum by (le) (rate(kubedns_probe_dnsmasq_latency_ms_bucket{RI}))) / 1000", "dnsmasq p99", "B"),
          vm(f"sum(rate(kubedns_probe_kubedns_errors{RI})) + sum(rate(kubedns_probe_dnsmasq_errors{RI}))", "probe errors / s", "C")],
         "s", min_=0, overrides=[right_axis("C", "short")],
         desc="The kube-dns sidecar probes its own kubedns and dnsmasq containers: latency (left) and failed probes "
              "(right)."), 8, 7)

d.row("Pipeline components")
RESTARTS = f"sum by (pod, container) (round(increase(kube_pod_container_status_restarts_total{{{NS}}}[$__range]))) > 0"
with_links(d.add(table("Restarts in the time range",
                       [vm(f"max by (pod, container, reason) (max_over_time(kube_pod_container_status_last_terminated_reason{{{NS}}}[$__range])) "
                           f"* on (pod, container) group_left () ({RESTARTS}) or on (pod, container) ({RESTARTS})",
                           instant=True, fmt="table")],
                       [{"id": "organize", "options": {"excludeByName": {"Time": True},
                                                       "indexByName": {"pod": 0, "container": 1, "reason": 2, "Value": 3},
                                                       "renameByName": {"pod": "Pod", "container": "Container",
                                                                        "reason": "Termination reason", "Value": "Restarts"}}}],
                       overrides=[COUNT("Restarts", 80)], sort=[{"displayName": "Restarts", "desc": True}],
                       defaults={"noValue": "No restart", "custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                       desc="Containers of the stack that restarted (OOMKilled: memory limit; Error: crash or failed "
                            "liveness probe). A store restart pauses ingestion and queries for its duration."),
                 12, 8), "operator", "events")
d.add(bargauge("Memory against the limit, top 12 pods",
               vm(f'topk(12, sum by (pod) (container_memory_working_set_bytes{{{NS}, container!=""}}) '
                  f'/ sum by (pod) (kube_pod_container_resource_limits{{{NS}, resource="memory"}}))', "{{pod}}", instant=True),
               "percentunit", thresholds(("green", None), ("orange", 0.8), ("red", 0.9)),
               "Working set memory of the stack's pods against their limit; at 100% the container is OOM-killed.",
               max_=1), 12, 8)

for p in d.panels:  # instant bar gauges follow the time picker's end
    if p["type"] == "bargauge":
        p["maxDataPoints"] = 1

pipeline = dashboard(UID, "Observability — Pipeline health", ["platform", "observability"], d.panels, [],
                     "Is the observability stack collecting, storing and alerting? Scrape targets, ingestion of "
                     "metrics, logs and spans, dropped data, disk and retention per store, rule evaluation and "
                     "notifications, DNS, and the stack's own restarts and memory, each linking to the component's "
                     "dashboard.", "now-6h")
pipeline["links"] = [
    {"title": "Component dashboards", "type": "dashboards", "tags": ["vm-k8s-stack"], "asDropdown": True,
     "includeVars": False, "keepTime": True},
    {"title": "Platform — GitOps & Secrets", "type": "link", "url": "/d/platform-gitops-secrets", "keepTime": True},
]
pipeline["annotations"] = {"list": restart_annotations(NS)}
DASHBOARD = pipeline
