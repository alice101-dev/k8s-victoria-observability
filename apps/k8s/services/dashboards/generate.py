"""Generate the SRE dashboards (SLO overview, service drill-down, logs) for the shop app.

usage: python3 apps/k8s/services/dashboards/generate.py
       (rewrites shop-slo.json, shop-service.json and shop-logs.json next to it, and the links and
       annotations of shop-overview.json; the Helm chart ships every *.json in this folder)
"""
import json, os, sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__))
VM = {"type": "prometheus", "uid": "VictoriaMetrics"}
VL = {"type": "victoriametrics-logs-datasource", "uid": "VictoriaLogs"}
VT = {"type": "jaeger", "uid": "VictoriaTraces"}
NS = 'namespace="shop"'
RI = "[$__rate_interval]"
APP_LOGS = '{kubernetes.pod_namespace="shop"}'
BURN = "/ on (job, slo, kind) (1 - slo:objective:ratio)"

LINKS = [
    {"title": "Shop dashboards", "type": "dashboards", "tags": ["shop"], "asDropdown": True,
     "includeVars": False, "keepTime": True},
    {"title": "Explore traces", "type": "link", "url": "/explore?left={\"datasource\":\"VictoriaTraces\"}", "targetBlank": True},
    {"title": "Explore logs", "type": "link", "url": "/explore?left={\"datasource\":\"VictoriaLogs\"}", "targetBlank": True},
]
# Rollouts (spec changes of shop Deployments/StatefulSets) and firing critical Shop alerts on every graph.
ANNOTATIONS = [
    {"name": "Rollouts", "datasource": {"type": "prometheus", "uid": "VictoriaMetrics"}, "enable": True,
     "iconColor": "blue", "step": "1m", "useValueForTime": False,
     "expr": 'changes(max by (deployment) (kube_deployment_metadata_generation{namespace="shop"})[1m]) > 0'
             ' or changes(max by (statefulset) (kube_statefulset_metadata_generation{namespace="shop"})[1m]) > 0',
     "titleFormat": "Rollout {{deployment}}{{statefulset}}", "textFormat": "Spec changed (image, env or resources)"},
    # Critical only: long-window warnings stay on for hours and would cover every graph.
    {"name": "Critical alerts", "datasource": {"type": "prometheus", "uid": "VictoriaMetrics"}, "enable": True,
     "iconColor": "red", "step": "1m", "useValueForTime": False,
     "expr": 'max by (alertname, job, deployment, slo) (ALERTS{alertname=~"Shop.*", alertstate="firing", severity="critical"})',
     "titleFormat": "{{alertname}}", "textFormat": "{{job}}{{deployment}} {{slo}}"},
]
LEVEL_COLORS = {"ERROR": "red", "WARN": "orange", "INFO": "green", "DEBUG": "blue"}
SVC_VAR_QUERY = f'label_values(http_server_requests_total{{{NS}}}, job)'


class Dash:
    """Panels flow left to right and wrap at 24 columns."""

    def __init__(self):
        self.panels, self.id, self.x, self.y, self.h = [], 0, 0, 0, 0

    def _newline(self):
        if self.x:
            self.y, self.x, self.h = self.y + self.h, 0, 0

    def add(self, p, w, h):
        if self.x + w > 24:
            self._newline()
        self.id += 1
        p["id"], p["gridPos"] = self.id, {"x": self.x, "y": self.y, "w": w, "h": h}
        if p["type"] == "timeseries" and w < 12:
            p["options"]["legend"].update(displayMode="list", placement="bottom", calcs=[])
        self.x, self.h = self.x + w, max(self.h, h)
        if self.x >= 24:
            self._newline()
        self.panels.append(p)
        return p

    def row(self, title):
        self._newline()
        self.add({"type": "row", "title": title, "collapsed": False, "panels": []}, 24, 1)


def vm(expr, legend="", ref="A", instant=False, fmt=None):
    t = {"datasource": VM, "expr": expr, "legendFormat": legend, "refId": ref,
         "range": not instant, "instant": instant}
    if fmt:
        t["format"] = fmt
    return t


