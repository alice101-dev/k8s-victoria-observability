"""Shared building blocks of the generated dashboards: datasources, panel helpers, links and annotations."""
import json
import os

VM = {"type": "prometheus", "uid": "VictoriaMetrics"}
VL = {"type": "victoriametrics-logs-datasource", "uid": "VictoriaLogs"}
VT = {"type": "jaeger", "uid": "VictoriaTraces"}
NS = 'namespace="shop"'
RI = "[$__rate_interval]"
APP_LOGS = '{kubernetes.pod_namespace="shop"}'
BURN = "/ on (job, slo, kind) (1 - slo:objective:ratio)"
# The kubelet is scraped on /metrics/cadvisor and /metrics/resource: both expose container_cpu_usage_seconds_total
# and container_memory_working_set_bytes for every container, so container_* queries pick one endpoint (as the
# kubernetes-mixin does) or every sum counts twice.
CADVISOR = 'job="kubelet", metrics_path="/metrics/cadvisor"'

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


def write(name, dash, out):
    with open(os.path.join(out, name), "w") as f:
        json.dump(dash, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{name}: {len(dash['panels'])} panels")


# Shared by the Logs and Kubernetes events dashboards.
right_axis = lambda ref, unit: {"matcher": {"id": "byFrameRefID", "options": ref},
                                "properties": [{"id": "unit", "value": unit}, {"id": "custom.axisPlacement", "value": "right"}]}
# Case-insensitive literal substring (\Q...\E quotes the text for the regexp); an empty search matches every line.
SEARCH = r'_msg:~`(?i)\Q${search:raw}\E`'
SEARCH_VAR = {"name": "search", "label": "Message contains", "type": "textbox", "query": "",
              "current": {"text": "", "value": ""}, "description": "Case-insensitive text in the message"}
ADHOC = {"name": "filters", "label": "Filters", "type": "adhoc", "datasource": VL}
WHEN = lambda name, unit: by_name(name, [{"id": "unit", "value": unit}, {"id": "custom.width", "value": 175}])


def vl_stat(title, expr, unit, th, desc, sparkline=True, decimals=0, no_value="0"):
    """A stat over the time range: a range query summed (with its sparkline), or one stats query."""
    p = stat(title, "", unit, th, desc, decimals, no_value)
    p["datasource"], p["targets"] = VL, [vl(expr, "statsRange" if sparkline else "stats")]
    if sparkline:
        p["options"].update(graphMode="area", reduceOptions={"calcs": ["sum"], "fields": "", "values": False})
    return p
