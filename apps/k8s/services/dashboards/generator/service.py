"""Shop — Service: one service end to end (SLO, RED, latency, dependencies, database, saturation, logs, traces)."""
from .common import (APP_LOGS, BURN, NS, RI, VL, VM, VT, by_name, code_colors, Dash, dashboard, level_colors,
    logs_panel, right_axis, stat, table, thresholds, ts, var_service, vl, vm)

FILE = "shop-service.json"

d = Dash()
S = f'{NS}, job="$service"'
POD = 'pod=~"$service-[a-z0-9]+-[a-z0-9]+"'
LOGS_LINK = [{"title": "Open in Shop — Logs", "url": "/d/shop-logs/shop-logs?var-service=$service&${__url_time_range}"}]

d.row("SLO — $service")
d.add(stat("Availability (30d)", '1 - slo:sli_error:ratio_rate30d{job="$service", slo="availability"}', "percentunit",
           thresholds(("red", None), ("orange", 0.99), ("green", 0.995)), decimals=3), 4, 4)
d.add(stat("Availability budget left", 'slo:error_budget_remaining:ratio{job="$service", slo="availability"}', "percentunit",
           thresholds(("red", None), ("orange", 0), ("green", 0.25)), decimals=1), 4, 4)
d.add(stat("Latency SLI (30d)", '1 - slo:sli_error:ratio_rate30d{job="$service", slo="latency"}', "percentunit",
           thresholds(("red", None), ("orange", 0.98), ("green", 0.99)), decimals=3,
           desc="Share of requests faster than the service latency threshold."), 4, 4)
d.add(stat("Latency budget left", 'slo:error_budget_remaining:ratio{job="$service", slo="latency"}', "percentunit",
           thresholds(("red", None), ("orange", 0), ("green", 0.25)), decimals=1), 4, 4)
d.add(stat("Burn rate 1h (worst SLI)", f'max(slo:sli_error:ratio_rate1h{{job="$service"}} {BURN})', "x",
           thresholds(("green", None), ("orange", 1), ("red", 14.4))), 4, 4)
d.add(stat("Requests / s", f'sum(rate(http_server_requests_total{{{S}}}[5m]))', "reqps",
           thresholds(("blue", None))), 4, 4)

d.row("Traffic and errors")
d.add(ts("Requests by route", [vm(f'sum by (method, route) (rate(http_server_requests_total{{{S}}}{RI}))', "{{method}} {{route}}")],
         "reqps"), 8, 8)
d.add(ts("Responses by status code", [vm(f'sum by (code) (rate(http_server_requests_total{{{S}}}{RI}))', "{{code}}")],
         "reqps", stack=True, overrides=code_colors(),
         desc="4xx are client or business outcomes (unknown user, payment declined) and do not count against the SLO."), 8, 8)
d.add(ts("5xx ratio by route", [vm(
    f'(sum by (route) (rate(http_server_requests_total{{{S}, code=~"5.."}}{RI})) or sum by (route) (rate(http_server_requests_total{{{S}}}{RI})) * 0)'
    f' / sum by (route) (rate(http_server_requests_total{{{S}}}{RI}))', "{{route}}")], "percentunit", decimals=2, min_=0, soft_max=0.05), 8, 8)

d.row("Latency")
d.add(ts("Latency percentiles", [
    vm(f'histogram_quantile({q}, sum by (le) (rate(http_server_request_duration_seconds_bucket{{{S}}}{RI})))', f"p{int(q * 100)}", ref)
    for q, ref in ((0.5, "A"), (0.95, "B"), (0.99, "C"))], "s"), 8, 8)
d.add(ts("p95 latency by route", [vm(
    f'histogram_quantile(0.95, sum by (route, le) (rate(http_server_request_duration_seconds_bucket{{{S}}}{RI})))', "{{route}}")], "s"), 8, 8)
d.add(ts("Slower than the SLO threshold — by route", [vm(
    'clamp_min(1 - histogram_share(scalar(max(slo:sli_threshold:seconds{job="$service", slo="latency"})),'
    f' sum by (route, le) (rate(http_server_request_duration_seconds_bucket{{{S}}}{RI}))), 0)', "{{route}}")],
    "percentunit", th=thresholds(("green", None), ("red", 0.01)), min_=0, soft_max=0.02,
    desc="Latency SLI per route: share of requests slower than the service threshold. The dashed line is the "
         "1% the SLO allows; a single slow route shows up here even when the service average looks fine."), 8, 8)