def vl(expr, qtype, ref="A", legend="", max_lines=None):
    t = {"datasource": VL, "expr": expr, "queryType": qtype, "editorMode": "code", "refId": ref}
    if legend:
        t["legendFormat"] = legend
    if max_lines:
        t["maxLines"] = max_lines
    return t


def thresholds(*steps):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def ts(title, targets, unit, ds=VM, desc="", stack=False, bars=False, th=None, decimals=None,
       overrides=None, legend_calcs=("mean", "max"), min_=None, max_=None, soft_min=None, soft_max=None, fill=10):
    custom = {"lineWidth": 1, "fillOpacity": 60 if bars else fill, "showPoints": "never",
              "drawStyle": "bars" if bars else "line",
              "stacking": {"mode": "normal" if stack else "none", "group": "A"}}
    if th:
        custom["thresholdsStyle"] = {"mode": "dashed"}
    defaults = {"unit": unit, "custom": custom}
    if th:
        defaults["thresholds"] = th
    if decimals is not None:
        defaults["decimals"] = decimals
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    if soft_min is not None:
        custom["axisSoftMin"] = soft_min
    if soft_max is not None:
        custom["axisSoftMax"] = soft_max
    return {"type": "timeseries", "title": title, "description": desc, "datasource": ds,
            "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
            "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": list(legend_calcs)},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "targets": targets}


def stat(title, expr, unit, th, desc="", decimals=2, no_value="–"):
    return {"type": "stat", "title": title, "description": desc, "datasource": VM,
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": th,
                                         "noValue": no_value}, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "none", "textMode": "value"},
            "targets": [vm(expr, instant=True)]}


def by_name(name, props):
    return {"matcher": {"id": "byName", "options": name}, "properties": props}


def level_colors():
    return [{"matcher": {"id": "byName", "options": lvl},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
            for lvl, c in LEVEL_COLORS.items()]


def code_colors():
    return [{"matcher": {"id": "byRegexp", "options": rx},
             "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": c}}]}
            for rx, c in (("^2..$", "green"), ("^4..$", "orange"), ("^5..$", "red"))]


def logs_panel(title, expr, desc=""):
    return {"type": "logs", "title": title, "description": desc, "datasource": VL,
            "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True,
                        "sortOrder": "Descending", "dedupStrategy": "none", "prettifyLogMessage": False},
            "targets": [vl(expr, "instant", max_lines=500)]}


def table(title, targets, transformations, overrides=None, desc="", sort=None, ds=VM, defaults=None):
    p = {"type": "table", "title": title, "description": desc, "datasource": ds,
         "fieldConfig": {"defaults": defaults or {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}},
                         "overrides": overrides or []},
         "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
         "transformations": transformations, "targets": targets}
    if sort:
        p["options"]["sortBy"] = sort
    return p


def var_service(multi):
    v = {"name": "service", "label": "Service", "type": "query", "datasource": VM,
         "query": {"query": SVC_VAR_QUERY, "refId": "service"}, "definition": SVC_VAR_QUERY,
         "refresh": 2, "sort": 1, "multi": multi, "includeAll": multi}
    v["current"] = ({"selected": True, "text": ["All"], "value": ["$__all"]} if multi
                    else {"selected": True, "text": "api-gateway", "value": "api-gateway"})
    return v


def dashboard(uid, title, tags, panels, variables, desc, time_from="now-3h"):
    return {"uid": uid, "title": title, "description": desc, "tags": tags, "timezone": "browser",
            "schemaVersion": 39, "version": 1, "editable": True, "graphTooltip": 1,
            "time": {"from": time_from, "to": "now"}, "refresh": "1m",
            "templating": {"list": variables}, "links": LINKS, "annotations": {"list": ANNOTATIONS},
            "panels": panels}


