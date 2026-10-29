from .common import (CADVISOR, COUNT, RI, VL, by_name, Dash, dashboard, logs_panel, restart_annotations, stat,
                     table, thresholds, ts, vl, vm)

FILE = "gateway-kgateway-public.json"
UID = "gateway-kgateway-public"

d = Dash()
NS = 'namespace="kgateway-system"'
PROXY = 'job="kgateway-system/kgateway-proxy"'
LISTENER = f'{PROXY}, envoy_http_conn_manager_prefix="http"'
UPSTREAM = f'{PROXY}, envoy_cluster_name=~"kube_.*", envoy_cluster_name!~"kube_(monitoring|kgateway-system)_.*"'
OTEL = f'{PROXY}, envoy_cluster_name=~"kube_monitoring_otel-collector_4317"'
ACCESS = '{service.name="kgateway-public"}'
REQ = f"sum(rate(envoy_http_downstream_rq_xx{{{LISTENER}}}{RI}))"
ERR5 = f'sum(rate(envoy_http_downstream_rq_xx{{{LISTENER}, envoy_response_code_class="5"}}{RI}))'


def quantile(q, metric, by="", sel=LISTENER):
    group = f"le, {by}" if by else "le"
    return f"histogram_quantile({q}, sum by ({group}) (rate({metric}{{{sel}}}{RI})))"


def instant(expr, ref):
    return vm(expr, ref=ref, instant=True, fmt="table")


def merged(columns):
    return [{"id": "merge", "options": {}},
            {"id": "organize", "options": {"excludeByName": {"Time": True},
                                           "indexByName": {f: i for i, (f, _) in enumerate(columns)},
                                           "renameByName": dict(columns)}}]


d.row("Overview")
for p in (
    stat("Requests / s", f"{REQ.replace(RI, '[5m]')} or vector(0)", "reqps", thresholds(("blue", None)),
         "Requests per second at the gateway listener (port 80), all routes."),
    stat("5xx ratio", f"({ERR5.replace(RI, '[5m]')} / {REQ.replace(RI, '[5m]')}) or vector(0)", "percentunit",
         thresholds(("green", None), ("orange", 0.01), ("red", 0.05)),
         "Share of responses with status 5xx over the last 5 minutes. Above 5% for 10 minutes fires GatewayHighErrorRate."),
    stat("p95 latency", quantile(0.95, "envoy_http_downstream_rq_time_bucket").replace(RI, "[5m]"), "ms",
         thresholds(("green", None), ("orange", 500), ("red", 1000)),
         "95th percentile of the time from the first byte received to the last byte sent, gateway included.",
         decimals=0),
    stat("Proxies up", f'count(up{{{PROXY}}} == 1) or vector(0)', "short",
         thresholds(("red", None), ("orange", 1), ("green", 2)), "Envoy replicas of the Gateway kgateway-public that are scraped as up.",
         decimals=0),
    stat("Controller up", 'max(up{job="kgateway"}) or vector(0)', "short", thresholds(("red", None), ("green", 1)),
         "The kgateway controller that turns Gateway API resources into Envoy configuration.", decimals=0),
    stat("Telemetry export errors", f'sum(increase(envoy_cluster_upstream_rq_xx{{{OTEL}, envoy_response_code_class!="2"}}[$__range])) or vector(0)',
         "short", thresholds(("green", None), ("orange", 1)),
         "Failed OTLP calls from the proxies to the OpenTelemetry Collector (spans and access logs) in the time range.",
         decimals=0),
):
    d.add(p, 4, 4)

d.row("Traffic at the listener")
d.add(ts("Requests by status class", [vm(f'sum by (envoy_response_code_class) (rate(envoy_http_downstream_rq_xx{{{LISTENER}}}{RI}))',
                                         "{{envoy_response_code_class}}xx")],
         "reqps", stack=True, min_=0,
         overrides=[by_name(f"{c}xx", [{"id": "color", "value": {"mode": "fixed", "fixedColor": col}}])
                    for c, col in (("2", "green"), ("3", "blue"), ("4", "orange"), ("5", "red"))],
         desc="Responses per second by status class at the gateway listener."), 12, 8)
