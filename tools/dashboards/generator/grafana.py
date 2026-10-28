from .common import CADVISOR, COUNT, RI, by_name, Dash, dashboard, stat, table, thresholds, ts, vm

FILE = "platform-grafana.json"
UID = "platform-grafana"

d = Dash()
G = 'job="grafana"'
HTTP = "grafana_http_request_duration_seconds"
PLUGIN = "grafana_plugin_request"
PODS = f"max by (namespace, pod) (up{{{G}}})"
REQ = f"sum(rate({HTTP}_count{{{G}}}[5m]))"
ERR5 = f'sum(rate({HTTP}_count{{{G}, status_code=~"5.."}}[5m]))'


def quantile(q, metric, by="", window=RI):
    group = ", ".join(x for x in (by, "le") if x)
    return f"histogram_quantile({q}, sum by ({group}) (rate({metric}_bucket{{{G}}}{window})))"


d.row("Overview")
for p in (
    stat("Up", f"max(up{{{G}}}) or vector(0)", "short", thresholds(("red", None), ("green", 1)),
         "Grafana scraped as up.", decimals=0),
    stat("Requests / s", f"{REQ} or vector(0)", "reqps", thresholds(("blue", None)),
         "HTTP requests per second to Grafana: pages, API calls and data source queries from the browser."),
    stat("5xx ratio", f"({ERR5} or vector(0)) / {REQ}", "percentunit",
         thresholds(("green", None), ("orange", 0.01), ("red", 0.05)), "Share of HTTP responses with status 5xx."),
    stat("p95 request time", quantile(0.95, HTTP, window="[5m]"), "s", thresholds(("green", None), ("orange", 1), ("red", 5)),
         "95th percentile HTTP request duration over the last 5 minutes."),
    stat("Failed data source queries", f'sum(increase({PLUGIN}_total{{{G}, status!="ok"}}[$__range])) or vector(0)', "short",
         thresholds(("green", None), ("orange", 1)),
         "Queries and resource calls to data source plugins that did not succeed in the time range.", decimals=0),
    stat("Active users", f"max(grafana_stat_active_users{{{G}}})", "short", thresholds(("blue", None)),
         "Users who used Grafana in the last 30 days.", decimals=0),
):
    d.add(p, 4, 4)

d.row("HTTP")
d.add(ts("Requests by status", [vm(f"sum by (status_code) (rate({HTTP}_count{{{G}}}{RI}))", "{{status_code}}")],
         "reqps", stack=True, min_=0, desc="HTTP requests per second by response status."), 12, 8)
d.add(ts("Request time", [vm(quantile(0.5, HTTP), "p50"), vm(quantile(0.95, HTTP), "p95", "B"),
                          vm(quantile(0.99, HTTP), "p99", "C")],
         "s", min_=0, desc="HTTP request duration percentiles over all handlers."), 12, 8)
d.add(table("Handlers", [vm(f"sum by (handler) (increase({HTTP}_count{{{G}}}[$__range]))", ref="A", instant=True, fmt="table"),
                         vm(f'sum by (handler) (increase({HTTP}_count{{{G}, status_code=~"5.."}}[$__range]))', ref="B",
                            instant=True, fmt="table"),
                         vm(quantile(0.95, HTTP, by="handler", window="[$__range]"), ref="C", instant=True, fmt="table")],
            [{"id": "merge", "options": {}},
             {"id": "organize", "options": {"excludeByName": {"Time": True},
                                            "indexByName": {"handler": 0, "Value #A": 1, "Value #B": 2, "Value #C": 3},
                                            "renameByName": {"handler": "Handler", "Value #A": "Requests",
                                                             "Value #B": "5xx", "Value #C": "p95"}}}],
            overrides=[COUNT("Requests"), COUNT("5xx"),
                       by_name("5xx", [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                       {"id": "thresholds", "value": thresholds(("green", None), ("red", 1))}]),
                       by_name("p95", [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 3}])],
            sort=[{"displayName": "p95", "desc": True}],
            desc="Every HTTP handler in the time range, slowest first: requests, 5xx responses and the 95th percentile "
                 "duration."), 24, 9)