def write(name, dash):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(dash, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{name}: {len(dash['panels'])} panels")


# --------------------------------------------------------------------------------------------
# 1. SLO overview
# --------------------------------------------------------------------------------------------
d = Dash()
SERVICES = ["api-gateway", "notification-service", "order-service", "payment-service", "product-service", "user-service"]
DEPLOY = f'{NS}, deployment=~"{"|".join(SERVICES)}"'
SVC_LINK = [{"title": "Open service dashboard",
             "url": "/d/shop-service/shop-service?var-service=${__data.fields.job}&${__url_time_range}"}]
GW_LINK = [{"title": "Open api-gateway dashboard",
            "url": "/d/shop-service/shop-service?var-service=api-gateway&${__url_time_range}"}]
pct = lambda name, dec=2: by_name(name, [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": dec}])


def slo_table(title, kind, first_col, link, desc, show_window=True):
    q = lambda e: f'sum by (job, slo) ({e})'
    return table(
        title,
        # sum by (job, slo): identical label sets in every query, so "merge" joins them into one row
        [vm(q(e), ref=ref, instant=True, fmt="table") for ref, e in (
            ("A", f'slo:objective:ratio{{kind="{kind}"}}'),
            ("B", f'1 - slo:sli_error:ratio_rate30d{{kind="{kind}"}}'),
            ("C", f'slo:error_budget_remaining:ratio{{kind="{kind}"}}'),
            ("D", f'slo:sli_error:ratio_rate1h{{kind="{kind}"}} {BURN}'),
            ("E", f'slo:sli_error:ratio_rate6h{{kind="{kind}"}} {BURN}'),
            ("F", f'slo:sli_window:seconds{{kind="{kind}"}}'),
            ("G", f'slo:sli_threshold:seconds{{kind="{kind}"}}'),
            # Time until the 30d budget is gone at the current 1h burn rate (capped at a year).
            ("H", f'clamp_max(clamp_min(slo:error_budget_remaining:ratio{{kind="{kind}"}}, 0) * 2592000'
                  f' / on (job, slo, kind) (slo:sli_error:ratio_rate1h{{kind="{kind}"}} {BURN}), 31536000)'))],
        [{"id": "merge", "options": {}},
         {"id": "organize", "options": {
             "excludeByName": {"Time": True, "cluster": True, "__name__": True, "Value #F": not show_window},
             "indexByName": {"job": 0, "slo": 1, "Value #G": 2, "Value #A": 3, "Value #B": 4, "Value #C": 5,
                             "Value #D": 6, "Value #E": 7, "Value #H": 8, "Value #F": 9},
             "renameByName": {"job": first_col, "slo": "SLI", "Value #A": "Objective", "Value #B": "Actual (30d)",
                              "Value #C": "Budget remaining", "Value #D": "Burn rate 1h", "Value #E": "Burn rate 6h",
                              "Value #F": "Data in window", "Value #G": "Threshold", "Value #H": "Budget gone in"}}}],
        overrides=[
            by_name(first_col, [{"id": "links", "value": link}]),
            pct("Objective", 1), pct("Actual (30d)", 3),
            by_name("Budget remaining", [
                {"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 1}, {"id": "min", "value": 0}, {"id": "max", "value": 1},
                {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
                {"id": "thresholds", "value": thresholds(("red", None), ("orange", 0), ("green", 0.25))}]),
            *[by_name(n, [{"id": "decimals", "value": 2},
                          {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                          {"id": "thresholds", "value": thresholds(("green", None), ("orange", 1), ("red", lim))}])
              for n, lim in (("Burn rate 1h", 14.4), ("Burn rate 6h", 6))],
            by_name("Data in window", [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 1}]),
            by_name("Threshold", [{"id": "unit", "value": "s"},
                                  {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "no 5xx"}}}]}]),
            by_name("Budget gone in", [
                {"id": "unit", "value": "s"}, {"id": "decimals", "value": 1},
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "thresholds", "value": thresholds(("red", None), ("orange", 86400), ("green", 604800))},
                {"id": "mappings", "value": [
                    {"type": "value", "options": {"0": {"text": "exhausted", "color": "red"}}},
                    {"type": "range", "options": {"from": 31536000, "to": None,
                                                  "result": {"text": "not at this rate", "color": "green"}}}]}]),
        ],
        sort=[{"displayName": "Budget remaining", "desc": False}], desc=desc)