d.add({"type": "heatmap", "title": "Latency distribution", "datasource": VM,
       "description": "Requests per latency bucket; a band moving up means the whole service slowed down, a second band means a slow subset.",
       "options": {"calculate": False, "yAxis": {"unit": "s", "axisPlacement": "left"},
                   "color": {"mode": "scheme", "scheme": "Oranges", "steps": 64, "exponent": 0.5, "fill": "dark-orange"},
                   "cellGap": 1, "showValue": "never", "tooltip": {"mode": "single", "yHistogram": True},
                   "legend": {"show": True}, "rowsFrame": {"layout": "auto"}},
       "fieldConfig": {"defaults": {"custom": {"hideFrom": {"legend": False, "tooltip": False, "viz": False}}}, "overrides": []},
       "targets": [vm(f'sum by (le) (increase(http_server_request_duration_seconds_bucket{{{S}}}{RI}))', "{{le}}", fmt="heatmap")]},
      24, 8)

d.row("Dependencies")
d.add(ts("Outgoing calls by peer and status",
         [vm(f'sum by (peer, code) (rate(http_client_requests_total{{{S}}}{RI}))', "{{peer}} {{code}}")], "reqps",
         desc="Calls this service makes to other services (empty for leaf services)."), 8, 8)
d.add(ts("Outgoing p95 latency by peer",
         [vm(f'histogram_quantile(0.95, sum by (peer, le) (rate(http_client_request_duration_seconds_bucket{{{S}}}{RI})))', "{{peer}}")],
         "s"), 8, 8)
d.add(ts("Incoming calls by caller",
         [vm(f'sum by (job, code) (rate(http_client_requests_total{{{NS}, peer="$service"}}{RI}))', "{{job}} {{code}}")], "reqps",
         desc="Which services call this one, as seen by the callers (api-gateway traffic is not instrumented client-side)."), 8, 8)

# PostgreSQL as this service sees it: its connection pool (pgxpool_*), its sessions in PostgreSQL and the
# errors PostgreSQL logged for them (sessions carry application_name = service). Empty for services
# without a database (api-gateway, notification-service).
PG_LOGS = '{kubernetes.pod_namespace="shop", kubernetes.container_name="postgres"}'
DB_LINK = [{"title": "Open CloudNativePG dashboard", "url": "/d/cloudnative-pg-shop/cloudnativepg?${__url_time_range}"}]
d.row("Database — $service")
for p in (
    d.add(ts("Connection pool", [
        vm(f'sum by (pod) (pgxpool_acquired_connections{{{S}}})', "in use {{pod}}"),
        vm(f'sum by (pod) (pgxpool_idle_connections{{{S}}})', "idle {{pod}}", "B"),
        vm(f'max(pgxpool_max_connections{{{S}}})', "max per pod", "C")], "short", decimals=0, min_=0,
        desc="Connections of each pod's pool (DB_MAX_CONNS). In use at the maximum means requests queue for a connection."), 6, 8),
    d.add(ts("Waiting for a connection", [
        vm(f'sum by (pod) (rate(pgxpool_empty_acquires_total{{{S}}}{RI}))', "waits {{pod}}"),
        vm(f'sum by (pod) (rate(pgxpool_empty_acquire_wait_seconds_total{{{S}}}{RI}))'
           f' / sum by (pod) (rate(pgxpool_empty_acquires_total{{{S}}}{RI}))', "average wait {{pod}}", "B"),
        vm(f'sum by (pod) (rate(pgxpool_canceled_acquires_total{{{S}}}{RI}))', "gave up {{pod}}", "C")], "ops", min_=0,
        overrides=[right_axis("B", "s")],
        desc="Acquisitions that found no idle connection and waited (left, per second), how long they waited "
             "(right) and how many gave up because the request timed out first."), 6, 8),
    d.add(ts("Sessions in PostgreSQL", [
        vm(f'sum by (state) (cnpg_backends_total{{{NS}, application_name="$service"}})', "{{state}}")],
        "short", stack=True, decimals=0, min_=0,
        desc="This service's sessions on the database by state. \"idle in transaction\" that persists is a "
             "transaction left open: it holds locks (ended by PostgreSQL after 30s for the services)."), 6, 8),
    d.add(ts("PostgreSQL errors by SQLSTATE", [
        vl(f'{PG_LOGS} record.application_name:in($service) record.error_severity:in(ERROR, FATAL) '
           '| stats by (record.sql_state_code) count() errors', "statsRange", legend="{{record.sql_state_code}}")],
        "short", ds=VL, stack=True, bars=True, decimals=0, legend_calcs=("sum",),
        desc="Errors PostgreSQL logged for this service's sessions: 55P03 lock timeout (another transaction holds "
             "the row), 57014 cancelled (statement_timeout, or the service gave up), 23505 duplicate key, 40P01 "
             "deadlock. Details in the CloudNativePG dashboard (Queries and errors, Locks and blocking)."), 6, 8),
):
    p["links"] = DB_LINK

