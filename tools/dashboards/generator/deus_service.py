from .common import (CADVISOR, RI, VL, VM, VT, by_name, code_colors, Dash, dashboard, describe, level_colors,
    logs_panel, right_axis, stat, table, thresholds, ts, vl, vm)
from .deus import ANNOTATIONS, APP, K8S_NS, LINKS_DEUS, LOGS

FILE = "deus-service.json"

BURN = "/ on (target, slo, kind) (1 - slo:objective:ratio)"
S = f'{APP}, service_name="$service", http_route!~"/openapi.*"'
SVC = f'{APP}, service_name="$service"'
POD = f'{K8S_NS}, pod=~"$service-[a-z0-9]+-[a-z0-9]+"'
REQ = "http_server_request_duration_seconds"
LOGS_SVC = f'{LOGS} service.name:="$service"'
LOGS_LINK = [{"title": "Open in Deus — Logs", "url": "/d/deus-logs/deus-logs?var-service=$service&${__url_time_range}"}]
DB_LINK = [{"title": "Open CloudNativePG dashboard", "url": "/d/cloudnative-pg/cloudnativepg?${__url_time_range}"}]

d = Dash()
d.row("SLO — $service")
d.add(stat("Availability (30d)", '1 - slo:sli_error:ratio_rate30d{target="$service", slo="availability"}', "percentunit",
           thresholds(("red", None), ("orange", 0.99), ("green", 0.995)), decimals=3), 4, 4)
d.add(stat("Availability budget left", 'slo:error_budget_remaining:ratio{target="$service", slo="availability"}', "percentunit",
           thresholds(("red", None), ("orange", 0), ("green", 0.25)), decimals=1), 4, 4)
d.add(stat("Latency SLI (30d)", '1 - slo:sli_error:ratio_rate30d{target="$service", slo="latency"}', "percentunit",
           thresholds(("red", None), ("orange", 0.98), ("green", 0.99)), decimals=3,
           desc="Share of requests faster than the service latency threshold."), 4, 4)
d.add(stat("Latency budget left", 'slo:error_budget_remaining:ratio{target="$service", slo="latency"}', "percentunit",
           thresholds(("red", None), ("orange", 0), ("green", 0.25)), decimals=1), 4, 4)
d.add(stat("Burn rate 1h (worst SLI)", f'max(slo:sli_error:ratio_rate1h{{target="$service"}} {BURN})', "x",
           thresholds(("green", None), ("orange", 1), ("red", 14.4))), 4, 4)
d.add(stat("Requests / s", f"sum(rate({REQ}_count{{{S}}}[5m]))", "reqps", thresholds(("blue", None))), 4, 4)

d.row("Traffic and errors")
d.add(ts("Requests by route", [vm(f"sum by (http_request_method, http_route) (rate({REQ}_count{{{S}}}{RI}))",
                                  "{{http_request_method}} {{http_route}}")], "reqps"), 8, 8)
d.add(ts("Responses by status code", [vm(f"sum by (http_response_status_code) (rate({REQ}_count{{{S}}}{RI}))",
                                         "{{http_response_status_code}}")], "reqps", stack=True, overrides=code_colors(),
         desc="4xx are the caller's mistakes or business rules (validation, unknown document, closed period) and do not "
              "count against the SLO."), 8, 8)
d.add(ts("5xx ratio by route", [vm(
    f'(sum by (http_route) (rate({REQ}_count{{{S}, http_response_status_code=~"5.."}}{RI}))'
    f" or sum by (http_route) (rate({REQ}_count{{{S}}}{RI})) * 0) / sum by (http_route) (rate({REQ}_count{{{S}}}{RI}))",
    "{{http_route}}")], "percentunit", decimals=2, min_=0, soft_max=0.05), 8, 8)

d.row("Latency")
d.add(ts("Latency percentiles", [
    vm(f"histogram_quantile({q}, sum by (le) (rate({REQ}_bucket{{{S}}}{RI})))", f"p{int(q * 100)}", ref)
    for q, ref in ((0.5, "A"), (0.95, "B"), (0.99, "C"))], "s"), 8, 8)
d.add(ts("p95 latency by route", [vm(
    f"histogram_quantile(0.95, sum by (http_route, le) (rate({REQ}_bucket{{{S}}}{RI})))", "{{http_route}}")], "s"), 8, 8)