d.row("User journeys — measured at api-gateway")
for name in ("checkout", "browse"):
    d.add(stat(f"{name.capitalize()} · 5m",
               f'1 - slo:sli_error:ratio_rate5m{{job="{name}", slo="availability"}}', "percentunit",
               thresholds(("red", None), ("orange", 0.99), ("green", 0.995)), decimals=2, no_value="no traffic",
               desc=f"Share of {name} requests answered without a 5xx at the edge, including failures of every service behind it."),
          3, 7)
d.add(slo_table("Journey SLOs", "journey", "Journey", GW_LINK,
                "What users experience. A service that is down loses its own SLI (it receives no requests), "
                "but the journeys that depend on it keep burning here.", show_window=False), 18, 7)

d.row("Service health")
d.add(stat("Services without ready pods", f'count(kube_deployment_status_replicas_available{{{DEPLOY}}} == 0) or vector(0)',
           "short", thresholds(("green", None), ("red", 1)), decimals=0,
           desc="A service with no ready pod is down even though its SLO rows stay quiet. Alert: ShopServiceDown."), 4, 6)
d.add({"type": "bargauge", "title": "Ready pods per service", "datasource": VM,
       "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1,
                                    "thresholds": thresholds(("red", None), ("orange", 0.01), ("green", 1))}, "overrides": []},
       "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "color",
                   "namePlacement": "left", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
       "targets": [vm(f'max by (deployment) (kube_deployment_status_replicas_available{{{DEPLOY}}})'
                      f' / max by (deployment) (kube_deployment_spec_replicas{{{DEPLOY}}})', "{{deployment}}", instant=True)]},
      12, 6)
d.add(stat("SLO data in window", "max(slo:sli_window:seconds)", "s", thresholds(("orange", None), ("green", 2592000)), decimals=1,
           desc="How much data the 30-day window really holds. Below 30 days (e.g. a recently created cluster), "
                "budget figures describe only this period and a short incident looks much larger."), 4, 6)
d.add(stat("SLO rules last evaluated", "time() - max(timestamp(slo:sli_error:ratio_rate5m))", "s",
           thresholds(("green", None), ("orange", 90), ("red", 300)), decimals=0,
           desc="Age of the newest SLI sample. If vmalert or the metrics storage is down, the dashboard silently goes stale."), 4, 6)

d.row("Services — error budget (30-day window)")
d.add(slo_table("Service SLOs", "service", "Service", SVC_LINK,
                "Availability = share of non-5xx responses; latency = share of requests faster than the service threshold. "
                "Burn rate 1 spends the budget in exactly 30 days; alerts page at 14.4 (1h) or 6 (6h). "
                "Budget gone in = remaining budget at the current 1h burn rate. Click a service to drill down."), 24, 15)

d.row("Burn rate")
d.add(ts("Burn rate — 1h window", [vm(f"slo:sli_error:ratio_rate1h {BURN}", "{{job}} · {{slo}}")], "x",
         th=thresholds(("green", None), ("orange", 1), ("red", 14.4)), decimals=2, min_=0, soft_max=1,
         desc="Pages when both the 1h and 5m burn rates exceed 14.4 (2% of the 30-day budget in one hour)."), 12, 9)
d.add(ts("Burn rate — 6h window", [vm(f"slo:sli_error:ratio_rate6h {BURN}", "{{job}} · {{slo}}")], "x",
         th=thresholds(("green", None), ("orange", 1), ("red", 6)), decimals=2, min_=0, soft_max=1,
         desc="Pages when both the 6h and 30m burn rates exceed 6 (5% of the 30-day budget in six hours)."), 12, 9)

