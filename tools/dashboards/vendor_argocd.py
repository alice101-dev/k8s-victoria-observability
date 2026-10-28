#!/usr/bin/env python3
import json
import os
import urllib.request

VERSION = "v3.5.5"
URL = f"https://raw.githubusercontent.com/argoproj/argo-cd/{VERSION}/examples/dashboard.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../infrastructure/configs/grafana/base/dashboards/argocd.json")

VM = {"type": "prometheus", "uid": "VictoriaMetrics"}
SWAPPED_JOBS = {
    "Repo Server Stats": ('job="argocd-server-metrics"', 'job="argocd-repo-server-metrics"'),
    "Server Stats": ('job="argocd-repo-server-metrics"', 'job="argocd-server-metrics"'),
}
ZERO_WHEN_EMPTY = {"Operations"}
GRAPH_KEYS = ("aliasColors", "bars", "dashLength", "dashes", "fill", "fillGradient", "hiddenSeries", "legend", "lines",
              "linewidth", "nullPointMode", "percentage", "pointradius", "points", "renderer", "seriesOverrides",
              "spaceLength", "stack", "steppedLine", "thresholds", "timeFrom", "timeRegions", "timeShift", "tooltip",
              "xaxis", "yaxes", "yaxis", "options", "pluginVersion")


def timeseries(panel):
    unit = ((panel.get("yaxes") or [{}])[0] or {}).get("format") or "short"
    custom = {"drawStyle": "bars" if panel.get("bars") else "line", "lineWidth": panel.get("linewidth", 1),
              "fillOpacity": 10 * panel.get("fill", 1), "showPoints": "never", "spanNulls": True,
              "stacking": {"mode": "normal" if panel.get("stack") else "none", "group": "A"}}
    out = {k: v for k, v in panel.items() if k not in GRAPH_KEYS}
    out["type"] = "timeseries"
    out["fieldConfig"] = {"defaults": {"unit": unit, "custom": custom}, "overrides": []}
    out["options"] = {"legend": {"displayMode": "table", "placement": "right", "calcs": ["mean", "max"]},
                      "tooltip": {"mode": "multi", "sort": "desc"}}
    return out


def fix(panel, row):
    if panel.get("type") == "graph":
        panel = timeseries(panel)
    if "datasource" in panel:
        panel["datasource"] = VM
    for target in panel.get("targets", []):
        target["datasource"] = VM
        if panel.get("title") in ZERO_WHEN_EMPTY and "expr" in target:
            target["expr"] = f"({target['expr']}) or vector(0)"
        if row in SWAPPED_JOBS and "expr" in target:
            wrong, right = SWAPPED_JOBS[row]
            target["expr"] = target["expr"].replace(wrong, right)
    return panel


def main():
    with urllib.request.urlopen(URL, timeout=60) as response:
        dash = json.load(response)

    panels, row = [], ""
    for panel in dash["panels"]:
        if panel["type"] == "text" and "argoimage" in json.dumps(panel):
            continue
        if panel["type"] == "row":
            row = panel["title"]
            panel["panels"] = [fix(p, row) for p in panel.get("panels", [])]
            panels.append(panel)
        else:
            panels.append(fix(panel, row))
    dash["panels"] = panels

    variables = []
    for var in dash["templating"]["list"]:
        if var["name"] == "datasource":
            continue
        if var.get("datasource"):
            var["datasource"] = VM
        if var["name"] == "namespace":
            var["query"] = "label_values(argocd_app_info, namespace)"
            var["definition"] = var["query"]
        variables.append(var)
    dash["templating"]["list"] = variables

    dash.pop("id", None)
    dash.pop("__inputs", None)
    dash.pop("__requires", None)
    dash["uid"] = "argocd"
    dash["title"] = "Argo CD"
    dash["tags"] = ["argocd", "platform", "custom"]
    dash["description"] = (f"The upstream Argo CD dashboard ({VERSION}/examples/dashboard.json) on VictoriaMetrics: "
                           "applications, syncs, controller, ApplicationSet controller, cluster cache, repo server, "
                           "API server and Redis.")
    dash["links"] = [{"title": "Platform dashboards", "type": "dashboards", "tags": ["platform"], "asDropdown": True,
                      "includeVars": False, "keepTime": True}]

    with open(OUT, "w") as f:
        json.dump(dash, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