d.add(ts("Slower than the SLO threshold — by route", [vm(
    'clamp_min(1 - histogram_share(scalar(max(slo:sli_threshold:seconds{target="$service", slo="latency"})),'
    f" sum by (http_route, le) (rate({REQ}_bucket{{{S}}}{RI}))), 0)", "{{http_route}}")],
    "percentunit", th=thresholds(("green", None), ("red", 0.01)), min_=0, soft_max=0.02,
    desc="Latency SLI per route: share of requests slower than the service threshold. The dashed line is the 1% the "
         "SLO allows; one slow route shows up here even when the service average looks fine."), 8, 8)
d.add({"type": "heatmap", "title": "Latency distribution", "datasource": VM,
       "description": "Requests per latency bucket; a band moving up means the whole service slowed down, a second band "
                      "means a slow subset of requests.",
       "options": {"calculate": False, "yAxis": {"unit": "s", "axisPlacement": "left"},
                   "color": {"mode": "scheme", "scheme": "Oranges", "steps": 64, "exponent": 0.5, "fill": "dark-orange"},
                   "cellGap": 1, "showValue": "never", "tooltip": {"mode": "single", "yHistogram": True},
                   "legend": {"show": True}, "rowsFrame": {"layout": "auto"}},
       "fieldConfig": {"defaults": {"custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}}}, "overrides": []},
       "targets": [vm(f"sum by (le) (increase({REQ}_bucket{{{S}}}{RI}))", "{{le}}", fmt="heatmap")]}, 24, 8)

CLIENT = "http_client_request_duration_seconds"
d.row("Dependencies")
d.add(ts("Outgoing calls by peer and status",
         [vm(f"sum by (peer_service, http_response_status_code) (rate({CLIENT}_count{{{SVC}}}{RI}))",
             "{{peer_service}} {{http_response_status_code}}")], "reqps",
         desc="Calls this service makes to the /internal endpoints of other services (empty for leaf services)."), 8, 8)
d.add(ts("Outgoing p95 latency by peer",
         [vm(f"histogram_quantile(0.95, sum by (peer_service, le) (rate({CLIENT}_bucket{{{SVC}}}{RI})))", "{{peer_service}}")],
         "s"), 8, 8)
d.add(ts("Incoming calls by caller",
         [vm(f'sum by (service_name, http_response_status_code) (rate({CLIENT}_count{{{APP}, peer_service="$service"}}{RI}))',
             "{{service_name}} {{http_response_status_code}}")], "reqps",
         desc="Which services call this one, as seen by the callers. Browser traffic through the gateway is in Traffic "
              "and errors."), 8, 8)

d.row("Outbox — $service")
d.add(ts("Events by destination and outcome",
         [vm(f"sum by (messaging_destination_name, outbox_outcome) (rate(outbox_events_total{{{SVC}}}{RI}))",
             "{{messaging_destination_name}} {{outbox_outcome}}")], "ops", stack=True,
         desc="Events written with the business transaction and delivered by the relay: journals to the ledger, "
              "allocations, emails. Failed deliveries are retried with backoff; dead ones are not."), 8, 8)
d.add(ts("Waiting and dead events", [
    vm(f'max by (outbox_state) (outbox_backlog{{{SVC}}})', "{{outbox_state}}")], "short", decimals=0, min_=0,
    th=thresholds(("green", None), ("red", 1)),
    desc="pending: written but not delivered yet. dead: rejected by the receiver or out of attempts; the receiving "
         "service is missing these documents until someone acts (DeusOutboxDeadEvents)."), 8, 8)
d.add(ts("Delivery delay p95", [
    vm(f"histogram_quantile(0.95, sum by (messaging_destination_name, le) (rate(outbox_delivery_delay_seconds_bucket{{{SVC}}}{RI})))",
       "{{messaging_destination_name}}")], "s",
    desc="Time from the business transaction to the delivery: how late the ledger, the other document or the email "
         "sees the change."), 8, 8)

DB = "db_client_operation_duration_seconds"
SQL_OPS = f'{SVC}, db_system_name="postgresql"'
CACHE_OPS = f'{SVC}, db_system_name="redis"'
d.row("Database — $service")
for p in (
    d.add(ts("Connection pool", [
        vm(f"sum by (db_client_connection_state) (db_client_connection_count{{{SVC}}})", "{{db_client_connection_state}}"),
        vm(f"sum(db_client_connection_pending_requests{{{SVC}}})", "waiting for a connection", "B")],
        "short", decimals=0, min_=0, overrides=[right_axis("B", "short")],
        desc="Connections of the service's pools by state (DB_MAX_CONNECTIONS per pod) and requests waiting for a free "
             "connection (right). Waiting requests mean the pool is too small or queries hold connections too long."), 8, 8),
    d.add(ts("Operations by type", [
        vm(f"sum by (db_operation_name) (rate({DB}_count{{{SQL_OPS}}}{RI}))", "{{db_operation_name}}")], "ops", stack=True,
        desc="Statements and transaction commands per second as the service sends them."), 8, 8),
    d.add(ts("p95 duration by operation", [
        vm(f"histogram_quantile(0.95, sum by (db_operation_name, le) (rate({DB}_bucket{{{SQL_OPS}}}{RI})))", "{{db_operation_name}}")],
        "s", desc="Client-side duration: network, lock waits and execution together. The slow statements themselves are "
                  "in Deus — Traces (SQL statements)."), 8, 8),
):
    p["links"] = DB_LINK

d.row("Cache — $service")
d.add(ts("Cache hit ratio", [
    vm(f'sum(rate(cache_lookups_total{{{SVC}, cache_result="hit"}}{RI})) / sum(rate(cache_lookups_total{{{SVC}}}{RI}))', "hit ratio")],
    "percentunit", min_=0, max_=1, decimals=1,
    desc="Reads answered by Redis. Empty for services that do not use the cache; low after a deploy or an "
         "invalidation, then rising."), 8, 8)
d.add(ts("Cache lookups by result", [
    vm(f"sum by (cache_result) (rate(cache_lookups_total{{{SVC}}}{RI}))", "{{cache_result}}")], "ops", stack=True,
    desc="hit: served from Redis; miss: read from PostgreSQL and stored; error: Redis unreachable or slower than the "
         "timeout, read from PostgreSQL."), 8, 8)
d.add(ts("p95 duration by Redis command", [
    vm(f"histogram_quantile(0.95, sum by (db_operation_name, le) (rate({DB}_bucket{{{CACHE_OPS}}}{RI})))", "{{db_operation_name}}")],
    "s", desc="Client-side duration of the Redis commands the service sends: GET and SET for the cache, GETDEL and EVAL "
              "for sessions and rate limits."), 8, 8)

d.row("Saturation")
d.add(ts("CPU per pod", [
    vm(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{CADVISOR}, {POD}, container="app"}}{RI}))', "{{pod}}"),
    vm(f'max(kube_pod_container_resource_requests{{{POD}, container="app", resource="cpu"}})', "request", "B")],
    "cores", decimals=3), 6, 8)
