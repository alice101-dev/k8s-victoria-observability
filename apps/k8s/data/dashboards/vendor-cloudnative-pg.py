#!/usr/bin/env python3
"""Vendor the CloudNativePG Grafana dashboard (cloudnative-pg/grafana-dashboards) with local fixes.

usage: python3 apps/k8s/data/dashboards/vendor-cloudnative-pg.py   (rewrites cloudnative-pg.json next to it)

Fixes for this stack. vmagent adds cluster=<GKE cluster> to every series, while the upstream "cluster"
variable expects that label to hold the CloudNativePG cluster name (set by the CNPG PodMonitor):
- "cluster" is derived from the instance pod names (shop-db-1 → shop-db), so "All" instances match
  only that cluster's pods and PVCs instead of every "<name>-<n>" pod in the namespace.
- Reconcile-error panels count errors within the time range (increase) instead of showing the
  operator's cumulative counter, which stays red until the operator restarts.
- The "First Recoverability Point" graph is labeled with the cluster name and shows the timestamp in
  the legend: a flat timestamp series stretches the y axis from 1970, so the axis is hidden.
- Export placeholders (__inputs, ${DS_EXPRESSION}) are resolved for file provisioning.
"""
import json
import os
import re
import urllib.request

# main after cluster-v0.0.5: also reads the Barman Cloud plugin backup metrics.
COMMIT = "7dd1bc255b5202530e1bae7b858a3bb41a2028c7"
URL = f"https://raw.githubusercontent.com/cloudnative-pg/grafana-dashboards/{COMMIT}/charts/cluster/grafana-dashboard.json"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudnative-pg.json")

RECONCILE = re.compile(r"max\((controller_runtime_reconcile_total\{[^}]*\})\)")


def walk(panels):
    for p in panels:
        yield p
        yield from walk(p.get("panels", []))


raw = urllib.request.urlopen(URL, timeout=30).read().decode()
dash = json.loads(raw.replace("${DS_EXPRESSION}", "__expr__"))
for key in ("__inputs", "__requires", "__elements", "id"):
    dash.pop(key, None)
dash["uid"] = "cloudnative-pg-shop"
dash["tags"] = ["cloudnativepg", "shop"]

for var in dash["templating"]["list"]:
    if var["name"] == "cluster":
        query = 'label_values(cnpg_collector_up{namespace=~"$namespace"}, pod)'
        var.update(query={"qryType": 1, "query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery"},
                   definition=query, regex="/^(?<text>.+)-[0-9]+$/", current={})

for panel in walk(dash["panels"]):
    for target in panel.get("targets", []):
        expr = target.get("expr", "")
        if "controller_runtime_reconcile_total" in expr:
            if panel["type"] == "timeseries":
                window = "$__rate_interval"
            elif expr.startswith("clamp_max("):
                window = "1h"  # health tile: red only for errors in the last hour
            else:
                window = "$__range"
            target["expr"] = RECONCILE.sub(rf"sum(increase(\1[{window}]))", expr)
        if "first_recoverability_point" in expr and panel["type"] == "timeseries":
            target["legendFormat"] = "$cluster"
            panel["fieldConfig"]["defaults"]["custom"]["axisPlacement"] = "hidden"
            panel["options"]["legend"].update(displayMode="list", placement="bottom", calcs=["lastNotNull"])

with open(OUT, "w") as f:
    json.dump(dash, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"wrote {OUT}")
