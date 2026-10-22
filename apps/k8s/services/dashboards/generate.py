"""Generate the SRE dashboards (SLO overview, service drill-down, logs, traces) for the shop app, and the
cluster-wide Kubernetes events dashboard.

usage: python3 apps/k8s/services/dashboards/generate.py [out_dir]
       (rewrites shop-slo.json, shop-service.json, shop-logs.json and shop-traces.json next to it, and the
       links and annotations of shop-overview.json; the Helm chart ships every *.json in this folder.
       cluster-events.json goes to observability/k8s-events/dashboards, shipped by the k8s-events app.)
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
OUT_EVENTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "../../../../observability/k8s-events/dashboards")
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
# Boot time per node name (node-exporter reports the node IP, kube_node_info maps it to the name).
NODE_BOOT = ('max by (node) (label_replace(node_boot_time_seconds, "internal_ip", "$1", "instance", "(.+):[0-9]+") '
             '* on (internal_ip) group_left (node) max by (internal_ip, node) (kube_node_info))')


def restart_annotations(namespace):
    """Node restarts (spot preemption, upgrade, repair) and container restarts with the last termination reason."""
    return [
        # True only in the first minute a new boot time shows (the node is gone for a few minutes in between).
        {"name": "Node restarts", "datasource": {"type": "prometheus", "uid": "VictoriaMetrics"}, "enable": True,
         "iconColor": "purple", "step": "1m", "useValueForTime": False,
         "expr": f"({NODE_BOOT} > max_over_time(({NODE_BOOT})[30m:1m] offset 1m)) * on (node) group_left "
                 "(label_cloud_google_com_gke_nodepool, label_cloud_google_com_gke_provisioning) max by (node, "
                 "label_cloud_google_com_gke_nodepool, label_cloud_google_com_gke_provisioning) (kube_node_labels)",
         "titleFormat": "Node restarted: {{node}}",
         "textFormat": "Pool {{label_cloud_google_com_gke_nodepool}} ({{label_cloud_google_com_gke_provisioning}}): "
                       "spot preemption, upgrade or repair; its pods were rescheduled"},
        {"name": "Container restarts", "datasource": {"type": "prometheus", "uid": "VictoriaMetrics"}, "enable": True,
         "iconColor": "orange", "step": "1m", "useValueForTime": False,
         "expr": f'(changes(kube_pod_container_status_restarts_total{{{namespace}}}[1m]) > 0) * on (namespace, pod, container) '
                 f'group_left (reason) max by (namespace, pod, container, reason) (kube_pod_container_status_last_terminated_reason{{{namespace}}})',
         "titleFormat": "Restart {{pod}}: {{reason}}", "textFormat": "Container {{container}} (OOMKilled: memory limit reached)"},
    ]


# Rollouts (spec changes of shop Deployments/StatefulSets), firing critical Shop alerts, node restarts and shop
# container restarts on every graph.
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
] + restart_annotations('namespace="shop"')
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


def vl(expr, qtype, ref="A", legend="", max_lines=None, ds=VL):
    t = {"datasource": ds, "expr": expr, "queryType": qtype, "editorMode": "code", "refId": ref}
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


def status_history(title, target, desc, th, unit="short"):
    """One row per series and a coloured cell per interval: when each started and stopped."""
    return {"type": "status-history", "title": title, "datasource": target["datasource"], "maxDataPoints": 60,
            "description": desc,
            "fieldConfig": {"defaults": {"unit": unit, "decimals": 0, "color": {"mode": "thresholds"}, "thresholds": th,
                                         "custom": {"fillOpacity": 80, "lineWidth": 1}}, "overrides": []},
            "options": {"showValue": "auto", "rowHeight": 0.85, "colWidth": 0.95, "legend": {"showLegend": False},
                        "tooltip": {"mode": "single", "sort": "none"}},
            "targets": [target]}


def logsql_table(title, expr, columns, desc, ds, overrides=(), sort=None, hide=()):
    """A LogsQL stats query as a table: one row per by(...) group, columns (field, header) in order."""
    return table(title, [vl(expr, "stats", ds=ds)],
                 [{"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
                  {"id": "merge", "options": {}},
                  {"id": "organize", "options": {"excludeByName": {"Time": True, **{h: True for h in hide}},
                                                 "indexByName": {f: i for i, (f, _) in enumerate(columns)},
                                                 "renameByName": dict(columns)}}],
                 overrides=list(overrides), desc=desc, sort=sort, ds=ds)


COUNT = lambda name, width=80: by_name(name, [{"id": "unit", "value": "short"}, {"id": "decimals", "value": 0},
                                               {"id": "custom.width", "value": width}])
PCT = lambda name, orange, red: by_name(name, [
    {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 2}, {"id": "custom.width", "value": 80},
    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
    {"id": "thresholds", "value": thresholds(("green", None), ("orange", orange), ("red", red))}])
LINKS_ON = lambda name, links, width=None: by_name(name, [{"id": "links", "value": links}]
                                                    + ([{"id": "custom.width", "value": width}] if width else []))


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


def write(name, dash, out=None):
    with open(os.path.join(out or OUT, name), "w") as f:
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
      8, 6)
# The SLO closest to violation, named: the first place to look when budgets are being spent.
p = d.add(stat("Lowest error budget", "bottomk(1, slo:error_budget_remaining:ratio)", "percentunit",
               thresholds(("red", None), ("orange", 0), ("green", 0.25)), decimals=1,
               desc="The journey or service SLO with the least error budget left in the 30-day window. Below 0 it is "
                    "violated; under 25% only a short incident remains before it is."), 4, 6)
p["targets"][0]["legendFormat"] = "{{job}} · {{slo}}"
p["options"].update(textMode="value_and_name", colorMode="background")
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

# SLI over the last hour against its objective (dashed): what "99.2% against 99.5%" looks like over time.
TARGET_LINE = {"matcher": {"id": "byFrameRefID", "options": "B"},
               "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                              {"id": "custom.lineWidth", "value": 2}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}
d.row("SLI against the objective — 1h")
for kind, title, who in (("journey", "Journey SLIs", "checkout and browse, measured at api-gateway"),
                         ("service", "Service SLIs", "each service's own requests")):
    d.add(ts(f"{title} — 1h", [vm(f'1 - slo:sli_error:ratio_rate1h{{kind="{kind}"}}', "{{job}} · {{slo}}"),
                               vm(f'max by (slo) (slo:objective:ratio{{kind="{kind}"}})', "objective · {{slo}}", "B")],
             # Fixed 95-100%: an incident dipping to 50% would otherwise flatten the band around the objective.
             "percentunit", decimals=2, min_=0.95, max_=1, fill=0, legend_calcs=("lastNotNull", "min"),
             overrides=[TARGET_LINE],
             desc=f"Share of good requests over the last hour ({who}): availability = not 5xx, latency = faster "
                  "than the threshold. The dashed lines are the objectives; below them the error budget is spent "
                  "faster than planned. The axis stops at 95%: lines leaving it at the bottom are incidents (Min in "
                  "the legend). Definitions at the bottom."), 12, 8)

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

# SLI definitions, read from the chart values the SLO recording rules are rendered from (values.yaml), so the
# text cannot drift from the rules.
VALUES = open(os.path.join(HERE, "..", "values.yaml")).read().split("\nslo:\n", 1)[1].split("\n\n", 1)[0]
val = lambda key: re.search(rf"^  {key}: \"?([0-9.]+)\"?", VALUES, re.M).group(1)
pct_ = lambda v: f"{float(v) * 100:g}%"
ms = lambda v: f"{float(v) * 1000:g} ms"
overrides_ = re.findall(r"^    ([a-z-]+): \{ latencyThreshold: \"([0-9.]+)\" \}", VALUES, re.M)
journeys_ = re.findall(r"^    ([a-z]+): \{ method: ([A-Z]+), route: \"?([^,\"]+)\"?, latencyThreshold: \"([0-9.]+)\" \}", VALUES, re.M)
assert overrides_ and journeys_, "slo values not parsed"
SLI_DOC = f"""
| SLI | Good events | Valid events | Objective (30 days) |
|---|---|---|---|
| **Availability** | responses that are not 5xx (4xx are the client's mistake and count as good) | all HTTP requests | {pct_(val("availability"))} |
| **Latency** | responses faster than the latency threshold | all HTTP requests | {pct_(val("latency"))} |

**Where they are measured.** *Journeys* at the edge (api-gateway), so they include failures of every service behind it:
{"; ".join(f"**{n}** = `{m} {r.replace('|', ' or ')}` (threshold {ms(t)})" for n, m, r, t in journeys_)}.
*Services* on each service's own requests (threshold {ms(val("latencyThreshold"))};
{"; ".join(", ".join(n for n, t in overrides_ if t == th) + f": {ms(th)}" for th in sorted({t for _, t in overrides_}))}:
the checkout path includes the payment provider).

