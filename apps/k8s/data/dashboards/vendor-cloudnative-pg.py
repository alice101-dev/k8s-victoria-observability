#!/usr/bin/env python3
"""Vendor the CloudNativePG Grafana dashboard (cloudnative-pg/grafana-dashboards) with local fixes.

usage: python3 apps/k8s/data/dashboards/vendor-cloudnative-pg.py   (rewrites cloudnative-pg.json next to it)

Fixes for this stack. vmagent adds cluster=<GKE cluster> to every series, while the upstream "cluster"
variable expects that label to hold the CloudNativePG cluster name (set by the CNPG PodMonitor):
- "cluster" is derived from the instance pod names (shop-db-1 → shop-db), so "All" instances match
  only that cluster's pods and PVCs instead of every "<name>-<n>" pod in the namespace.
- Reconcile-error panels count errors within the time range (increase) instead of showing the
  operator's cumulative counter, which stays red until the operator restarts.
- The Backups row is rebuilt (backup_panels): backup age and last attempt, recovery window, data at
  risk (RPO), WAL archiving throughput and backlog, and the Barman Cloud sidecar events from VictoriaLogs.
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

PROM = {"type": "prometheus", "uid": "${DS_PROMETHEUS}"}
LOGS = {"type": "victoriametrics-logs-datasource", "uid": "VictoriaLogs"}
SEL = 'namespace=~"$namespace", pod=~"$instances"'
LAST_BACKUP = f'max({{__name__=~"barman_cloud_cloudnative_pg_io_last_available_backup_timestamp|cnpg_collector_last_available_backup_timestamp", {SEL}}})'
LAST_FAILED = f'max({{__name__=~"barman_cloud_cloudnative_pg_io_last_failed_backup_timestamp|cnpg_collector_last_failed_backup_timestamp", {SEL}}})'
FIRST_POINT = f'max({{__name__=~"barman_cloud_cloudnative_pg_io_first_recoverability_point|cnpg_collector_first_recoverability_point", {SEL}}} > 0)'
# Only the primary archives WAL.
LAST_ARCHIVE_AGE = f"max((1 - cnpg_pg_replication_in_recovery{{{SEL}}}) * on (pod) cnpg_pg_stat_archiver_seconds_since_last_archival{{{SEL}}})"
WAL_READY = f'max(cnpg_collector_pg_wal_archive_status{{value="ready", {SEL}}})'


def steps(*pairs):
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in pairs]}


def stat(pid, title, expr, unit, th, x, y, desc, mappings=(), decimals=1):
    return {"id": pid, "type": "stat", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"h": 4, "w": 4, "x": x, "y": y},
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": th,
                                         "mappings": list(mappings), "noValue": "–"}, "overrides": []},
            "options": {"colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
            "targets": [{"datasource": PROM, "expr": expr, "refId": "A", "instant": True, "range": False}]}


def series(pid, title, targets, unit, x, y, desc, th=None, overrides=(), bars=False):
    custom = {"drawStyle": "bars" if bars else "line", "fillOpacity": 60 if bars else 10, "lineWidth": 1,
              "showPoints": "never", "spanNulls": True}
    if th:
        custom["thresholdsStyle"] = {"mode": "dashed"}
    return {"id": pid, "type": "timeseries", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
            "fieldConfig": {"defaults": {"unit": unit, "custom": custom, **({"thresholds": th} if th else {})},
                            "overrides": list(overrides)},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "targets": [dict(t, datasource=PROM, refId=chr(65 + i), range=True) for i, t in enumerate(targets)]}


def color(name, value):
    return {"matcher": {"id": "byName", "options": name},
            "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": value}}]}


def backup_panels(y):
    """Backups row: is the latest backup recent, how far back can we restore, how much data is at risk."""
    never = {"type": "range", "options": {"from": 1e9, "to": None, "result": {"text": "never", "color": "red"}}}
    return [
        stat(9001, "Last successful backup", f"time() - {LAST_BACKUP}", "s",
             steps(("green", None), ("orange", 93600), ("red", 172800)), 0, y,
             "Age of the newest base backup in the object store. The ScheduledBackup runs daily at 02:00 UTC; "
             "orange after 26h, red after 48h.", mappings=[never]),
        stat(9002, "Last backup attempt", f"{LAST_FAILED} > bool {LAST_BACKUP}", "none",
             steps(("green", None), ("red", 1)), 4, y,
             "Failed when a backup failed after the newest successful one (see the events below).",
             mappings=[{"type": "value", "options": {"0": {"text": "Succeeded", "color": "green"},
                                                     "1": {"text": "Failed", "color": "red"}}}], decimals=0),
        stat(9003, "Recovery window", f"time() - {FIRST_POINT}", "s", steps(("blue", None)), 8, y,
             "How far back point-in-time recovery can go: from the oldest backup kept by the retention policy "
             "(7d) up to the last archived WAL."),
        stat(9004, "Data at risk (RPO)", LAST_ARCHIVE_AGE, "s",
             steps(("green", None), ("orange", 360), ("red", 900)), 12, y,
             "Age of the last WAL archived from the primary: losing the whole cluster now would lose the writes "
             "since then. archive_timeout forces an archive at least every 5 minutes."),
        stat(9005, "WAL waiting to archive", WAL_READY, "none",
             steps(("green", None), ("orange", 5), ("red", 20)), 16, y,
             "WAL segments written but not yet in the object store. A growing number means archiving is slower "
             "than WAL generation or failing; pg_wal then grows until the disk is full.", decimals=0),
        stat(9006, "WAL archive failures", f"sum(increase(cnpg_pg_stat_archiver_failed_count{{{SEL}}}[$__range]))",
             "none", steps(("green", None), ("orange", 1)), 20, y,
             "Failed archive attempts in the selected time range. A few right after a restart are retried; "
             "sustained failures fire PostgresWalArchivingFailing.", decimals=0),
        series(9007, "Time since last successful backup", [{"expr": f"time() - {LAST_BACKUP}", "legendFormat": "$cluster"}],
               "s", 0, y + 4, "Drops to zero at every successful backup; the dashed line is the 26h alert threshold "
               "(PostgresBackupTooOld).", th=steps(("green", None), ("red", 93600))),
        series(9008, "Recovery window (point-in-time recovery)", [{"expr": f"time() - {FIRST_POINT}", "legendFormat": "$cluster"}],
               "s", 12, y + 4, "Grows until it reaches the retention policy; the dashed line is the 7d retention.",
               th=steps(("blue", None), ("purple", 604800))),
        series(9009, "WAL archived and failed",
               [{"expr": f"sum(increase(cnpg_pg_stat_archiver_archived_count{{{SEL}}}[$__rate_interval]))", "legendFormat": "archived"},
                {"expr": f"sum(increase(cnpg_pg_stat_archiver_failed_count{{{SEL}}}[$__rate_interval]))", "legendFormat": "failed"}],
               "none", 0, y + 12, "WAL segments shipped to the object store per interval.", bars=True,
               overrides=[color("archived", "green"), color("failed", "red")]),
        series(9010, "pg_wal: waiting to archive and size",
               [{"expr": WAL_READY, "legendFormat": "waiting to archive"},
                {"expr": f'max by (pod) (cnpg_collector_pg_wal{{value="size", {SEL}}})', "legendFormat": "pg_wal size {{pod}}"}],
               "none", 12, y + 12, "Segments waiting for the archiver (left) and the pg_wal directory size (right). "
               "Both rise together when archiving stalls.",
               overrides=[{"matcher": {"id": "byRegexp", "options": "^pg_wal size"},
                           "properties": [{"id": "unit", "value": "bytes"},
                                          {"id": "custom.axisPlacement", "value": "right"}]}]),
        {"id": 9011, "type": "logs", "title": "Backup, retention and archiving events (Barman Cloud sidecar)",
         "description": "Base backups (start, completion, backup ID), retention runs and errors from the plugin sidecar. "
                        "Routine \"Archived WAL file\" lines are left out.",
         "datasource": LOGS, "gridPos": {"h": 9, "w": 24, "x": 0, "y": y + 20},
         "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "sortOrder": "Descending",
                     "dedupStrategy": "none"},
         "targets": [{"datasource": LOGS, "refId": "A", "queryType": "instant", "editorMode": "code", "maxLines": 200,
                      "expr": '{kubernetes.pod_namespace="$namespace", kubernetes.container_name="plugin-barman-cloud"} '
                              '_msg:~"(?i)backup|retention|error|fail"'}]},
    ]


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

for row in dash["panels"]:
    if row["type"] == "row" and row["title"] == "Backups":
        assert row.get("collapsed"), "the Backups row is expected to be collapsed (panels nested in it)"
        row["panels"] = backup_panels(row["gridPos"]["y"] + 1)

with open(OUT, "w") as f:
    json.dump(dash, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"wrote {OUT}")