d.add(ts("Memory per pod", [
    vm(f'sum by (pod) (container_memory_working_set_bytes{{{CADVISOR}, {POD}, container="app"}})', "{{pod}}"),
    vm(f'max(kube_pod_container_resource_limits{{{POD}, container="app", resource="memory"}})', "limit", "B")],
    "bytes"), 6, 8)
d.add(ts("JavaScript heap per pod", [
    vm(f"sum by (k8s_pod_name) (process_runtime_heap_used_bytes{{{SVC}}})", "{{k8s_pod_name}}")], "bytes",
    desc="Heap in use reported by each Bun process. A heap that only grows between deploys is a leak."), 6, 8)
d.add(ts("Container restarts", [
    vm(f"sum by (pod) (increase(kube_pod_container_status_restarts_total{{{POD}}}[10m]))", "{{pod}}")],
    "short", bars=True, decimals=0, min_=0, soft_max=3,
    desc="Restarts in the previous 10 minutes (OOMKilled, failed liveness probe, crash)."), 6, 8)

d.row("Logs — $service")
d.add(ts("Log volume by level",
         [vl(f"{LOGS_SVC} | stats by (severity_text) count() logs", "statsRange", legend="{{severity_text}}")],
         "short", ds=VL, stack=True, bars=True, overrides=level_colors(), legend_calcs=("sum",)), 12, 8)