**Error budget and burn rate.** Budget = 1 − objective over a rolling 30 days. Burn rate = error ratio / budget: 1 spends
the budget in exactly 30 days. Pages (critical): burn rate above 14.4 over 1h and 5m, or above 6 over 6h and 30m.
Tickets (warning): above 3 over 1d and 2h, or above 1 over 3d and 6h. Recording rules: `apps/k8s/services/templates/monitoring.yaml`.
"""
d.row("SLI definitions")
d.add({"type": "text", "title": "", "transparent": True, "options": {"mode": "markdown", "content": SLI_DOC.strip()}}, 24, 9)

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
write("shop-service.json", dashboard(
    "shop-service", "Shop — Service", ["shop", "red"], d.panels, [var_service(False), MIN_DUR],
    "One service end to end: SLO, RED, latency distribution, dependencies, database, saturation, logs and traces.", "now-1h"))

# --------------------------------------------------------------------------------------------
# 3. Logs
# --------------------------------------------------------------------------------------------
# Every panel follows the Service variable, the message search and the ad hoc Filters. The Level variable narrows
# the panels that list lines (patterns, facets, request picker, explorer); overview and trends split by level.
d = Dash()
# Case-insensitive literal substring (\Q...\E quotes the text for the regexp); an empty search matches every line.
SEARCH = r'_msg:~`(?i)\Q${search:raw}\E`'
LOGS = f"{APP_LOGS} service:in($service) {SEARCH}"
LOGS_LEVEL = f"{LOGS} level:in($level)"
ERR = "level:=ERROR"
# Lines that bypass the JSON logger have no service or level field: library loggers (go-redis Sentinel
# discovery and failover), Go panics and crash output.
UNSTRUCTURED = ('{kubernetes.pod_namespace="shop", kubernetes.container_name="app"} '
                f'"kubernetes.pod_labels.app.kubernetes.io/name":in($service) -level:* {SEARCH}')
VERSION = '| copy "kubernetes.pod_labels.app.kubernetes.io/version" as version '
# Log lines as shown: the service in front and the cause (err) appended, so a line reads on its own.
READABLE = '| format if (err:*) "<_msg> — err: <err>" as _msg | format "[<service>] <_msg>" as _msg'
SELF = "/d/shop-logs/shop-logs?${service:queryparam}&${level:queryparam}&${__url_time_range}"
MESSAGE_LINK = [{"title": "Show only lines with this message", "url": SELF + "&var-search=${__data.fields.search_key:percentencode}"}]
POD_LINK = [{"title": "Show this pod's lines in the explorer",
             "url": SELF + "&${search:queryparam}&var-filters=kubernetes.pod_name%7C%3D%7C${__value.raw}"}]
SERVICE_DASH = [{"title": "Open Shop — Service dashboard", "url": "/d/shop-service/shop-service?var-service=${__value.raw}&${__url_time_range}"}]
PIPELINE_LINKS = [{"title": "Open VictoriaLogs - vlagent", "url": "/d/Y5Z9GzMGz/victorialogs-vlagent?${__url_time_range}"},
                  {"title": "Open VictoriaLogs - cluster", "url": "/d/XqCOFEX4z/victorialogs-cluster?${__url_time_range}"}]
LEVEL_CELL = by_name("Level", [{"id": "custom.width", "value": 70}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "mappings", "value": [{"type": "value", "options": {
                                   lvl: {"color": c, "index": i} for i, (lvl, c) in enumerate(LEVEL_COLORS.items())}}]}])
GAUGE = lambda name, unit="short", max_=None, width=150: by_name(name, [
    {"id": "unit", "value": unit}, {"id": "custom.width", "value": width}, {"id": "min", "value": 0},
    {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
    {"id": "color", "value": {"mode": "continuous-BlYlRd"}}] + ([{"id": "max", "value": max_}] if max_ else []))
WHEN = lambda name, unit: by_name(name, [{"id": "unit", "value": unit}, {"id": "custom.width", "value": 175}])


def vl_stat(title, expr, unit, th, desc, sparkline=True, decimals=0, no_value="0"):
    """A stat over the time range: a range query summed (with its sparkline), or one stats query."""
    p = stat(title, "", unit, th, desc, decimals, no_value)
    p["datasource"], p["targets"] = VL, [vl(expr, "statsRange" if sparkline else "stats")]
    if sparkline:
        p["options"].update(graphMode="area", reduceOptions={"calcs": ["sum"], "fields": "", "values": False})
    return p


d.row("Overview — $service")
for p in (
    vl_stat("Log lines", f"{LOGS} | stats count() lines", "short", thresholds(("blue", None)),
            "Lines the selected services logged in the time range (all levels)."),
    vl_stat("Warnings", f"{LOGS} level:=WARN | stats count() warnings", "short", thresholds(("green", None), ("orange", 1)),
            "WARN lines. Here mostly business outcomes (unknown user, declined payment), see Patterns."),
    vl_stat("Errors", f"{LOGS} {ERR} | stats count() errors", "short", thresholds(("green", None), ("red", 1)),
            "ERROR lines: a request failed for a technical reason."),
    vl_stat("Error ratio", f"{LOGS} | stats count() if ({ERR}) errors, count() lines "
                           "| math errors*100/lines as error_ratio | delete errors, lines",
            "percent", thresholds(("green", None), ("orange", 0.1), ("red", 1)),
            "Share of ERROR lines among all lines of the selected services.", sparkline=False, decimals=2, no_value="–"),
    vl_stat("Services logging errors", f"{LOGS} {ERR} | stats count_uniq(service) services", "short",
            thresholds(("green", None), ("red", 1)), "Services with at least one ERROR line. More than one at the "
            "same time usually means a shared dependency (database, Redis, a called service).", sparkline=False),
    vl_stat("Lines outside the JSON logger", f"{UNSTRUCTURED} | stats count() lines", "short",
            thresholds(("green", None), ("orange", 1)), "Lines without a level: library loggers that bypass slog "
            "(go-redis logs Sentinel discovery and master changes), Go panics and crash output. Listed under Log explorer."),
):
    d.add(p, 4, 4)

d.row("Trends")
d.add(ts("Lines by level", [vl(f"{LOGS} | stats by (level) count() lines", "statsRange", legend="{{level}}")],
         "short", ds=VL, stack=True, bars=True, overrides=level_colors(), legend_calcs=("sum",)), 12, 8)
d.add(ts("Error ratio by service", [vl(f"{LOGS} | stats by (service) count() if ({ERR}) errors, count() lines "
                                       "| math round(errors*100/lines, 0.01) as error_ratio | delete errors, lines",
                                       "statsRange", legend="{{service}}")],
         "percent", ds=VL, min_=0, soft_max=1, desc="Share of ERROR lines per service. A service that logs more "
         "lines (more traffic) does not look worse here; compare with the Errors per service timeline below."), 12, 8)
d.add(status_history("Errors per service",
                     vl(f"{LOGS} | stats by (service) count() if ({ERR}) errors", "statsRange", legend="{{service}}"),
                     "ERROR lines per service and interval: when each service started and stopped failing, and "
                     "which failed first. Rollouts and critical alerts are marked on the graphs above.",
                     thresholds(("green", None), ("orange", 1), ("red", 10))), 24, 7)

d.row("Patterns — $level lines")
# Numbers (ports, IDs, status codes, IP parts) become <N>, so messages and causes (err) that differ only in such
# values form one pattern. Whole numbers only: collapse_nums also rewrites hex-looking words ("exceeded"). The
# search_key (text before the first placeholder) feeds the message link. Every text column is a by() field: the
# datasource reads the other stats results as numbers.
NUMS = r'| replace_regexp ("\\b[0-9]+\\b", "<N>") at _msg | replace_regexp ("\\b[0-9]+\\b", "<N>") at err '
PATTERNS = (f"{LOGS_LEVEL} {NUMS}"
            '| extract_regexp "^(?P<search_key>[^<]*)" from _msg '
            "| stats by (service, level, _msg, err, search_key) count() lines, count_uniq(kubernetes.pod_name) pods, "
            "min(_time) first, max(_time) last "
            "| total_stats sum(lines) total "
            "| math round(lines*100/total, 0.01) as share, round(first/1e6) as first_ms, round(last/1e6) as last_ms "
            "| delete total, first, last | sort by (lines desc) | limit 50")
d.add(logsql_table("Message patterns", PATTERNS,
                   [("service", "Service"), ("level", "Level"), ("_msg", "Message"), ("err", "Cause"), ("lines", "Lines"),
                    ("share", "Share"), ("pods", "Pods"), ("first_ms", "First seen"), ("last_ms", "Last seen"),
                    ("search_key", "search_key")],
                   "What the selected lines say, grouped by pattern, with the cause (err) the service logged. Click a "
                   "message to keep only its lines: the explorer and the Request picker then show just those.",
                   VL, overrides=[LINKS_ON("Service", SERVICE_DASH, 150), LEVEL_CELL, LINKS_ON("Message", MESSAGE_LINK, 260),
                                  GAUGE("Lines"), GAUGE("Share", "percent", 100, 110), COUNT("Pods", 60),
                                  WHEN("First seen", "dateTimeAsLocal"), WHEN("Last seen", "dateTimeFromNow"),
                                  by_name("search_key", [{"id": "custom.hidden", "value": True}])],
                   sort=[{"displayName": "Lines", "desc": True}]), 24, 10)
d.add(ts("Patterns over time", [vl(f"{LOGS_LEVEL} {NUMS}| stats by (service, _msg) count() lines",
                                   "statsRange", legend="{{service}}: {{_msg}}")],
         "short", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
         desc="The same patterns per interval: a new pattern appearing, or one jumping, marks the start of a problem."), 12, 10)
# Facets: the most frequent values of every field in the selected lines (top 5 per field) and the share of the
# lines carrying each value. A product, user, pod, node or version close to 100% localises the problem. Fields
# that are constant for every shop line, unique per line or covered by the patterns are left out.
FACET_SKIP = ["_msg", "err", "_time", "_stream", "_stream_id", "trace_id", "span_id", "output_stream",
              "kubernetes.container_id", "kubernetes.container_name", "kubernetes.pod_ip", "kubernetes.pod_namespace",
              "kubernetes.pod_labels.pod-template-hash", "kubernetes.pod_labels.app.kubernetes.io/name",
              "kubernetes.pod_labels.app.kubernetes.io/part-of", "kubernetes.pod_labels.app.kubernetes.io/component",
              "kubernetes.pod_labels.app.kubernetes.io/managed-by", "kubernetes.pod_labels.topology.kubernetes.io/region",
              "kubernetes.pod_labels.topology.kubernetes.io/zone"]
FACETS = (f"{LOGS_LEVEL} | delete {', '.join(FACET_SKIP)} | facets 5 keep_const_fields "
          r'| replace_regexp ("^kubernetes\\.(pod_labels\\.)?([a-z]+\\.kubernetes\\.io/)?", "") at field_name '
          "| stats by (field_name, field_value) sum(hits) lines "
          '| format if (field_name:=level) "<lines>" as level_lines | total_stats sum(level_lines) total '
          "| math round(lines*100/total, 0.1) as share | delete level_lines, total | filter -field_name:=level "
          "| sort by (share desc) | limit 60")
d.add(logsql_table("What these lines have in common", FACETS,
                   [("field_name", "Field"), ("field_value", "Value"), ("lines", "Lines"), ("share", "Share")],
                   "Most frequent values of each field in the selected lines and the share of lines carrying them. "
                   "One product, user, pod, node or version near 100% points at the cause.",
                   VL, overrides=[by_name("Field", [{"id": "custom.width", "value": 130}]), COUNT("Lines"),
                                  GAUGE("Share", "percent", 100, 160)],
                   sort=[{"displayName": "Share", "desc": True}]), 12, 10)

d.row("Where — pods, nodes and versions")
d.add(logsql_table("Lines by pod",
                   f'{LOGS} | copy "kubernetes.pod_labels.topology.kubernetes.io/zone" as zone {VERSION}'
                   "| stats by (kubernetes.pod_name, kubernetes.pod_node_name, zone, version) count() lines, "
                   f"count() if (level:=WARN) warnings, count() if ({ERR}) errors "
                   "| math round(errors*100/lines, 0.01) as error_ratio | sort by (errors desc, warnings desc) | limit 30",
                   [("kubernetes.pod_name", "Pod"), ("kubernetes.pod_node_name", "Node"), ("zone", "Zone"),
                    ("version", "Version"), ("lines", "Lines"), ("warnings", "Warnings"), ("errors", "Errors"),
                    ("error_ratio", "Error ratio")],
                   "One pod, node or version with more errors than its peers points at the pod, the node or a rollout "
                   "rather than the code path. Click a pod to list its lines in the explorer.",
                   VL, overrides=[LINKS_ON("Pod", POD_LINK, 270), COUNT("Lines"), COUNT("Warnings"), COUNT("Errors"),
                                  PCT("Error ratio", 0.1, 1)],
                   sort=[{"displayName": "Errors", "desc": True}]), 24, 8)
d.add(ts("Warnings and errors by version", [vl(f"{LOGS} level:in(ERROR, WARN) {VERSION}| stats by (version, level) count() lines",
                                               "statsRange", legend="{{version}} {{level}}")],
         "short", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
         desc="Lines per image version (app.kubernetes.io/version). A new version bringing new errors shows here at "
              "the rollout (blue annotation)."), 24, 7)

d.row("Request — ${request:text}")
p = d.add(logs_panel("All lines of one request, oldest first", f'{APP_LOGS} trace_id:="${{request:raw}}" trace_id:* {READABLE}',
                     "Every service's lines with the trace ID picked in the Request variable: the latest $level lines "
                     "that match Message contains, newest first. The panel link opens the trace."), 24, 9)
p["options"]["sortOrder"] = "Ascending"
p["links"] = [{"title": "Open the trace in Shop — Traces", "url": "/d/shop-traces/shop-traces?var-trace_id=${request}&${__url_time_range}"}]

d.row("Log explorer")
d.add(logs_panel("Lines — $level", f"{LOGS_LEVEL} {READABLE}", "Expand a line for its fields; trace_id links to the trace. Narrow with "
                 "Message contains and Filters (any field)."), 24, 18)
d.add(logs_panel("Lines outside the JSON logger", f'{UNSTRUCTURED} | format "[<kubernetes.pod_name>] <_msg>" as _msg',
                 "Output without a level: library loggers (go-redis Sentinel), Go panics and crash output."), 24, 7)

d.row("Log pipeline — is every line arriving?")
for p in (
    d.add(logsql_table("Last line per service", f"{APP_LOGS} service:in($service) | stats by (service) max(_time) last, count() lines "
                       "| math round(last/1e6) as last_ms | delete last",
                       [("service", "Service"), ("last_ms", "Last line"), ("lines", "Lines")],
                       "When each service last logged. A service that logs per request and stops logging while it "
                       "serves traffic has lost its log shipping or hangs (api-gateway logs only at start and on errors).",
                       VL, overrides=[WHEN("Last line", "dateTimeFromNow"), COUNT("Lines")],
                       sort=[{"displayName": "Last line", "desc": False}]), 6, 8),
    d.add(ts("Log bytes by service", [vl(f"{APP_LOGS} service:in($service) | stats by (service) sum_len(*) bytes",
                                         "statsRange", legend="{{service}}")],
             "bytes", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
             desc="Uncompressed size of all fields per interval: who drives log volume (storage and cost)."), 6, 8),
    d.add(ts("Lines dropped or skipped", [
        vm(f'sum by (reason) (rate(vl_rows_dropped_total{{reason!="debug"}}{RI}))', "dropped: {{reason}}"),
        vm(f"sum(rate(vl_too_long_lines_skipped_total{RI}))", "skipped: too long", "B"),
        vm(f"sum(rate(vlagent_remotewrite_packets_dropped_total{RI}))", "collector dropped", "C")],
        "short", min_=0, desc="Lines per second VictoriaLogs or the collector did not store (whole cluster). "
        "Should stay at 0."), 6, 8),
    d.add(ts("Collector backlog", [
        vm("sum(vlagent_remotewrite_pending_data_bytes)", "pending"),
        vm(f"sum(rate(vlagent_remotewrite_errors_total{RI}))", "send errors", "B"),
        vm(f"sum(rate(vlagent_remotewrite_retries_count_total{RI}))", "retries", "C")],
        "bytes", min_=0, overrides=[right_axis("B", "short"), right_axis("C", "short")],
        desc="Data vlagent has collected but not yet delivered (left) and failed or retried sends per second "
             "(right). A growing backlog delays every log panel."), 6, 8),
):
    p["links"] = PIPELINE_LINKS

# Count graphs: at least one minute per bar, otherwise each bar holds a handful of lines.
for p in d.panels:
    if p["type"] in ("timeseries", "status-history") and p["datasource"] is VL:
        p["interval"] = "1m"

LEVEL = {"name": "level", "label": "Level", "type": "custom", "query": "ERROR,WARN,INFO,DEBUG",
         "multi": True, "includeAll": True,
         "current": {"selected": True, "text": ["ERROR", "WARN"], "value": ["ERROR", "WARN"]},
         "options": [{"selected": v in ("ERROR", "WARN"), "text": v, "value": v} for v in ("ERROR", "WARN", "INFO", "DEBUG")]}
LOG_SERVICE = {"name": "service", "label": "Service", "type": "query", "datasource": VL,
               "query": {"type": "fieldValue", "field": "service", "query": f"{APP_LOGS} service:*", "limit": 100, "refId": "service"},
               "definition": f"service: {APP_LOGS} service:*", "refresh": 2, "sort": 1, "multi": True, "includeAll": True,
               "current": {"selected": True, "text": ["All"], "value": ["$__all"]}}
SEARCH_VAR = {"name": "search", "label": "Message contains", "type": "textbox", "query": "",
              "current": {"text": "", "value": ""}, "description": "Case-insensitive text in the message"}
# Request picker: the latest lines of the selected levels that carry a trace ID, labelled
# "<time> · <service> · <message> · <trace id>" and sorted newest first; the regex keeps the trace ID as the value.
# field_values runs "sort | limit 30" on each vlstorage, so the values limit must cover all nodes' results.
REQUEST_PICK = (f'{LOGS_LEVEL} trace_id:* | sort by (_time desc) | limit 30 | format "<_time>" as t '
                r'| replace ("T", " ") at t | replace_regexp ("\\.[0-9]+Z$", " UTC") at t '
                '| format "<t> · <service> · <_msg> · <trace_id>" as option')
REQUEST_VAR = {"name": "request", "label": "Request (latest first)", "type": "query", "datasource": VL,
               "query": {"type": "fieldValue", "field": "option", "query": REQUEST_PICK, "limit": 100, "refId": "request"},
               "definition": "option: latest lines with a trace ID", "regex": "/^(?<text>.* · (?<value>[0-9a-f]{32}))$/",
               "sort": 2, "refresh": 2, "multi": False, "includeAll": False, "allowCustomValue": True, "current": {}}
ADHOC = {"name": "filters", "label": "Filters", "type": "adhoc", "datasource": VL}
write("shop-logs.json", dashboard(
    "shop-logs", "Shop — Logs", ["shop", "logs"], d.panels, [LOG_SERVICE, LEVEL, SEARCH_VAR, ADHOC, REQUEST_VAR],
    "Application logs of the shop services from VictoriaLogs: volume and error trends, message patterns, what failing "
    "lines have in common, pods and versions, one request across services, the explorer and the log pipeline.", "now-1h"))

# --------------------------------------------------------------------------------------------
# 4. Traces: span analytics with LogsQL on VictoriaTraces (datasource "VictoriaTraces (LogsQL)")
# --------------------------------------------------------------------------------------------
# Computed from the spans at query time; every span is kept here (100% sampling). With head sampling in
# production, divide counts and rates by the sampling ratio (percentiles stay valid). SLOs and alerts keep
# using the metrics.
VTL = {"type": "victoriametrics-logs-datasource", "uid": "VictoriaTracesLogsQL"}
SPANS = '"resource_attr:service.namespace":=shop "resource_attr:service.name":in($service)'
SERVER = f"{SPANS} kind:=2"
SVC = '| copy "resource_attr:service.name" as service'
CODE = '"span_attr:http.response.status_code"'
IS_5XX, IS_4XX = f'{CODE}:~"^5"', f'{CODE}:~"^4"'
# Failed: error status, except business outcomes. HTTP client spans mark 4xx answers as errors (OTel
# semantic conventions), and the payment provider span marks declined cards.
FAILED = f'status_code:=2 -{IS_4XX} -"span_attr:payment.status":=declined'
IN_ENDPOINT_TRACES = 'trace_id:in("resource_attr:service.namespace":=shop kind:=2 name:="${endpoint:raw}" | fields trace_id)'
# Readable operation names: HTTP client spans get their peer, SQL spans the statement, Redis spans the
# command. Attributes are copied to plain names first, because format placeholders read ":" as a modifier.
OPERATION = ('| copy "span_attr:server.address" as peer, "span_attr:db.query.text" as sql, "span_attr:db.statement" as redis_stmt '
             '| replace_regexp ("[ \\t\\n]+", " ") at sql '
             '| format "<name>" as operation '
             '| format if (kind:=3 "span_attr:http.request.method":*) "<name> → <peer>" as operation '
             '| format if (sql:*) "SQL <sql>" as operation '
             '| extract if ("span_attr:db.system":=redis) "<redis_cmd> " from redis_stmt '
             '| format if ("span_attr:db.system":=redis) "Redis <redis_cmd>" as operation')
KEEP_VARS = "${service:queryparam}&${endpoint:queryparam}&${attribute:queryparam}&${min_duration:queryparam}&${__url_time_range}"
TRACE_LINK = [{"title": "Show this trace below", "url": f"/d/shop-traces/shop-traces?{KEEP_VARS}&var-trace_id=${{__value.raw}}"}]
ENDPOINT_LINK = [{"title": "Break down this endpoint (Where the time goes)",
                  "url": "/d/shop-traces/shop-traces?var-service=${__data.fields.Service}&var-endpoint=${__value.raw}"
                         "&${attribute:queryparam}&${min_duration:queryparam}&${__url_time_range}"}]
SERVICE_DASH_LINK = [{"title": "Open Shop — Service dashboard",
                      "url": "/d/shop-service/shop-service?var-service=${__value.raw}&${__url_time_range}"}]
NS_UNIT = lambda name, width=90: by_name(name, [{"id": "unit", "value": "ns"}, {"id": "decimals", "value": 1},
                                                 {"id": "custom.width", "value": width}])
def lq_table(title, expr, columns, desc, overrides=(), sort=None, hide=()):
    """A stats query on spans as a table."""
    return logsql_table(title, expr, columns, desc, VTL, overrides, sort, hide)


def lq_stat(title, expr, unit, th, desc, decimals=1, name=None):
    p = stat(title, "", unit, th, desc, decimals)
    p["datasource"], p["targets"] = VTL, [vl(expr, "stats", ds=VTL)]
    if name:
        p["fieldConfig"]["defaults"]["displayName"] = name
        p["options"]["textMode"] = "value_and_name"
    return p


d = Dash()
d.row("Overview — $service")
d.add(lq_stat("Requests / s", f"{SERVER} | stats rate() rps", "reqps", thresholds(("blue", None)),
              "Server spans per second: every request handled by the selected services (a checkout passes "
              "through several of them)."), 4, 4)
d.add(lq_stat("5xx", f"{SERVER} | stats count() calls, count() if ({IS_5XX}) failed "
                     "| math failed*100/calls as pct_5xx | fields pct_5xx", "percent",
              thresholds(("green", None), ("orange", 0.5), ("red", 1)), "Share of server spans answered with a 5xx.",
              decimals=2), 4, 4)
d.add(lq_stat("p95 latency", f"{SERVER} | stats quantile(0.95, duration) p95", "ns", thresholds(("blue", None)),
              "95th percentile duration of the server spans."), 4, 4)
d.add(lq_stat("Slowest endpoint (p95)", f"{SERVER} | stats by (name) quantile(0.95, duration) p95 "
                                        "| sort by (p95 desc) | limit 1", "ns", thresholds(("blue", None)),
              "The endpoint with the highest p95; break it down in \"Where the time goes\".", name="${__field.labels.name}"), 4, 4)
d.add(lq_stat("Failed operations", f"{SPANS} {FAILED} | stats count() failed", "short",
              thresholds(("green", None), ("red", 1)),
              "Spans with an error status in the time range, business outcomes excluded (4xx answers seen by a "
              "client, declined payments): timeouts, 5xx, database errors.", decimals=0), 4, 4)
d.add(lq_stat("Declined payments", f'{SPANS} "span_attr:payment.status":=declined | stats count() declined', "short",
              thresholds(("blue", None)), "Business outcome, not a failure: payments the provider declined.",
              decimals=0), 4, 4)

d.row("Service map")
# Node graph built from the spans: nodes are the services (their server and consumer spans) plus PostgreSQL
# and Redis (the client spans calling them), edges are client spans from caller to callee. The arc around a
# node is its share of successful (green) and failed (red) spans, so a failing service turns red.
TO_DEPENDENCY = ('| format if (kind:=3 "span_attr:db.system.name":=postgresql) "postgresql" as {f} '
                 '| format if (kind:=3 "span_attr:db.system":=redis) "redis" as {f} ')
MAP_NODES = ('"resource_attr:service.namespace":=shop (kind:=2 or kind:=5 or (kind:=3 ("span_attr:db.system.name":=postgresql '
             'or "span_attr:db.system":=redis) -name:="pool.acquire")) | copy "resource_attr:service.name" as id '
             + TO_DEPENDENCY.format(f="id")
             + '| copy id as title | format if (id:=postgresql) "shop-db (PostgreSQL)" as title '
               '| format if (id:=redis) "Redis" as title '
             # The node graph panel formats only its main stat (options, no field config), so p95 and the failed
             # share go into the subtitle text: a second stats turns that text into a label.
             + f"| stats by (id, title) rate() rps, quantile(0.95, duration) p95, count() requests, count() if ({FAILED}) failed "
               "| math round(p95/1e6, 0.1) as p95_ms, round(failed*100/requests, 0.01) as failed_pct "
               '| format "p95 <p95_ms> ms · <failed_pct>% failed" as subtitle '
               "| stats by (id, title, subtitle) max(rps) mainstat, max(requests) detail__requests, max(failed) detail__failed "
               "| math (detail__requests-detail__failed)/detail__requests as arc__success, "
               "detail__failed/detail__requests as arc__errors")
MAP_EDGES = ('"resource_attr:service.namespace":=shop kind:=3 -name:="pool.acquire" '
             '| copy "resource_attr:service.name" as source, "span_attr:server.address" as target '
             + TO_DEPENDENCY.format(f="target")
             + '| filter target:* | format "<source>→<target>" as id '
             + f"| stats by (id, source, target) rate() mainstat, quantile(0.95, duration) p95, count() detail__calls, "
               f"count() if ({FAILED}) detail__failed "
               "| math round(p95/1e6, 0.1) as detail__p95_ms, round(detail__failed*100/detail__calls, 0.01) as detail__failed_pct "
               "| delete p95")
d.add({"type": "nodeGraph", "title": "Service map", "datasource": VTL,
       "description": "Every service, PostgreSQL and Redis: requests per second in the circle, p95 and the failed share "
                      "below the name. The ring shows the share of successful (green) and failed (red) spans, business "
                      "outcomes not counted as failures. Hover an arrow for its calls per second, p95 and failed share."
                      " Click a node for its details.",
       "targets": [vl(MAP_NODES, "stats", "A", ds=VTL), vl(MAP_EDGES, "stats", "B", ds=VTL)],
       "transformations": [
           {"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
           {"id": "merge", "options": {}, "filter": {"id": "byRefId", "options": "A"}},
           {"id": "merge", "options": {}, "filter": {"id": "byRefId", "options": "B"}},
           # Rename sets the display name only: the panel still finds mainstat, arc__*, detail__* by field name,
           # while the legend and tooltips read the display names.
           {"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": {
               "mainstat": "Requests per second", "arc__success": "Success", "arc__errors": "Failed",
               "detail__requests": "Requests", "detail__failed": "Failed requests", "detail__calls": "Calls",
               "detail__p95_ms": "p95 (ms)", "detail__failed_pct": "Failed (%)"}}}],
       "options": {"nodes": {"mainStatUnit": "reqps",
                             "arcs": [{"field": "arc__success", "color": "green"}, {"field": "arc__errors", "color": "red"}]},
                   "edges": {"mainStatUnit": "reqps"}}}, 14, 15)
d.add(lq_table("Calls to dependencies",
               f'"resource_attr:service.namespace":=shop kind:=3 -name:="pool.acquire" {SVC} | copy "span_attr:server.address" as peer '
               '| format if ("span_attr:db.system.name":=postgresql) "shop-db (PostgreSQL)" as peer '
               '| format if ("span_attr:db.system":=redis) "Redis" as peer | filter peer:* '
               f"| stats by (service, peer) count() calls, count() if ({FAILED}) failed, count() if ({IS_4XX}) e4, "
               "quantile(0.95, duration) p95 | sort by (calls desc) | limit 50",
               [("service", "Caller"), ("peer", "Callee"), ("calls", "Calls"), ("failed", "Failed"), ("e4", "4xx"),
                ("p95", "p95")],
               "Client spans by caller and callee: other services, PostgreSQL (shop-db-rw) and Redis. p95 includes the "
               "network and the callee's work.",
               overrides=[COUNT("Calls"), COUNT("Failed"), COUNT("4xx"), NS_UNIT("p95")],
               sort=[{"displayName": "Calls", "desc": True}]), 10, 15)

d.row("Endpoints — $service")
d.add(lq_table("Endpoints (server spans)",
               f"{SERVER} {SVC} | stats by (service, name) count() calls, rate() rps, count() if ({IS_5XX}) e5, "
               f"count() if ({IS_4XX}) e4, quantile(0.5, duration) p50, quantile(0.95, duration) p95, "
               "quantile(0.99, duration) p99, max(duration) slowest "
               "| math round(e5*100/calls, 0.01) as pct_5xx, round(e4*100/calls, 0.01) as pct_4xx "
               "| sort by (p95 desc) | limit 100",
               [("service", "Service"), ("name", "Endpoint"), ("calls", "Calls"), ("rps", "Req/s"), ("pct_5xx", "5xx %"),
                ("pct_4xx", "4xx %"), ("p50", "p50"), ("p95", "p95"), ("p99", "p99"), ("slowest", "Max")],
               "Every endpoint of the selected services, slowest first. 4xx are client or business outcomes. "
               "Click an endpoint to break it down, a service to open its dashboard.",
               overrides=[LINKS_ON("Endpoint", ENDPOINT_LINK), LINKS_ON("Service", SERVICE_DASH_LINK, 150),
                          COUNT("Calls"), by_name("Req/s", [{"id": "unit", "value": "reqps"}, {"id": "decimals", "value": 2},
                                                            {"id": "custom.width", "value": 80}]),
                          PCT("5xx %", 0.5, 1),
                          by_name("4xx %", [{"id": "unit", "value": "percent"}, {"id": "decimals", "value": 2},
                                            {"id": "custom.width", "value": 80}]),
                          NS_UNIT("p50"), NS_UNIT("p95"), NS_UNIT("p99"), NS_UNIT("Max")],
               sort=[{"displayName": "p95", "desc": True}], hide=("e5", "e4")), 24, 11)
d.add(ts("p95 by endpoint", [vl(f"{SERVER} | stats by (name) quantile(0.95, duration) p95", "statsRange",
                                legend="{{name}}", ds=VTL)], "ns", ds=VTL), 12, 8)
d.add(ts("5xx by endpoint", [vl(f"{SERVER} {IS_5XX} | stats by (name) count() failed", "statsRange",
                                legend="{{name}}", ds=VTL)], "short", ds=VTL, stack=True, bars=True, decimals=0,
         legend_calcs=("sum",)), 12, 8)

d.row("Where the time goes — $endpoint")
d.add(lq_table("Operations in $endpoint traces",
               f'"resource_attr:service.namespace":=shop {IN_ENDPOINT_TRACES} {SVC} {OPERATION} '
               f"| stats by (service, operation) count() calls, count() if ({FAILED}) failed, "
               "quantile(0.95, duration) p95, sum(duration) total | total_stats max(total) root_total "
               "| math round(total*100/root_total, 0.1) as time_pct, total/calls as avg | sort by (total desc) | limit 40",
               [("service", "Service"), ("operation", "Operation"), ("time_pct", "Share of endpoint time"),
                ("calls", "Calls"), ("failed", "Failed"), ("avg", "Avg"), ("p95", "p95")],
               "Every span of the traces that went through the selected endpoint, across all services, by total time. "
               "The share is the operation's time over the endpoint's own time; spans nest (a client call contains "
               "the server span it calls), so read it top-down rather than adding it up. Consumers such as "
               "notification.deliver run after the response.",
               overrides=[by_name("Share of endpoint time", [
                   {"id": "unit", "value": "percent"}, {"id": "min", "value": 0}, {"id": "max", "value": 100},
                   {"id": "custom.width", "value": 220},
                   {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}}]),
                   by_name("Service", [{"id": "custom.width", "value": 150}]), COUNT("Calls"), COUNT("Failed"),
                   NS_UNIT("Avg"), NS_UNIT("p95")],
               sort=[{"displayName": "Share of endpoint time", "desc": True}], hide=("total", "root_total")), 24, 11)
d.add(ts("p95 by operation in $endpoint traces", [vl(
    f'"resource_attr:service.namespace":=shop {IN_ENDPOINT_TRACES} {SVC} {OPERATION} '
    "| stats by (service, operation) quantile(0.95, duration) p95", "statsRange", legend="{{service}}: {{operation}}", ds=VTL)],
    "ns", ds=VTL, desc="A step in one operation shows where a slowdown of the endpoint comes from."), 12, 9)
d.add(ts("Failed operations in $endpoint traces", [vl(
    f'"resource_attr:service.namespace":=shop {IN_ENDPOINT_TRACES} {FAILED} {SVC} {OPERATION} '
    "| stats by (service, operation) count() failed", "statsRange", legend="{{service}}: {{operation}}", ds=VTL)],
    "short", ds=VTL, stack=True, bars=True, decimals=0, legend_calcs=("sum",),
    desc="The operation that fails first is usually the cause; the spans above it fail because of it."), 12, 9)

d.row("Database and Redis, as the services see them — $service")
d.add(lq_table("SQL statements",
               f'{SPANS} "span_attr:db.query.text":* {SVC} | copy "span_attr:db.query.text" as sql '
               '| replace_regexp ("[ \\t\\n]+", " ") at sql '
               "| stats by (service, sql) count() calls, count() if (status_code:=2) failed, quantile(0.5, duration) p50, "
               'quantile(0.95, duration) p95, max(duration) slowest, avg("span_attr:pgx.rows_affected") row_count '
               "| sort by (p95 desc) | limit 50",
               [("service", "Service"), ("sql", "Statement"), ("calls", "Calls"), ("failed", "Failed"), ("p50", "p50"),
                ("p95", "p95"), ("slowest", "Max"), ("row_count", "Rows (avg)")],
               "Client-side duration of each statement: network, lock waits and server execution together. Compare "
               "with Top statements in the CloudNativePG dashboard (server side only): a gap means waiting outside "
               "PostgreSQL's execution, e.g. for locks.",
               overrides=[by_name("Service", [{"id": "custom.width", "value": 150}]), COUNT("Calls"), COUNT("Failed"),
                          NS_UNIT("p50"), NS_UNIT("p95"), NS_UNIT("Max"),
                          by_name("Rows (avg)", [{"id": "decimals", "value": 1}, {"id": "custom.width", "value": 90}])],
               sort=[{"displayName": "p95", "desc": True}]), 24, 8)
d.add(lq_table("Redis commands",
               f'{SPANS} "span_attr:db.system":=redis {SVC} | extract "<command> " from "span_attr:db.statement" '
               "| stats by (service, command) count() calls, count() if (status_code:=2) failed, "
               "quantile(0.95, duration) p95, max(duration) slowest | sort by (calls desc) | limit 30",
               [("service", "Service"), ("command", "Command"), ("calls", "Calls"), ("failed", "Failed"), ("p95", "p95"),
                ("slowest", "Max")],
               "Redis commands by service (cache reads and writes, idempotency keys, the notification stream).",
               overrides=[COUNT("Calls"), COUNT("Failed"), NS_UNIT("p95"), NS_UNIT("Max")],
               sort=[{"displayName": "Calls", "desc": True}]), 12, 8)
d.add(ts("Waiting for a connection vs SQL (p95)", [
    vl(f'{SPANS} name:="pool.acquire" {SVC} | stats by (service) quantile(0.95, duration) p95', "statsRange",
       legend="pool wait {{service}}", ds=VTL),
    vl(f'{SPANS} "span_attr:db.query.text":* {SVC} | stats by (service) quantile(0.95, duration) p95', "statsRange",
       "B", legend="SQL {{service}}", ds=VTL)], "ns", ds=VTL,
    desc="pool.acquire spans: time a request waited for a free connection of its service's pool. Waits close to "
         "the SQL time mean the pool is too small or connections are held too long."), 12, 8)

d.row("Errors — $service")
d.add(ts("Failed spans by operation", [vl(f"{SPANS} {FAILED} {SVC} | stats by (service, name) count() failed",
                                          "statsRange", legend="{{service}}: {{name}}", ds=VTL)],
         "short", ds=VTL, stack=True, bars=True, decimals=0, legend_calcs=("sum",),
         desc="Business outcomes (4xx seen by a client, declined payments) are not counted."), 12, 9)
d.add(lq_table("Top failure messages",
               f"{SPANS} {FAILED} {SVC} | stats by (service, name, status_message) count() failed "
               "| sort by (failed desc) | limit 30",
               [("service", "Service"), ("name", "Operation"), ("status_message", "Message"), ("failed", "Count")],
               "Span status messages, e.g. \"canceling statement due to lock timeout (SQLSTATE 55P03)\" or "
               "\"context deadline exceeded\".",
               overrides=[COUNT("Count", 70)], sort=[{"displayName": "Count", "desc": True}]), 12, 9)
d.add({"type": "logs", "title": "Latest failed spans", "datasource": VTL,
       "description": "Newest failed spans; expand one and use View trace (trace_id) to open the whole trace.",
       "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "sortOrder": "Descending",
                   "dedupStrategy": "none", "prettifyLogMessage": False},
       "targets": [vl(f'{SPANS} {FAILED} {SVC} | math round(duration/1e6, 0.1) as ms '
                      '| format "<service> <name> (<ms> ms): <status_message>" as _msg | format "error" as level',
                      "instant", max_lines=200, ds=VTL)]}, 24, 9)

d.row("Hot spots — $service by ${attribute:text}")
d.add(lq_table("Requests by ${attribute:text}",
               f'{SERVER} "${{attribute:raw}}":* {SVC} | copy "${{attribute:raw}}" as value '
               f"| stats by (value) count() calls, count() if ({IS_5XX}) e5, quantile(0.95, duration) p95, max(duration) slowest "
               "| math round(e5*100/calls, 0.01) as pct_5xx | sort by (pct_5xx desc, p95 desc) | limit 30",
               [("value", "Value"), ("calls", "Calls"), ("pct_5xx", "5xx %"), ("p95", "p95"), ("slowest", "Max")],
               "Server spans grouped by the selected attribute, worst first: one product, user, pod, node or version "
               "standing out points to the cause (a hot row, a bad pod or node, a bad release).",
               overrides=[COUNT("Calls"), PCT("5xx %", 0.5, 1), NS_UNIT("p95"), NS_UNIT("Max")],
               sort=[{"displayName": "5xx %", "desc": True}], hide=("e5",)), 12, 9)
d.add(ts("5xx by ${attribute:text}", [vl(
    f'{SERVER} "${{attribute:raw}}":* {IS_5XX} | copy "${{attribute:raw}}" as value | stats by (value) count() failed',
    "statsRange", legend="{{value}}", ds=VTL)], "short", ds=VTL, stack=True, bars=True, decimals=0,
    legend_calcs=("sum",)), 12, 9)
d.add(ts("Checkout outcomes (api-gateway POST /api/orders)", [vl(
    '"resource_attr:service.namespace":=shop "resource_attr:service.name":=api-gateway kind:=2 name:="POST /api/orders" '
    f'| copy {CODE} as code | stats by (code) count() requests', "statsRange", legend="{{code}}", ds=VTL)],
    "short", ds=VTL, stack=True, bars=True, decimals=0, overrides=code_colors(), legend_calcs=("sum",),
    desc="What users got: 201 created, 402 payment declined, 404 unknown user or product, 5xx failures."), 12, 8)
d.add(ts("Payment outcomes", [vl(
    '"resource_attr:service.namespace":=shop name:="payment-provider.authorize" '
    '| copy "span_attr:payment.status" as status | stats by (status) count() payments', "statsRange",
    legend="{{status}}", ds=VTL)], "short", ds=VTL, stack=True, bars=True, decimals=0, legend_calcs=("sum",),
    overrides=[by_name("approved", [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]),
               by_name("declined", [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}])],
    desc="Payment provider answers; declines are expected (about 10% in this demo)."), 12, 8)

d.row("Traces — $service")
REQUEST_COLUMNS = [("started_ms", "Time"), ("trace_id", "Trace ID"), ("service", "Service"), ("name", "Endpoint"),
                   ("code", "Status"), ("took", "Duration")]
REQUEST_OVERRIDES = [by_name("Time", [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 165}]),
                     LINKS_ON("Trace ID", TRACE_LINK, 250), by_name("Service", [{"id": "custom.width", "value": 125}]),
                     by_name("Status", [{"id": "custom.width", "value": 60}]), NS_UNIT("Duration", 85)]
REQUESTS = (f"{SVC} | copy {CODE} as code | stats by (trace_id, service, name, code) max(duration) took, "
            "max(start_time_unix_nano) started | math round(started/1e6) as started_ms")
d.add(lq_table("Slowest requests (over ${min_duration:text})", f"{SERVER} duration:>${{min_duration:raw}} {REQUESTS} "
               "| sort by (took desc) | limit 50", REQUEST_COLUMNS,
               "Server spans slower than the threshold, slowest first. Click a trace ID to show the trace below.",
               overrides=REQUEST_OVERRIDES, sort=[{"displayName": "Duration", "desc": True}], hide=("started",)), 12, 10)
d.add(lq_table("Failed requests (5xx)", f"{SERVER} {IS_5XX} {REQUESTS} | sort by (started_ms desc) | limit 50",
               REQUEST_COLUMNS, "Server spans answered with a 5xx, newest first. Click a trace ID to show the trace below.",
               overrides=REQUEST_OVERRIDES, sort=[{"displayName": "Time", "desc": True}], hide=("started",)), 12, 10)
d.add({"type": "traces", "title": "Trace: ${trace_id:text}", "datasource": VT,
       "description": "The slowest or failed request of the time range by default; pick another in the Trace variable "
                      "(slowest first) or click a trace ID in the tables above. Every span with its timing, attributes "
                      "and links to the logs and metrics of its service.",
       # Tall enough for a whole checkout trace (up to 35 spans: payment, notification) without scrolling inside the
       # panel: 40 rows = 1512 px.
       "options": {}, "targets": [{"datasource": VT, "refId": "A", "query": "${trace_id}"}]}, 24, 40)


# Span graphs: at least one minute per point, otherwise a point holds a handful of spans and the
# percentiles jump around.
for p in d.panels:
    if p["type"] == "timeseries" and p["datasource"] is VTL:
        p["interval"] = "1m"


def lq_var(name, label, field, query, multi, current):
    v = {"name": name, "label": label, "type": "query", "datasource": VTL,
         "query": {"type": "fieldValue", "field": field, "query": query, "limit": 200, "refId": name},
         "definition": f"{field}: {query}", "refresh": 2, "sort": 1, "multi": multi, "includeAll": multi}
    v["current"] = ({"selected": True, "text": ["All"], "value": ["$__all"]} if multi
                    else {"selected": True, "text": current, "value": current})
    return v


# Trace picker: the 30 slowest (or failed) requests of the time range, labelled "<ms> ms · HTTP <code> · <id>".
# Sorted numerically (descending) on the label's first number, so the slowest trace is selected by default; the
# regex keeps the trace ID as the value. Trace IDs from the table links are accepted as custom values.
# field_values runs "sort | limit 30" on each vtstorage, so the values limit must cover all nodes' results.
TRACE_PICK = (f"{SERVER} (duration:>${{min_duration:raw}} or {IS_5XX}) | stats by (trace_id) max(duration) d, max({CODE}) code "
              '| sort by (d desc) | limit 30 | math round(d/1e6) as ms | format "<ms> ms · HTTP <code> · <trace_id>" as option')
TRACE_VAR = {"name": "trace_id", "label": "Trace (slowest first)", "type": "query", "datasource": VTL,
             "query": {"type": "fieldValue", "field": "option", "query": TRACE_PICK, "limit": 100, "refId": "trace_id"},
             "definition": "option: slowest or failed requests", "regex": "/^(?<text>.* · (?<value>[0-9a-f]{32}))$/",
             "sort": 4, "refresh": 2, "multi": False, "includeAll": False, "allowCustomValue": True, "current": {}}
custom = lambda name, label, pairs, current: {
    "name": name, "label": label, "type": "custom", "query": ",".join(f"{t} : {v}" for t, v in pairs),
    "current": {"selected": True, "text": current, "value": dict(pairs)[current]},
    "options": [{"selected": t == current, "text": t, "value": v} for t, v in pairs]}
ATTRIBUTES = [("product", "span_attr:order.product_id"), ("user", "span_attr:order.user_id"),
              ("HTTP status", "span_attr:http.response.status_code"), ("pod", "resource_attr:k8s.pod.name"),
              ("node", "resource_attr:k8s.node.name"), ("version", "resource_attr:service.version")]
DURATIONS = [(t, str(int(ms * 1e6))) for t, ms in (("100ms", 100), ("250ms", 250), ("500ms", 500), ("1s", 1000), ("2s", 2000))]
write("shop-traces.json", dashboard(
    "shop-traces", "Shop — Traces", ["shop", "traces"], d.panels, [
        lq_var("service", "Service", "resource_attr:service.name", '"resource_attr:service.namespace":=shop', True, None),
        lq_var("endpoint", "Endpoint", "name",
               '"resource_attr:service.namespace":=shop "resource_attr:service.name":in($service) kind:=2', False,
               "POST /api/orders"),
        custom("attribute", "Hot spots by", ATTRIBUTES, "product"),
        custom("min_duration", "Slow request threshold", DURATIONS, "250ms"),
        TRACE_VAR],
    "Distributed tracing analytics from the spans (LogsQL on VictoriaTraces): service map, endpoints, where the time "
    "goes inside a request, database and Redis calls, failures, hot spots by attribute and single traces.",
    "now-1h"))

# --------------------------------------------------------------------------------------------
# 5. Overview: link to the other Shop dashboards
# --------------------------------------------------------------------------------------------
p = os.path.join(OUT, "shop-overview.json")
ov = json.load(open(p))
ov["links"] = LINKS
ov["annotations"] = {"list": ANNOTATIONS}
with open(p, "w") as f:
    json.dump(ov, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("shop-overview.json: links updated")

# --------------------------------------------------------------------------------------------
# 6. Kubernetes events (all namespaces): stored in VictoriaLogs by the k8s-events collector (k8s_events receiver)
# --------------------------------------------------------------------------------------------
# Events of cluster-scoped objects (nodes) have no namespace and are always included. The Type variable narrows
# the event list and the explorer; the overview and trends split by type themselves.
d = Dash()
EV = '{service.name="kubernetes-events"}'
EVENTS = f'{EV} (k8s.namespace.name:in($namespace) or k8s.namespace.name:="") {SEARCH}'
EVENTS_TYPE = f"{EVENTS} severity_text:in($type)"
WARNING = "severity_text:=Warning"
NS_RE = 'namespace=~"${namespace:regex}"'
NAMESPACE = '| copy k8s.namespace.name as namespace | format if (namespace:="") "(cluster)" as namespace '
# Event lines as shown: "<Kind>/<name> <reason>: <message>", with the type as the log level.
EVENT_LINE = ('| format "<k8s.object.kind>/<k8s.object.name> <k8s.event.reason>: <_msg>" as _msg '
              '| format if (severity_text:=Warning) "warning" as level | format if (severity_text:=Normal) "info" as level')
EV_SELF = "/d/cluster-events/cluster-events?${namespace:queryparam}&${type:queryparam}&${__url_time_range}"
OBJECT_LINK = [{"title": "Show this object's events in the explorer",
                "url": EV_SELF + "&var-filters=k8s.object.name%7C%3D%7C${__value.raw}"}]
NODE_LINKS = [{"title": "Open Kubernetes / Views / Nodes", "url": "/d/k8s_views_nodes/kubernetes-views-nodes?${__url_time_range}"}]

d.row("Overview — $namespace")
for p in (
    vl_stat("Warning events", f"{EVENTS} {WARNING} | stats count() events", "short",
            thresholds(("green", None), ("orange", 1)), "Events of type Warning, including repeats of the same event."),
    vl_stat("Pods that could not be scheduled", f"{EVENTS} k8s.event.reason:=FailedScheduling "
            "| stats count_uniq(k8s.object.name) pods", "short", thresholds(("green", None), ("red", 1)),
            "Pods the scheduler could not place: not enough CPU or memory, node affinity, taints or volumes. After a "
            "spot preemption the remaining nodes may be too full.", sparkline=False),
    vl_stat("Failed probes", f"{EVENTS} k8s.event.reason:=Unhealthy | stats count() events", "short",
            thresholds(("green", None), ("orange", 1)), "Readiness and liveness probe failures (the pod leaves its "
            "Service, or the container is restarted)."),
    vl_stat("OOM kills", f'{EV} k8s.event.reason:=OOMKilling {SEARCH} | stats count_uniq(_msg) kills', "short",
            thresholds(("green", None), ("red", 1)), "Processes the kernel killed at a container memory limit, reported "
            "by node-problem-detector on the node (all namespaces; the event names the process, the pod shows under "
            "Container restarts).", sparkline=False),
    stat("Container restarts", f"sum(round(increase(kube_pod_container_status_restarts_total{{{NS_RE}}}[$__range])))",
         "short", thresholds(("green", None), ("orange", 1)), "Container restarts in the time range; the reasons are "
         "in the Container restarts table.", decimals=0, no_value="0"),
    stat("Node restarts", f"count(time() - {NODE_BOOT} < $__range_s)", "short", thresholds(("green", None), ("orange", 1)),
         "Nodes that booted in the time range: spot VM preemption, node upgrade or repair, or a new node. Their pods "
         "were rescheduled; the Nodes row shows when.", decimals=0, no_value="0"),
):
    d.add(p, 4, 4)

d.row("Trends")
d.add(ts("Warning events by reason", [vl(f"{EVENTS} {WARNING} | stats by (k8s.event.reason) count() events",
                                         "statsRange", legend="{{k8s.event.reason}}")],
         "short", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
         desc="FailedScheduling: no node fits. Unhealthy: probe failed. BackOff: crash or image pull loop. Failed: "
              "image pull or container start failed. OOMKilling: memory limit. Evicted / Preempting: node pressure "
              "or a higher-priority pod."), 12, 8)
d.add(status_history("Warning events per namespace",
                     vl(f"{EVENTS} {WARNING} {NAMESPACE}| stats by (namespace) count() events", "statsRange",
                        legend="{{namespace}}"),
                     "When each namespace had warnings; (cluster) is nodes and other cluster-scoped objects.",
                     thresholds(("green", None), ("orange", 1), ("red", 20))), 12, 8)

d.row("Nodes — restarts and spot preemptions")
POOL = "label_cloud_google_com_gke_nodepool"
PROVISIONING = "label_cloud_google_com_gke_provisioning"
NODE_LABELS = (f"max by (node, {POOL}, {PROVISIONING}, label_cloud_google_com_gke_preemptible, "
               "label_node_kubernetes_io_instance_type, label_topology_kubernetes_io_zone) (kube_node_labels)")
BY_POOL = f'* on (node) group_left ({POOL}) max by (node, {POOL}) (kube_node_labels{{{POOL}!=""}})'
# Why a node went away or came back, from its events in the time range. Several of them often share one second
# (NodeNotReady and Rebooted), so the most telling one wins, not the latest: 5 autoscaler scale-down, 4 shutdown
# (spot preemption or maintenance), 3 reboot, 2 joined the cluster, 1 NotReady. The table maps the code to text.
NODE_CAUSE = (f"{EV} k8s.object.kind:=Node k8s.event.reason:in(ScaleDown, Shutdown, NodeShutdown, Rebooted, RegisteredNode, "
              "NodeNotReady) | copy k8s.object.name as node "
              '| format if (k8s.event.reason:=NodeNotReady) "1" as cause | format if (k8s.event.reason:=RegisteredNode) "2" as cause '
              '| format if (k8s.event.reason:=Rebooted) "3" as cause | format if (k8s.event.reason:in(Shutdown, NodeShutdown)) "4" as cause '
              '| format if (k8s.event.reason:=ScaleDown) "5" as cause '
              "| stats by (node) max(cause) cause, max(_time) last | math round(last/1e6) as cause_ms | delete last")
CAUSES = {"5": ("Autoscaler scale-down", "blue"), "4": ("Shutdown: spot preemption or maintenance", "orange"),
          "3": ("Rebooted: preemption, repair or reset", "orange"), "2": ("Joined: scale-up or recreated VM", "green"),
          "1": ("NotReady", "red")}
d.add(table("Nodes", [vm(f"{NODE_BOOT} * 1000", ref="A", instant=True, fmt="table"),
                      vm(f"time() - {NODE_BOOT} < bool $__range_s", ref="B", instant=True, fmt="table"),
                      vm('max by (node) (kube_node_status_condition{condition="Ready", status="true"})', ref="C",
                         instant=True, fmt="table"),
                      vm(NODE_LABELS, ref="D", instant=True, fmt="table"),
                      vl(NODE_CAUSE, "stats", ref="E")],
            [{"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
             {"id": "merge", "options": {}},
             {"id": "organize", "options": {
                 "excludeByName": {"Time": True, "Value #D": True},
                 "indexByName": {POOL: 0, "node": 1, PROVISIONING: 2, "label_cloud_google_com_gke_preemptible": 3,
                                 "label_node_kubernetes_io_instance_type": 4, "label_topology_kubernetes_io_zone": 5,
                                 "Value #A": 6, "Value #B": 7, "cause": 8, "cause_ms": 9, "Value #C": 10},
                 "renameByName": {POOL: "Node pool", "node": "Node", PROVISIONING: "Provisioning",
                                  "label_cloud_google_com_gke_preemptible": "Preemptible",
                                  "label_node_kubernetes_io_instance_type": "Machine type",
                                  "label_topology_kubernetes_io_zone": "Zone", "Value #A": "Booted",
                                  "Value #B": "Booted in range", "cause": "Cause (time range)",
                                  "cause_ms": "Last node event", "Value #C": "Ready"}}}],
            overrides=[by_name("Node pool", [{"id": "custom.width", "value": 170}]),
                       by_name("Provisioning", [{"id": "custom.width", "value": 190}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                {"id": "mappings", "value": [{"type": "value", "options": {
                                                    "spot": {"text": "Spot (can be preempted)", "color": "orange", "index": 0},
                                                    "preemptible": {"text": "Preemptible (can be preempted)", "color": "orange", "index": 1},
                                                    "standard": {"text": "On-demand", "color": "green", "index": 2}}}]}]),
                       by_name("Preemptible", [{"id": "custom.width", "value": 100}]),
                       by_name("Machine type", [{"id": "custom.width", "value": 130}]),
                       by_name("Zone", [{"id": "custom.width", "value": 150}]),
                       by_name("Booted", [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "custom.width", "value": 130}]),
                       by_name("Booted in range", [{"id": "custom.width", "value": 130}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                      {"id": "mappings", "value": [{"type": "value", "options": {
                                                          "1": {"text": "Yes", "color": "orange", "index": 0},
                                                          "0": {"text": "No", "color": "text", "index": 1}}}]}]),
                       by_name("Cause (time range)", [{"id": "custom.width", "value": 290}, {"id": "noValue", "value": "No node event"},
                                                      {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                                      {"id": "mappings", "value": [{"type": "value", "options": {
                                                          code: {"text": text, "color": color, "index": i}
                                                          for i, (code, (text, color)) in enumerate(CAUSES.items())}}]}]),
                       by_name("Last node event", [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "custom.width", "value": 130}]),
                       by_name("Ready", [{"id": "custom.width", "value": 90}, {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                         {"id": "mappings", "value": [{"type": "value", "options": {
                                             "1": {"text": "Ready", "color": "green", "index": 0},
                                             "0": {"text": "NotReady", "color": "red", "index": 1}}},
                                             {"type": "special", "options": {"match": "null", "result": {
                                                 "text": "Removed", "color": "#808080", "index": 2}}}]}])],
            desc="Every node with its GKE node pool, provisioning model and machine type, when it last booted and whether "
                 "that was in the time range (a restart, or a new node). Spot (and preemptible) nodes can be reclaimed by Compute Engine at any time: "
                 "a recent boot there usually is a preemption, after which GKE recreated the VM under the same name and its "
                 "pods were rescheduled. On-demand nodes reboot only for upgrades or repairs. Cause: the most telling "
                 "node event in the time range (autoscaler ScaleDown, Shutdown, Rebooted, RegisteredNode, NodeNotReady); "
                 "a node removed by the autoscaler stays listed for a few minutes.",
            sort=[{"displayName": "Node pool", "desc": False}]), 24, 7)
d.panels[-1]["datasource"] = {"type": "datasource", "uid": "-- Mixed --"}
d.add(ts("Ready nodes by node pool",
         [vm(f'sum by ({POOL}) (kube_node_status_condition{{condition="Ready", status="true"}} {BY_POOL})', f"{{{{{POOL}}}}} ready"),
          vm(f"count by ({POOL}) (kube_node_info {BY_POOL})", f"{{{{{POOL}}}}} nodes", "B")],
         "short", decimals=0, min_=0, legend_calcs=("min", "max"),
         desc="Ready nodes against all nodes, per node pool: a dip is a node lost (preemption, upgrade) until it is back; "
              "a rise is the autoscaler adding a node."), 10, 8)
d.add(logs_panel("Node events", f"{EV} k8s.object.kind:=Node {SEARCH} {EVENT_LINE}",
                 "Events on nodes: NodeNotReady, Rebooted, Starting (kubelet), RegisteredNode, OOMKilling and other "
                 "node-problem-detector findings."), 14, 8)
for p in d.panels[-3:]:
    p["links"] = NODE_LINKS

d.row("Pods and workloads — why they fail")
d.add(logsql_table("Warning events by object",
                   f"{EVENTS_TYPE} {NAMESPACE}"
                   r'| replace_regexp ("\\b[0-9]+\\b", "<N>") at _msg '
                   "| stats by (namespace, k8s.object.kind, k8s.object.name, k8s.event.reason, _msg) count() events, "
                   "max(_time) last | math round(last/1e6) as last_ms | delete last | sort by (last_ms desc) | limit 100",
                   [("namespace", "Namespace"), ("k8s.object.kind", "Kind"), ("k8s.object.name", "Object"),
                    ("k8s.event.reason", "Reason"), ("_msg", "Message"), ("events", "Events"), ("last_ms", "Last seen")],
                   "Events of the selected types per object and message (numbers collapsed), newest first. Click an "
                   "object to list its events in the explorer.",
                   VL, overrides=[by_name("Namespace", [{"id": "custom.width", "value": 130}]),
                                  by_name("Kind", [{"id": "custom.width", "value": 110}]),
                                  LINKS_ON("Object", OBJECT_LINK, 280), by_name("Reason", [{"id": "custom.width", "value": 140}]),
                                  COUNT("Events", 70), WHEN("Last seen", "dateTimeFromNow")],
                   sort=[{"displayName": "Last seen", "desc": True}]), 24, 10)
RESTARTS = f"sum by (namespace, pod, container) (round(increase(kube_pod_container_status_restarts_total{{{NS_RE}}}[$__range]))) > 0"
d.add(table("Container restarts",
            [vm(f"max by (namespace, pod, container, reason) (max_over_time(kube_pod_container_status_last_terminated_reason{{{NS_RE}}}[$__range])) "
                f"* on (namespace, pod, container) group_left () ({RESTARTS}) or on (namespace, pod, container) ({RESTARTS})",
                instant=True, fmt="table")],
            [{"id": "organize", "options": {"excludeByName": {"Time": True},
                                            "indexByName": {"namespace": 0, "pod": 1, "container": 2, "reason": 3, "Value": 4},
                                            "renameByName": {"namespace": "Namespace", "pod": "Pod", "container": "Container",
                                                             "reason": "Termination reason", "Value": "Restarts"}}}],
            overrides=[COUNT("Restarts", 80), by_name("Termination reason", [{"id": "custom.width", "value": 160}])],
            desc="Containers that restarted in the time range and why their runs ended: OOMKilled (memory limit), "
                 "Error (non-zero exit, failed liveness probe) or Completed. Pods deleted since keep their row.",
            sort=[{"displayName": "Restarts", "desc": True}]), 14, 8)
d.add(ts("Pending pods", [vm(f'sum by (namespace) (kube_pod_status_phase{{phase="Pending", {NS_RE}}}) > 0', "{{namespace}}")],
         "short", stack=True, decimals=0, min_=0, legend_calcs=("max",),
         desc="Pods waiting for a node or for their images. Persisting after a preemption means the remaining "
              "nodes are too full (see FailedScheduling)."), 10, 8)

d.row("Event explorer")
d.add(logs_panel("Events — $type", f"{EVENTS_TYPE} {EVENT_LINE}",
                 "Every event as <Kind>/<name> <reason>: <message>. Expand an event for the object, namespace, node and "
                 "count. Narrow with Message contains and Filters (any field)."), 24, 14)

d.row("Event pipeline")
for p in (
    d.add(ts("Events collected", [
        vm(f"sum(rate(otelcol_receiver_accepted_log_records{RI}))", "received from the API server"),
        vm(f"sum(rate(otelcol_exporter_sent_log_records{RI}))", "stored in VictoriaLogs", "B"),
        vm(f"sum(rate(otelcol_exporter_send_failed_log_records{RI}))", "failed to store", "C")],
        "short", min_=0, desc="Events per second through the k8s-events collector. Failed sends are retried from "
        "its queue; the KubeEventsExportFailing alert fires when they persist."), 12, 6),
    d.add(ts("Collector queue and memory", [
        vm("sum(otelcol_exporter_queue_size)", "queued events"),
        vm("sum(otelcol_exporter_queue_capacity)", "queue capacity", "B"),
        vm("sum(otelcol_process_memory_rss)", "memory", "C")],
        "short", min_=0, overrides=[right_axis("C", "bytes")],
        desc="Events waiting to be sent (left) and the collector's memory (right). A full queue drops events."), 12, 6),
):
    p["links"] = [{"title": "Open VictoriaLogs - cluster", "url": "/d/XqCOFEX4z/victorialogs-cluster?${__url_time_range}"}]

for p in d.panels:
    if p["type"] in ("timeseries", "status-history") and p["datasource"] is VL:
        p["interval"] = "1m"

EV_NAMESPACE = {"name": "namespace", "label": "Namespace", "type": "query", "datasource": VL,
                "query": {"type": "fieldValue", "field": "k8s.namespace.name", "query": f"{EV} k8s.namespace.name:*",
                          "limit": 200, "refId": "namespace"},
                "definition": f"k8s.namespace.name: {EV}", "refresh": 2, "sort": 1, "multi": True, "includeAll": True,
                "current": {"selected": True, "text": ["All"], "value": ["$__all"]}}
EV_TYPE = {"name": "type", "label": "Type", "type": "custom", "query": "Warning,Normal", "multi": True, "includeAll": True,
           "current": {"selected": True, "text": ["Warning"], "value": ["Warning"]},
           "options": [{"selected": v == "Warning", "text": v, "value": v} for v in ("Warning", "Normal")]}
os.makedirs(OUT_EVENTS, exist_ok=True)
events = dashboard("cluster-events", "Cluster — Events", ["kubernetes", "events"], d.panels,
                   [EV_NAMESPACE, EV_TYPE, SEARCH_VAR, ADHOC],
                   "Kubernetes events from VictoriaLogs (all namespaces): warnings by reason and namespace, node restarts "
                   "and spot preemptions, why pods fail (scheduling, probes, OOM, image pulls), restarts and the event "
                   "pipeline.", "now-6h")
events["annotations"] = {"list": restart_annotations(NS_RE)}
write("cluster-events.json", events, OUT_EVENTS)