d.row("Budget and SLI trends")
d.add(ts("Error budget remaining (30d)", [vm("slo:error_budget_remaining:ratio", "{{job}} · {{slo}}")], "percentunit",
         th=thresholds(("red", None), ("green", 0)), decimals=1, legend_calcs=("lastNotNull", "min"),
         max_=1, soft_min=0, fill=0,
         desc="Below 0 the SLO is violated for the 30-day window."), 12, 9)
d.add(ts("Bad requests ratio — 5m", [vm("slo:sli_error:ratio_rate5m", "{{job}} · {{slo}}")], "percentunit", soft_max=0.01,
         desc="Share of requests that are 5xx (availability) or slower than the threshold (latency)."), 12, 9)

d.row("Alerts")
d.add(table(
    "Shop alerts — firing and pending",
    [vm('ALERTS{alertname=~"Shop.*"}', instant=True, fmt="table")],
    [{"id": "organize", "options": {
        "excludeByName": {"Time": True, "Value": True, "__name__": True, "cluster": True, "alertgroup": True},
        "indexByName": {"alertname": 0, "alertstate": 1, "severity": 2, "job": 3, "slo": 4, "service": 5}}}],
    overrides=[by_name("alertstate", [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                      {"id": "mappings", "value": [{"type": "value", "options": {
                                          "firing": {"color": "red", "index": 0},
                                          "pending": {"color": "orange", "index": 1}}}]}])],
    defaults={"custom": {"align": "auto", "cellOptions": {"type": "auto"}}, "noValue": "No Shop alerts"},
    desc="Alert state written by vmalert (ALERTS series). Burn-rate alerts carry the job and slo labels."), 24, 6)

write("shop-slo.json", dashboard(
    "shop-slo", "Shop — SLO Overview", ["shop", "slo"], d.panels, [],
    "Availability and latency SLOs per service: error budget, burn rates and alerts."))

# --------------------------------------------------------------------------------------------
# 2. Service drill-down
# --------------------------------------------------------------------------------------------
d = Dash()
S = f'{NS}, job="$service"'
POD = 'pod=~"$service-[a-z0-9]+-[a-z0-9]+"'
LOGS_LINK = [{"title": "Open in Shop — Logs", "url": "/d/shop-logs/shop-logs?var-service=$service&${__url_time_range}"}]

d.row("SLO — $service")
d.add(stat("Availability (30d)", f'1 - slo:sli_error:ratio_rate30d{{job="$service", slo="availability"}}', "percentunit",
           thresholds(("red", None), ("orange", 0.99), ("green", 0.995)), decimals=3), 4, 4)
d.add(stat("Availability budget left", f'slo:error_budget_remaining:ratio{{job="$service", slo="availability"}}', "percentunit",
           thresholds(("red", None), ("orange", 0), ("green", 0.25)), decimals=1), 4, 4)
d.add(stat("Latency SLI (30d)", f'1 - slo:sli_error:ratio_rate30d{{job="$service", slo="latency"}}', "percentunit",
           thresholds(("red", None), ("orange", 0.98), ("green", 0.99)), decimals=3,
           desc="Share of requests faster than the service latency threshold."), 4, 4)
