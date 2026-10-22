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
- New rows after "Operational Stats": "Queries and errors" (slow statements and errors by SQLSTATE from the
  PostgreSQL logs in VictoriaLogs) and "Database efficiency" (cache hit, rollback, temp files, conflicts).
- Replication row: bytes behind the primary per standby and WAL retained by replication slots.
- Annotations: instance (re)starts, role changes (failover/switchover) and firing critical alerts.
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


def stat(pid, title, expr, unit, th, x, y, desc, mappings=(), decimals=1, w=4):
    return {"id": pid, "type": "stat", "title": title, "description": desc, "datasource": PROM,
            "gridPos": {"h": 4, "w": w, "x": x, "y": y},
            "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals, "thresholds": th,
                                         "mappings": list(mappings), "noValue": "–"}, "overrides": []},
            "options": {"colorMode": "background", "graphMode": "none", "textMode": "value", "justifyMode": "center",
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
            "targets": [{"datasource": PROM, "expr": expr, "refId": "A", "instant": True, "range": False}]}


def series(pid, title, targets, unit, x, y, desc, th=None, overrides=(), bars=False, ds=PROM, stack=False):
    custom = {"drawStyle": "bars" if bars else "line", "fillOpacity": 60 if bars else 10, "lineWidth": 1,
              "showPoints": "never", "spanNulls": True, "stacking": {"mode": "normal" if stack else "none"}}
    if th:
        custom["thresholdsStyle"] = {"mode": "dashed"}
    return {"id": pid, "type": "timeseries", "title": title, "description": desc, "datasource": ds,
            "gridPos": {"h": 8, "w": 12, "x": x, "y": y},
            "fieldConfig": {"defaults": {"unit": unit, "custom": custom, **({"thresholds": th} if th else {})},
                            "overrides": list(overrides)},
            "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                        "tooltip": {"mode": "multi", "sort": "desc"}},
            "targets": [dict(t, datasource=ds, refId=chr(65 + i), **({"range": True} if ds is PROM else {}))
                        for i, t in enumerate(targets)]}


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
               overrides=[{"matcher": {"id": "byFrameRefID", "options": "B"},
                           "properties": [{"id": "unit", "value": "bytes"},
                                          {"id": "custom.axisPlacement", "value": "right"}]}]),
        {"id": 9011, "type": "logs", "title": "Backup, retention and archiving events (Barman Cloud sidecar)",
         "description": "Base backups (start, completion, backup ID), retention runs and errors from the plugin sidecar. "
                        "Routine \"Archived WAL file\" lines are left out. \"WAL file ... does not exist\" while a "
                        "standby starts is expected: it asks the archive for WAL the primary has not archived yet.",
         "datasource": LOGS, "gridPos": {"h": 9, "w": 24, "x": 0, "y": y + 20},
         "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "sortOrder": "Descending",
                     "dedupStrategy": "none"},
         "targets": [{"datasource": LOGS, "refId": "A", "queryType": "instant", "editorMode": "code", "maxLines": 200,
                      "expr": '{kubernetes.pod_namespace="$namespace", kubernetes.container_name="plugin-barman-cloud"} '
                              '_msg:~"(?i)backup|retention|error|fail"'}]},
    ]


# PostgreSQL server logs (JSON, shipped by vlagent) of the selected CloudNativePG cluster.
PG_LOGS = ('{kubernetes.pod_namespace="$namespace", kubernetes.container_name="postgres"} '
           '"kubernetes.pod_labels.cnpg.io/cluster":in($cluster)')
DB_SEL = f'{SEL}, datname!~"template0|template1"'


def rate(metric, by=""):
    grouping = f" by ({by})" if by else ""
    return f"sum{grouping} (rate({metric}{{{DB_SEL}}}[$__rate_interval]))"