d.row("Saturation")
d.add(ts("CPU per pod", [
    vm(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{NS}, {POD}, container="app"}}{RI}))', "{{pod}}"),
    vm(f'max(kube_pod_container_resource_requests{{{NS}, {POD}, container="app", resource="cpu"}})', "request", "B")],
    "cores", decimals=3), 6, 8)
d.add(ts("Memory per pod", [
    vm(f'sum by (pod) (container_memory_working_set_bytes{{{NS}, {POD}, container="app"}})', "{{pod}}"),
    vm(f'max(kube_pod_container_resource_limits{{{NS}, {POD}, container="app", resource="memory"}})', "limit", "B")],
    "bytes"), 6, 8)
d.add(ts("In-flight requests and goroutines", [
    vm(f'sum by (pod) (http_server_requests_inflight{{{S}}})', "in-flight {{pod}}"),
    vm(f'sum by (pod) (go_goroutines{{{S}}})', "goroutines {{pod}}", "B")], "short"), 6, 8)
d.add(ts("Container restarts", [
    vm(f'sum by (pod) (increase(kube_pod_container_status_restarts_total{{{NS}, {POD}}}[10m]))', "{{pod}}")],
    "short", bars=True, decimals=0, min_=0, soft_max=3, desc="Restarts in the previous 10 minutes (OOMKilled, failed liveness probe, crash)."), 6, 8)

d.row("Logs — $service")
d.add(ts("Log volume by level",
         [vl(f'{APP_LOGS} service:in($service) | stats by (level) count() logs', "statsRange", legend="{{level}}")],
         "short", ds=VL, stack=True, bars=True, overrides=level_colors(), legend_calcs=("sum",)), 12, 8)
d.add(table("Top WARN and ERROR messages",
            [vl(f'{APP_LOGS} service:in($service) level:in(WARN, ERROR) | stats by (level, _msg) count() hits | sort by (hits desc) | limit 10', "stats")],
            [{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}},
             {"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True},
                                            "indexByName": {"level": 0, "_msg": 1, "Value": 2},
                                            "renameByName": {"level": "Level", "_msg": "Message", "Value": "Count", "hits": "Count"}}}],
            overrides=[by_name("Message", [{"id": "links", "value": LOGS_LINK}])],
            sort=[{"displayName": "Count", "desc": True}], ds=VL), 12, 8)
d.add(logs_panel("WARN and ERROR logs — $service", f'{APP_LOGS} service:in($service) level:in(WARN, ERROR)',
                 "Expand a line and follow trace_id to open the trace."), 24, 10)

d.row("Traces — $service")
TRACE_TABLE = dict(overrides=[], sort=[{"displayName": "Duration", "desc": True}], ds=VT)
TRACES_LINK = [{"title": "Open Shop — Traces for $service", "url": "/d/shop-traces/shop-traces?var-service=$service&${__url_time_range}"}]
for p in (
    d.add(table("Slowest traces",
                [{"datasource": VT, "refId": "A", "queryType": "search", "service": "$service",
                  "minDuration": "$min_duration", "limit": 20}], [], desc="Traces through $service slower than the selected minimum duration.",
                **TRACE_TABLE), 12, 9),
    d.add(table("Traces with errors",
                [{"datasource": VT, "refId": "A", "queryType": "search", "service": "$service",
                  "tags": "error=true", "limit": 20}], [], desc="Traces through $service with a span marked as error.",
                **TRACE_TABLE), 12, 9),
):
    p["links"] = TRACES_LINK

MIN_DUR = {"name": "min_duration", "label": "Slow trace threshold", "type": "custom",
           "query": "100ms,250ms,500ms,1s,2s", "current": {"selected": True, "text": "250ms", "value": "250ms"},
           "options": [{"selected": v == "250ms", "text": v, "value": v} for v in ("100ms", "250ms", "500ms", "1s", "2s")]}
DASHBOARD = dashboard(
    "shop-service", "Shop — Service", ["shop", "red"], d.panels, [var_service(False), MIN_DUR],
    "One service end to end: SLO, RED, latency distribution, dependencies, database, saturation, logs and traces.", "now-1h")