d.add(table("Top WARN and ERROR messages",
            [vl(f"{LOGS_SVC} severity_text:in(WARN, ERROR) | stats by (severity_text, _msg, exception.message) count() hits "
                "| sort by (hits desc) | limit 10", "stats")],
            [{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}},
             {"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True},
                                            "indexByName": {"severity_text": 0, "_msg": 1, "exception.message": 2, "Value": 3},
                                            "renameByName": {"severity_text": "Level", "_msg": "Message",
                                                             "exception.message": "Cause", "Value": "Count", "hits": "Count"}}}],
            overrides=[by_name("Message", [{"id": "links", "value": LOGS_LINK}])],
            sort=[{"displayName": "Count", "desc": True}], ds=VL), 12, 8)
d.add(logs_panel("WARN and ERROR logs — $service", f"{LOGS_SVC} severity_text:in(WARN, ERROR)",
                 "Expand a line and follow trace_id to open the trace."), 24, 10)

d.row("Traces — $service")
TRACE_TABLE = dict(overrides=[], sort=[{"displayName": "Duration", "desc": True}], ds=VT)
TRACES_LINK = [{"title": "Open Deus — Traces for $service", "url": "/d/deus-traces/deus-traces?var-service=$service&${__url_time_range}"}]
for p in (
    d.add(table("Slowest traces",
                [{"datasource": VT, "refId": "A", "queryType": "search", "service": "$service",
                  "minDuration": "$min_duration", "limit": 20}], [],
                desc="Traces through $service slower than the selected minimum duration.", **TRACE_TABLE), 12, 9),
    d.add(table("Traces with errors",
                [{"datasource": VT, "refId": "A", "queryType": "search", "service": "$service",
                  "tags": "error=true", "limit": 20}], [], desc="Traces through $service with a span marked as error.",
                **TRACE_TABLE), 12, 9),
):
    p["links"] = TRACES_LINK

SVC_VAR_QUERY = f"label_values({REQ}_count{{{APP}}}, service_name)"
SERVICE_VAR = {"name": "service", "label": "Service", "type": "query", "datasource": VM,
               "query": {"query": SVC_VAR_QUERY, "refId": "service"}, "definition": SVC_VAR_QUERY,
               "refresh": 2, "sort": 1, "multi": False, "includeAll": False,
               "current": {"selected": True, "text": "sales", "value": "sales"}}
MIN_DUR = {"name": "min_duration", "label": "Slow trace threshold", "type": "custom",
           "query": "100ms,250ms,500ms,1s,2s", "current": {"selected": True, "text": "500ms", "value": "500ms"},
           "options": [{"selected": v == "500ms", "text": v, "value": v} for v in ("100ms", "250ms", "500ms", "1s", "2s")]}
describe(d.panels, {
    "Availability (30d)": "Share of this service's requests answered without a 5xx over the 30-day SLO window.",
    "Availability budget left": "Error budget of the availability SLO still unspent in the 30-day window: 100% none "
                                "used, 0% the SLO is met exactly, below 0 it is violated.",
    "Latency budget left": "Error budget of the latency SLO (requests slower than the threshold) still unspent in "
                           "the 30-day window.",
    "Burn rate 1h (worst SLI)": "How fast the worst SLI of this service spends its budget over the last hour: 1 "
                                "spends it in exactly 30 days, 14.4 pages (2% of the budget in one hour).",
    "Requests / s": "Requests this service answers per second (5-minute average).",
    "Requests by route": "Requests per second by HTTP method and route pattern; /internal routes are calls from other services.",
    "5xx ratio by route": "Share of each route's requests answered with a 5xx (server error).",
    "Latency percentiles": "Server-side response time of the whole service: median, 95th and 99th percentile.",
    "p95 latency by route": "95th percentile response time per route: which endpoint is slow.",
    "Outgoing p95 latency by peer": "95th percentile duration of this service's calls to other services: slowness "
                                    "that comes from a dependency.",
    "CPU per pod": "CPU used by the app container of each pod, against its request. Above the request the pod "
                   "competes for CPU with its neighbours.",
    "Memory per pod": "Working set memory of the app container of each pod, against its limit: at the limit the "
                      "container is OOM-killed.",
    "Log volume by level": "Log records of this service per interval by level.",
    "Top WARN and ERROR messages": "The 10 most frequent WARN and ERROR messages of this service and their cause.",
})
DASHBOARD = dashboard(
    "deus-service", "Deus — Service", ["deus", "red"], d.panels, [SERVICE_VAR, MIN_DUR],
    "One deus service end to end: SLO, RED, latency, dependencies, outbox, database, saturation, logs and traces.",
    "now-1h", links=LINKS_DEUS, annotations=ANNOTATIONS)