def log_panels(y):
    """Which statements are slow and which errors PostgreSQL raises, from its own logs."""
    slow = (f'{PG_LOGS} record.message:"duration:" '
            '| extract "duration: <duration_ms> ms  <kind>: <statement>" from record.message '
            '| stats by (statement) count() calls, max(duration_ms) max_ms, avg(duration_ms) avg_ms '
            '| sort by (max_ms desc) | limit 20')
    errors = f"{PG_LOGS} record.error_severity:in(ERROR, FATAL, PANIC)"
    ms = lambda name: {"matcher": {"id": "byName", "options": name},
                       "properties": [{"id": "unit", "value": "ms"}, {"id": "decimals", "value": 1},
                                      {"id": "custom.width", "value": 110}]}
    return [
        {"id": 9101, "type": "table", "title": "Slow statements (over log_min_duration_statement)",
         "description": "Statements slower than log_min_duration_statement (250ms), aggregated from the PostgreSQL "
                        "logs over the selected range. Backup start/stop (pg_backup_start/stop) is expected here.",
         "datasource": LOGS, "gridPos": {"h": 8, "w": 24, "x": 0, "y": y},
         "fieldConfig": {"defaults": {"custom": {"cellOptions": {"type": "auto"}, "inspect": True}},
                         "overrides": [ms("Max"), ms("Avg"),
                                       {"matcher": {"id": "byName", "options": "Calls"},
                                        "properties": [{"id": "custom.width", "value": 80}]}]},
         "options": {"showHeader": True, "cellHeight": "sm", "sortBy": [{"displayName": "Max", "desc": True}]},
         "transformations": [
             {"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
             {"id": "merge", "options": {}},
             {"id": "organize", "options": {
                 "excludeByName": {"Time": True},
                 "indexByName": {"statement": 0, "calls": 1, "max_ms": 2, "avg_ms": 3},
                 "renameByName": {"statement": "Statement", "calls": "Calls", "max_ms": "Max", "avg_ms": "Avg"}}}],
         "targets": [{"datasource": LOGS, "refId": "A", "queryType": "stats", "editorMode": "code", "expr": slow}]},
        series(9102, "Errors by severity and SQLSTATE",
               [{"expr": f"{errors} | stats by (record.error_severity, record.sql_state_code) count() errors",
                 "queryType": "statsRange", "editorMode": "code",
                 "legendFormat": "{{record.error_severity}} {{record.sql_state_code}}"}],
               "short", 0, y + 8, "ERROR/FATAL/PANIC log lines. Common SQLSTATEs: 57P03 server starting up (restarts), "
               "40P01 deadlock, 23505 unique violation, 53300 too many connections, 57014 statement timeout.",
               bars=True, stack=True, ds=LOGS),
        {"id": 9103, "type": "logs", "title": "Latest errors", "datasource": LOGS,
         "description": "The newest ERROR/FATAL/PANIC lines; expand one for the SQLSTATE, user, database and query.",
         "gridPos": {"h": 8, "w": 12, "x": 12, "y": y + 8},
         "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "sortOrder": "Descending",
                     "dedupStrategy": "none"},
         "targets": [{"datasource": LOGS, "refId": "A", "queryType": "instant", "editorMode": "code", "maxLines": 100,
                      "expr": f'{errors} | format "<record.error_severity> <record.sql_state_code> [<kubernetes.pod_name>]: '
                              f'<record.message>" as _msg | copy record.error_severity as level'}]},
    ]


def efficiency_panels(y):
    """Signals that the database is short on memory or that the workload misbehaves."""
    hit, read = "cnpg_pg_stat_database_blks_hit", "cnpg_pg_stat_database_blks_read"
    commit, rollback = "cnpg_pg_stat_database_xact_commit", "cnpg_pg_stat_database_xact_rollback"
    return [
        stat(9111, "Cache hit ratio", f"{rate(hit)} / ({rate(hit)} + {rate(read)})", "percentunit",
             steps(("red", None), ("orange", 0.9), ("green", 0.99)), 0, y,
             "Share of block reads served from shared_buffers. Below 99% on a steady workload: the working set "
             "does not fit, consider more shared_buffers/memory.", decimals=2, w=6),
        stat(9112, "Rollback ratio", f"{rate(rollback)} / ({rate(commit)} + {rate(rollback)})", "percentunit",
             steps(("green", None), ("orange", 0.05), ("red", 0.2)), 6, y,
             "Share of transactions rolled back. A jump usually means application errors (constraint violations, "
             "deadlocks, timeouts) — see Errors by SQLSTATE.", decimals=2, w=6),
        stat(9113, "Temporary files", f"sum(increase(cnpg_pg_stat_database_temp_files{{{DB_SEL}}}[$__range]))", "none",
             steps(("green", None), ("orange", 1)), 12, y,
             "Sorts and hashes that spilled to disk in the selected range: those queries need more work_mem "
             "(or an index).", decimals=0, w=6),
        stat(9114, "Recovery conflicts", f"sum(increase(cnpg_pg_stat_database_conflicts{{{DB_SEL}}}[$__range]))", "none",
             steps(("green", None), ("orange", 1)), 18, y,
             "Queries cancelled on a standby because replayed changes conflicted with them, in the selected range.",
             decimals=0, w=6),
        series(9115, "Cache hit ratio by database",
               [{"expr": f"{rate(hit, 'datname')} / ({rate(hit, 'datname')} + {rate(read, 'datname')})",
                 "legendFormat": "{{datname}}"}],
               "percentunit", 0, y + 4, "The dashed line is the 99% target.", th=steps(("red", None), ("green", 0.99))),
        series(9116, "Commits and rollbacks per second",
               [{"expr": rate(commit, "datname"), "legendFormat": "commit {{datname}}"},
                {"expr": rate(rollback, "datname"), "legendFormat": "rollback {{datname}}"}],
               "ops", 12, y + 4, "Transactions per second by database."),
    ]