d.add(stat("Latency budget left", f'slo:error_budget_remaining:ratio{{job="$service", slo="latency"}}', "percentunit",
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
right_axis = lambda ref, unit: {"matcher": {"id": "byFrameRefID", "options": ref},
                                "properties": [{"id": "unit", "value": unit}, {"id": "custom.axisPlacement", "value": "right"}]}
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
d.add(table("Slowest traces",
            [{"datasource": VT, "refId": "A", "queryType": "search", "service": "$service",
              "minDuration": "$min_duration", "limit": 20}], [], desc="Traces through $service slower than the selected minimum duration.",
            **TRACE_TABLE), 12, 9)
d.add(table("Traces with errors",
            [{"datasource": VT, "refId": "A", "queryType": "search", "service": "$service",
              "tags": "error=true", "limit": 20}], [], desc="Traces through $service with a span marked as error.",
            **TRACE_TABLE), 12, 9)

MIN_DUR = {"name": "min_duration", "label": "Slow trace threshold", "type": "custom",
           "query": "100ms,250ms,500ms,1s,2s", "current": {"selected": True, "text": "250ms", "value": "250ms"},
           "options": [{"selected": v == "250ms", "text": v, "value": v} for v in ("100ms", "250ms", "500ms", "1s", "2s")]}
write("shop-service.json", dashboard(
    "shop-service", "Shop — Service", ["shop", "red"], d.panels, [var_service(False), MIN_DUR],
    "One service end to end: SLO, RED, latency distribution, dependencies, database, saturation, logs and traces.", "now-1h"))

# --------------------------------------------------------------------------------------------
# 3. Logs
# --------------------------------------------------------------------------------------------
d = Dash()
F = f'{APP_LOGS} service:in($service) level:in($level)'
d.row("Volume")
d.add(ts("Log volume by level", [vl(f'{F} | stats by (level) count() logs', "statsRange", legend="{{level}}")],
         "short", ds=VL, stack=True, bars=True, overrides=level_colors(), legend_calcs=("sum",)), 12, 8)
d.add(ts("Log volume by service", [vl(f'{F} | stats by (service) count() logs', "statsRange", legend="{{service}}")],
         "short", ds=VL, stack=True, bars=True, legend_calcs=("sum",)), 12, 8)
d.row("Top sources")
TOP = [{"id": "labelsToFields", "options": {"mode": "columns"}}, {"id": "merge", "options": {}}]
d.add(table("Top messages",
            [vl(f'{F} | stats by (service, level, _msg) count() hits | sort by (hits desc) | limit 15', "stats")],
            TOP + [{"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True},
                                                  "indexByName": {"service": 0, "level": 1, "_msg": 2, "Value": 3},
                                                  "renameByName": {"service": "Service", "level": "Level", "_msg": "Message", "Value": "Count", "hits": "Count"}}}],
            sort=[{"displayName": "Count", "desc": True}], ds=VL,
            desc="The most frequent messages for the selected services and levels."), 14, 9)
d.add(table("Top pods",
            [vl(f'{F} | stats by (kubernetes.pod_name) count() hits | sort by (hits desc) | limit 10', "stats")],
            TOP + [{"id": "organize", "options": {"excludeByName": {"Time": True, "__name__": True},
                                                  "indexByName": {"kubernetes.pod_name": 0, "Value": 1},
                                                  "renameByName": {"kubernetes.pod_name": "Pod", "Value": "Count", "hits": "Count"}}}],
            sort=[{"displayName": "Count", "desc": True}], ds=VL,
            desc="A single pod at the top usually means a node or pod-local problem rather than a code bug."), 10, 9)
d.row("Logs")
d.add(logs_panel("Logs", F, "Expand a line and follow trace_id to open the trace. Add field filters with the Filters variable."), 24, 16)

LEVEL = {"name": "level", "label": "Level", "type": "custom", "query": "ERROR,WARN,INFO,DEBUG",
         "multi": True, "includeAll": True,
         "current": {"selected": True, "text": ["ERROR", "WARN"], "value": ["ERROR", "WARN"]},
         "options": [{"selected": v in ("ERROR", "WARN"), "text": v, "value": v} for v in ("ERROR", "WARN", "INFO", "DEBUG")]}
ADHOC = {"name": "filters", "label": "Filters", "type": "adhoc", "datasource": VL}
write("shop-logs.json", dashboard(
    "shop-logs", "Shop — Logs", ["shop", "logs"], d.panels, [var_service(True), LEVEL, ADHOC],
    "Application logs of the shop services from VictoriaLogs.", "now-1h"))

# --------------------------------------------------------------------------------------------
# 4. Overview: link to the other Shop dashboards
# --------------------------------------------------------------------------------------------
p = os.path.join(OUT, "shop-overview.json")
ov = json.load(open(p))
ov["links"] = LINKS
ov["annotations"] = {"list": ANNOTATIONS}
with open(p, "w") as f:
    json.dump(ov, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("shop-overview.json: links updated")