d.add(ts("Latency", [vm(quantile(q, "envoy_http_downstream_rq_time_bucket"), f"p{int(q * 100)}", chr(65 + i))
                     for i, q in enumerate((0.5, 0.95, 0.99))],
         "ms", min_=0, desc="Request duration percentiles at the listener, gateway included (Envoy downstream time)."),
      12, 8)
d.add(ts("Active connections and requests",
         [vm(f"sum(envoy_http_downstream_cx_active{{{LISTENER}}})", "connections"),
          vm(f"sum(envoy_http_downstream_rq_active{{{LISTENER}}})", "requests in flight", "B")],
         "short", min_=0, desc="Client connections open on the listener and requests being processed."), 12, 7)
d.add(ts("Gateway-generated failures",
         [vm(f'sum(rate(envoy_cluster_upstream_cx_connect_fail{{{UPSTREAM}}}{RI})) or vector(0)', "connect failures"),
          vm(f'sum(rate(envoy_cluster_upstream_rq_timeout{{{UPSTREAM}}}{RI})) or vector(0)', "upstream timeouts", "B"),
          vm(f'sum(rate(envoy_http_downstream_rq_xx{{{LISTENER}, envoy_response_code_class="4"}}{RI})) or vector(0)',
             "4xx (incl. no route)", "C")],
         "reqps", min_=0, desc="Failures the gateway produces itself: backend connection failures and timeouts, and 4xx "
                               "such as 404 for paths without a route."), 12, 7)

d.row("Upstreams (routed services)")
UP_COLUMNS = [("envoy_cluster_name", "Upstream (kube_<namespace>_<service>_<port>)"), ("Value #A", "Requests / s"),
              ("Value #B", "5xx"), ("Value #C", "p95"), ("Value #D", "Healthy endpoints")]
d.add(table("Upstreams", [
    instant(f"sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_xx{{{UPSTREAM}}}[5m]))", "A"),
    instant(f'(sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_xx{{{UPSTREAM}, envoy_response_code_class="5"}}[5m])) '
            f'/ sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_xx{{{UPSTREAM}}}[5m]))) '
            f'or sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_xx{{{UPSTREAM}}}[5m])) * 0', "B"),
    instant(quantile(0.95, "envoy_cluster_upstream_rq_time_bucket", "envoy_cluster_name", UPSTREAM).replace(RI, "[5m]"), "C"),
    instant(f"min by (envoy_cluster_name) (envoy_cluster_membership_healthy{{{UPSTREAM}}}) "
            f"and on (envoy_cluster_name) sum by (envoy_cluster_name) (increase(envoy_cluster_upstream_rq_xx{{{UPSTREAM}}}[$__range])) > 0", "D")],
    merged(UP_COLUMNS),
    overrides=[by_name("Requests / s", [{"id": "unit", "value": "reqps"}, {"id": "decimals", "value": 2}]),
               by_name("5xx", [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 1},
                               {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "thresholds", "value": thresholds(("green", None), ("orange", 0.01), ("red", 0.05))}]),
               by_name("p95", [{"id": "unit", "value": "ms"}, {"id": "decimals", "value": 0}]),
               COUNT("Healthy endpoints", 140)],
    sort=[{"displayName": "Requests / s", "desc": True}],
    defaults={"noValue": "No traffic", "custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
    desc="Every backend that received traffic through the gateway in the time range: request rate, 5xx share, p95 "
         "upstream time and endpoints Envoy considers healthy (0 means 503 with flag UH)."), 24, 7)