def replication_panels(y):
    """Lag in bytes stays meaningful when traffic is low (time-based lag then reads 0 or jumps)."""
    stages = [{"expr": f"max by (application_name) (cnpg_pg_stat_replication_{s}_diff_bytes{{{SEL}}})",
               "legendFormat": f"{{{{application_name}}}} {s}"} for s in ("sent", "write", "flush", "replay")]
    return [
        series(9121, "Bytes behind the primary", stages, "bytes", 0, y,
               "WAL not yet sent, written, flushed or replayed by each standby, as seen by the primary."),
        series(9122, "WAL retained by replication slots",
               [{"expr": f"max by (slot_name) (cnpg_pg_replication_slots_pg_wal_lsn_diff{{{SEL}}})",
                 "legendFormat": "{{slot_name}}"}],
               "bytes", 12, y, "WAL kept on the primary for each slot. A slot whose consumer is gone retains WAL "
               "forever and eventually fills the disk."),
    ]


def changes_annotation(name, metric, title, icon):
    return {"name": name, "datasource": PROM, "enable": True, "iconColor": icon, "step": "1m",
            "expr": f"changes(max by (pod) ({metric}{{{SEL}}})[1m]) > 0",
            "titleFormat": title, "useValueForTime": False}


ANNOTATIONS = [
    changes_annotation("Instance (re)starts", "cnpg_pg_postmaster_start_time", "{{pod}} (re)started", "orange"),
    changes_annotation("Role changes", "cnpg_pg_replication_in_recovery", "{{pod}} promoted or demoted", "purple"),
    {"name": "Critical alerts", "datasource": PROM, "enable": True, "iconColor": "red", "step": "1m",
     "expr": 'max by (alertname, job, pod, deployment, slo) '
             '(ALERTS{alertname=~"Postgres.*|Shop.*|Redis.*", alertstate="firing", severity="critical"})',
     "titleFormat": "{{alertname}}", "textFormat": "{{pod}}{{job}}{{deployment}} {{slo}}", "useValueForTime": False},
]


def insert_collapsed_rows(dash, before_title, rows):
    """Insert collapsed rows (title, panels) right before the row titled before_title."""
    anchor = next(p for p in dash["panels"] if p["type"] == "row" and p["title"] == before_title)
    y0 = anchor["gridPos"]["y"]
    for p in dash["panels"]:
        if p["gridPos"]["y"] >= y0:
            p["gridPos"]["y"] += len(rows)
            for child in p.get("panels", []):
                child["gridPos"]["y"] += len(rows)
    for i, (pid, title, build) in enumerate(rows):
        dash["panels"].append({"id": pid, "type": "row", "title": title, "collapsed": True,
                               "gridPos": {"h": 1, "w": 24, "x": 0, "y": y0 + i}, "panels": build(y0 + i + 1)})
    dash["panels"].sort(key=lambda p: (p["gridPos"]["y"], p["gridPos"]["x"]))


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
    if row["type"] == "row" and row["title"] == "Replication":
        assert row.get("collapsed"), "the Replication row is expected to be collapsed (panels nested in it)"
        bottom = max(p["gridPos"]["y"] + p["gridPos"]["h"] for p in row["panels"])
        row["panels"] += replication_panels(bottom)

insert_collapsed_rows(dash, "Storage & I/O", [
    (9100, "Queries and errors (PostgreSQL logs)", log_panels),
    (9110, "Database efficiency", efficiency_panels),
])
dash["annotations"]["list"] += ANNOTATIONS

with open(OUT, "w") as f:
    json.dump(dash, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"wrote {OUT}")