d.row("Data source queries")
d.add(ts("Queries by data source",
         [vm(f"sum by (plugin_id, exported_endpoint, status) (rate({PLUGIN}_total{{{G}}}{RI}))",
             "{{plugin_id}} {{exported_endpoint}} {{status}}")],
         "reqps", stack=True, min_=0,
         desc="Calls from Grafana to its data source plugins (VictoriaMetrics, VictoriaLogs, VictoriaTraces): "
              "queryData runs panel queries, callResource serves variables and the query editors."), 12, 8)
d.add(ts("Query time by data source",
         [vm(quantile(0.95, f"{PLUGIN}_duration_seconds", by="plugin_id"), "p95 {{plugin_id}}"),
          vm(quantile(0.5, f"{PLUGIN}_duration_seconds", by="plugin_id"), "p50 {{plugin_id}}", "B")],
         "s", min_=0, desc="Time a data source plugin needs to answer, which includes the query in the backend."),
      12, 8)

d.row("Frontend")
d.add(ts("Page load time",
         [vm(quantile(0.5, "grafana_frontend_boot_load_time_seconds"), "p50 load"),
          vm(quantile(0.95, "grafana_frontend_boot_load_time_seconds"), "p95 load", "B"),
          vm(quantile(0.95, "grafana_frontend_boot_first_contentful_paint_time_seconds"), "p95 first contentful paint",
             "C")],
         "s", min_=0, desc="How long the Grafana web app takes to load in the users' browsers, as the browsers report it."),
      16, 7)
d.add(ts("Frontend boot errors", [vm(f"sum(increase(grafana_frontend_boot_errors_total{{{G}}}{RI}))", "errors")],
         "short", bars=True, min_=0, decimals=0, desc="Browser sessions in which the Grafana app failed to start."), 8, 7)

d.row("Database")
d.add(ts("Connections", [vm(f"max(grafana_database_conn_open{{{G}}})", "open"),
                         vm(f"max(grafana_database_conn_in_use{{{G}}})", "in use", "B"),
                         vm(f"max(grafana_database_conn_idle{{{G}}})", "idle", "C"),
                         vm(f"max(grafana_database_conn_max_open{{{G}}})", "max", "D")],
         "short", min_=0, decimals=0, desc="Connections of Grafana to its own database (dashboards, users, settings)."),
      12, 7)
d.add(ts("Waits for a connection", [vm(f"sum(rate(grafana_database_conn_wait_count_total{{{G}}}{RI}))", "waits / s"),
                                    vm(f"sum(rate(grafana_database_conn_wait_duration_seconds{{{G}}}{RI}))",
                                       "seconds waited / s", "B")],
         "short", min_=0, desc="Requests that had to wait because every database connection was busy."), 12, 7)

d.row("Content and users")
d.add(ts("Content", [vm(f'max by (__name__) ({{{G}, __name__=~"grafana_stat_totals_(dashboard|folder|datasource|annotations|library_panels|alert_rules)"}})',
                        "{{__name__}}")],
         "short", min_=0, decimals=0,
         desc="Dashboards, folders, data sources, annotations, library panels and alert rules stored in Grafana."), 12, 7)
d.add(ts("Users", [vm(f'max by (__name__) ({{{G}, __name__=~"grafana_stat_(active_users|totals_(admins|editors|viewers))"}})',
                      "{{__name__}}")],
         "short", min_=0, decimals=0, desc="Users by role, and the users active in the last 30 days."), 12, 7)

d.row("Resources")
d.add(ts("Memory by container",
         [vm(f'sum by (container) (container_memory_working_set_bytes{{{CADVISOR}, container!="", container!="POD"}} '
             f"* on (namespace, pod) group_left () {PODS})", "{{container}}")],
         "bytes", stack=True, min_=0, desc="Working set of the Grafana pod: Grafana and its dashboard and datasource "
                                           "sidecars."), 12, 7)
d.add(ts("CPU by container",
         [vm(f'sum by (container) (rate(container_cpu_usage_seconds_total{{{CADVISOR}, container!="", container!="POD"}}{RI}) '
             f"* on (namespace, pod) group_left () {PODS})", "{{container}}")],
         "short", stack=True, min_=0, desc="CPU cores used by the Grafana pod."), 12, 7)

DASHBOARD = dashboard(UID, "Platform — Grafana", ["platform", "grafana"], d.panels, [],
                      "Grafana itself: HTTP traffic and errors, slow handlers, data source queries, page load time in "
                      "the browser, its database, content, users and resources.")