d.add(ts("Requests per upstream", [vm(f"sum by (envoy_cluster_name) (rate(envoy_cluster_upstream_rq_xx{{{UPSTREAM}}}{RI})) > 0",
                                      "{{envoy_cluster_name}}")],
         "reqps", min_=0, desc="Requests per second the gateway sends to each backend."), 12, 7)
d.add(ts("Upstream p95", [vm(quantile(0.95, "envoy_cluster_upstream_rq_time_bucket", "envoy_cluster_name", UPSTREAM),
                             "{{envoy_cluster_name}}")],
         "ms", min_=0, desc="Time each backend takes to answer, measured by the gateway."), 12, 7)

d.row("Access logs and traces")
d.add(table("Requests by route and status", [vl(
    f"{ACCESS} | stats by (envoy.route_name, http.response.status_code) count() requests | sort by (requests desc) | limit 30",
    "stats")],
    [{"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
     {"id": "merge", "options": {}},
     {"id": "organize", "options": {"excludeByName": {"Time": True},
                                    "renameByName": {"envoy.route_name": "Route", "http.response.status_code": "Status",
                                                     "requests": "Requests"}}}],
    overrides=[COUNT("Requests", 100), by_name("Status", [{"id": "custom.width", "value": 80}])],
    sort=[{"displayName": "Requests", "desc": True}], ds=VL,
    desc="Access log records (OTLP) per route and status in the time range. The route name ends with the HTTPRoute "
         "namespace and name."), 12, 8)
d.add(logs_panel("Failed requests", f'{ACCESS} http.response.status_code:~"^5"',
                 "Access log records with status 5xx: method, path, status, Envoy response flags and duration. Each "
                 "record carries the trace_id of the request; open it in VictoriaTraces from the log details."), 12, 8)

d.row("Proxies and controller")
d.add(ts("Proxy memory against the limit",
         [vm(f'sum by (pod) (container_memory_working_set_bytes{{{CADVISOR}, {NS}, container="kgateway-proxy"}}) '
             f'/ sum by (pod) (kube_pod_container_resource_limits{{{NS}, container="kgateway-proxy", resource="memory"}})', "{{pod}}")],
         "percentunit", min_=0, max_=1, desc="Working set of each Envoy replica against its memory limit."), 8, 7)
d.add(ts("Proxy CPU", [vm(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{CADVISOR}, {NS}, container="kgateway-proxy"}}{RI}))',
                          "{{pod}}")], "short", min_=0, desc="CPU cores used by each Envoy replica."), 8, 7)
d.add(ts("Controller translations and syncs",
         [vm(f'sum by (result) (rate(kgateway_translator_translations_total{RI})) > 0', "translations {{result}}"),
          vm(f'sum by (result) (rate(kgateway_status_syncer_status_syncs_total{RI})) > 0', "status syncs {{result}}", "B"),
          vm(f'sum(rate(kgateway_xds_snapshot_syncs_total{RI})) > 0', "xDS pushes", "C")],
         "short", min_=0, desc="How often the controller rebuilds the Envoy configuration and pushes it to the proxies; "
                               "anything but success fires KgatewayTranslationErrors."), 8, 7)

gateway = dashboard(UID, "Gateway — kgateway-public", ["platform", "gateway"], d.panels, [],
                    "The internal Gateway kgateway-public (kgateway, Envoy): traffic, errors and latency at the listener, every "
                    "routed backend, OTLP access logs with their traces, and the health of the proxies and controller.")
gateway["links"] = [
    {"title": "Platform dashboards", "type": "dashboards", "tags": ["platform"], "asDropdown": True,
     "includeVars": False, "keepTime": True},
    {"title": "Explore gateway traces", "type": "link",
     "url": "/explore?left={\"datasource\":\"VictoriaTraces\",\"queries\":[{\"refId\":\"A\",\"queryType\":\"search\",\"service\":\"kgateway-public\"}]}",
     "targetBlank": True},
]
gateway["annotations"] = {"list": restart_annotations(NS)}
DASHBOARD = gateway
