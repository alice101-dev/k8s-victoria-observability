from .common import (ADHOC, COUNT, LEVEL_COLORS, LINKS_ON, PCT, SEARCH, SEARCH_VAR, VL, WHEN, by_name, Dash, dashboard,
    describe, level_colors, logs_panel, logsql_table, status_history, thresholds, ts, vl, vl_stat)
from .deus import ANNOTATIONS, LINKS_DEUS, LOGS as APP_LOGS

FILE = "deus-logs.json"

d = Dash()
LOGS = f"{APP_LOGS} service.name:in($service) {SEARCH}"
LOGS_LEVEL = f"{LOGS} severity_text:in($level)"
ERR = "severity_text:=ERROR"
NAMES = '| copy service.name as service, severity_text as level, exception.message as err '
READABLE = '| format if (exception.message:*) "<_msg> — <exception.message>" as _msg | format "[<service.name>] <_msg>" as _msg'
SELF = "/d/deus-logs/deus-logs?${service:queryparam}&${level:queryparam}&${__url_time_range}"
MESSAGE_LINK = [{"title": "Show only lines with this message", "url": SELF + "&var-search=${__data.fields.search_key:percentencode}"}]
POD_LINK = [{"title": "Show this pod's lines in the explorer",
             "url": SELF + "&${search:queryparam}&var-filters=k8s.pod.name%7C%3D%7C${__value.raw}"}]
SERVICE_DASH = [{"title": "Open Deus — Service", "url": "/d/deus-service/deus-service?var-service=${__value.raw}&${__url_time_range}"}]
PIPELINE_LINK = [{"title": "Open Observability pipeline", "url": "/d/observability-pipeline/observability-pipeline?${__url_time_range}"}]
LEVEL_CELL = by_name("Level", [{"id": "custom.width", "value": 70}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "mappings", "value": [{"type": "value", "options": {
                                   lvl: {"color": c, "index": i} for i, (lvl, c) in enumerate(LEVEL_COLORS.items())}}]}])
GAUGE = lambda name, unit="short", max_=None, width=150: by_name(name, [
    {"id": "unit", "value": unit}, {"id": "custom.width", "value": width}, {"id": "min", "value": 0},
    {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
    {"id": "color", "value": {"mode": "continuous-BlYlRd"}}] + ([{"id": "max", "value": max_}] if max_ else []))

d.row("Overview — $service")
for p in (
    vl_stat("Log records", f"{LOGS} | stats count() lines", "short", thresholds(("blue", None)),
            "Records the selected services logged in the time range (all levels)."),
    vl_stat("Warnings", f"{LOGS} severity_text:=WARN | stats count() warnings", "short", thresholds(("green", None), ("orange", 1)),
            "WARN records: mostly rejected requests (validation, unknown document, business rules), see Patterns."),
    vl_stat("Errors", f"{LOGS} {ERR} | stats count() errors", "short", thresholds(("green", None), ("red", 1)),
            "ERROR records: a request or a background job failed for a technical reason."),
    vl_stat("Error ratio", f"{LOGS} | stats count() if ({ERR}) errors, count() lines "
                           "| math errors*100/lines as error_ratio | delete errors, lines",
            "percent", thresholds(("green", None), ("orange", 0.1), ("red", 1)),
            "Share of ERROR records among all records of the selected services.", sparkline=False, decimals=2, no_value="–"),
    vl_stat("Services logging errors", f"{LOGS} {ERR} | stats count_uniq(service.name) services", "short",
            thresholds(("green", None), ("red", 1)), "Services with at least one ERROR record. Several at the same time "
            "usually means a shared dependency: the database, a called service, the network.", sparkline=False),
    vl_stat("Browser errors", f'{APP_LOGS} service.name:=deus-dashboard telemetry.source:=browser {ERR} | stats count() errors', "short",
            thresholds(("green", None), ("orange", 1)), "Errors the web app reported from users' browsers: unhandled "
            "exceptions, failed loads and failed requests."),
):
    d.add(p, 4, 4)

d.row("Trends")
d.add(ts("Records by level", [vl(f"{LOGS} | stats by (severity_text) count() lines", "statsRange", legend="{{severity_text}}")],
         "short", ds=VL, stack=True, bars=True, overrides=level_colors(), legend_calcs=("sum",)), 12, 8)
d.add(ts("Error ratio by service", [vl(f"{LOGS} | stats by (service.name) count() if ({ERR}) errors, count() lines "
                                       "| math round(errors*100/lines, 0.01) as error_ratio | delete errors, lines",
                                       "statsRange", legend="{{service.name}}")],
         "percent", ds=VL, min_=0, soft_max=1, desc="Share of ERROR records per service. A service with more traffic does "
         "not look worse here; compare with the Errors per service timeline below."), 12, 8)
d.add(status_history("Errors per service",
                     vl(f"{LOGS} | stats by (service.name) count() if ({ERR}) errors", "statsRange", legend="{{service.name}}"),
                     "ERROR records per service and interval: when each service started and stopped failing, and which "
                     "failed first. Rollouts and critical alerts are marked on the graphs above.",
                     thresholds(("green", None), ("orange", 1), ("red", 10))), 24, 7)

d.row("Patterns — $level records")
NUMS = (r'| replace_regexp ("\\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\\b", "<ID>") at err '
        r'| replace_regexp ("\\b[0-9]+\\b", "<N>") at _msg | replace_regexp ("\\b[0-9]+\\b", "<N>") at err ')
PATTERNS = (f"{LOGS_LEVEL} {NAMES}{NUMS}"
            '| extract_regexp "^(?P<search_key>[^<]*)" from _msg '
            "| stats by (service, level, _msg, err, search_key) count() lines, count_uniq(k8s.pod.name) pods, "
            "min(_time) first, max(_time) last "
            "| total_stats sum(lines) total "
            "| math round(lines*100/total, 0.01) as share, round(first/1e6) as first_ms, round(last/1e6) as last_ms "
            "| delete total, first, last | sort by (lines desc) | limit 50")
d.add(logsql_table("Message patterns", PATTERNS,
                   [("service", "Service"), ("level", "Level"), ("_msg", "Message"), ("err", "Cause"), ("lines", "Records"),
                    ("share", "Share"), ("pods", "Pods"), ("first_ms", "First seen"), ("last_ms", "Last seen"),
                    ("search_key", "search_key")],
                   "What the selected records say, grouped by pattern (numbers and IDs replaced), with the cause "
                   "(exception.message). Click a message to keep only its records.",
                   VL, overrides=[LINKS_ON("Service", SERVICE_DASH, 130), LEVEL_CELL, LINKS_ON("Message", MESSAGE_LINK, 220),
                                  GAUGE("Records"), GAUGE("Share", "percent", 100, 110), COUNT("Pods", 60),
                                  WHEN("First seen", "dateTimeAsLocal"), WHEN("Last seen", "dateTimeFromNow"),
                                  by_name("search_key", [{"id": "custom.hidden", "value": True}])],
                   sort=[{"displayName": "Records", "desc": True}]), 24, 10)
d.add(ts("Patterns over time", [vl(f"{LOGS_LEVEL} {NAMES}{NUMS}| stats by (service, _msg) count() lines",
                                   "statsRange", legend="{{service}}: {{_msg}}")],
         "short", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
         desc="The same patterns per interval: a new pattern appearing, or one jumping, marks the start of a problem."), 12, 10)
FACET_SKIP = ["_msg", "exception.message", "exception.stacktrace", "exception.cause", "_time", "_stream", "_stream_id",
              "trace_id", "span_id", "k8s.pod.uid", "k8s.replicaset.uid", "k8s.replicaset.name", "k8s.pod.start_time",
              "k8s.cluster.uid", "k8s.namespace.name", "k8s.container.name", "service.namespace", "service.instance.id",
              "deployment.environment.name", "container.image.name", "scope.version", "severity_number"]
FACETS = (f"{LOGS_LEVEL} | delete {', '.join(FACET_SKIP)} | facets 5 keep_const_fields "
          "| stats by (field_name, field_value) sum(hits) lines "
          '| format if (field_name:=severity_text) "<lines>" as level_lines | total_stats sum(level_lines) total '
          "| math round(lines*100/total, 0.1) as share | delete level_lines, total | filter -field_name:=severity_text "
          "| sort by (share desc) | limit 60")
d.add(logsql_table("What these records have in common", FACETS,
                   [("field_name", "Field"), ("field_value", "Value"), ("lines", "Records"), ("share", "Share")],
                   "Most frequent values of each field in the selected records and the share carrying them. One route, "
                   "error code, pod, node or version near 100% points at the cause.",
                   VL, overrides=[by_name("Field", [{"id": "custom.width", "value": 150}]), COUNT("Records"),
                                  GAUGE("Share", "percent", 100, 160)],
                   sort=[{"displayName": "Share", "desc": True}]), 12, 10)

d.row("Where — pods, nodes and versions")
d.add(logsql_table("Records by pod",
                   f"{LOGS} | stats by (k8s.pod.name, k8s.node.name, service.version) count() lines, "
                   f"count() if (severity_text:=WARN) warnings, count() if ({ERR}) errors "
                   "| math round(errors*100/lines, 0.01) as error_ratio | sort by (errors desc, warnings desc) | limit 30",
                   [("k8s.pod.name", "Pod"), ("k8s.node.name", "Node"), ("service.version", "Version"), ("lines", "Records"),
                    ("warnings", "Warnings"), ("errors", "Errors"), ("error_ratio", "Error ratio")],
                   "One pod, node or version with more errors than its peers points at the pod, the node or a rollout "
                   "rather than the code path. Click a pod to list its records in the explorer.",
                   VL, overrides=[LINKS_ON("Pod", POD_LINK, 270), COUNT("Records"), COUNT("Warnings"), COUNT("Errors"),
                                  PCT("Error ratio", 0.1, 1)],
                   sort=[{"displayName": "Errors", "desc": True}]), 24, 8)
d.add(ts("Warnings and errors by version", [vl(f"{LOGS} severity_text:in(ERROR, WARN) "
                                               "| stats by (service.version, severity_text) count() lines",
                                               "statsRange", legend="{{service.version}} {{severity_text}}")],
         "short", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
         desc="Records per image version (service.version). A new version bringing new errors shows here at the rollout."), 24, 7)

d.row("Request — ${request:text}")
p = d.add(logs_panel("All records of one request, oldest first", f'{APP_LOGS} trace_id:="${{request:raw}}" trace_id:* {READABLE}',
                     "Every service's records with the trace ID picked in the Request variable. The panel link opens "
                     "the trace."), 24, 9)
p["options"]["sortOrder"] = "Ascending"
p["links"] = [{"title": "Open the trace in Deus — Traces", "url": "/d/deus-traces/deus-traces?var-trace_id=${request}&${__url_time_range}"}]

d.row("Log explorer")
d.add(logs_panel("Records — $level", f"{LOGS_LEVEL} {READABLE}", "Expand a record for its fields; trace_id links to the "
                 "trace. Narrow with Message contains and Filters (any field)."), 24, 18)

d.row("Log pipeline — is every record arriving?")
for p in (
    d.add(logsql_table("Last record per service", f"{APP_LOGS} service.name:in($service) | stats by (service.name) max(_time) last, "
                       "count() lines | math round(last/1e6) as last_ms | delete last",
                       [("service.name", "Service"), ("last_ms", "Last record"), ("lines", "Records")],
                       "When each service last logged. Services log every rejected or failed request and the outbox "
                       "relay's work; one that stops logging while it serves traffic has lost its OTLP export or hangs.",
                       VL, overrides=[WHEN("Last record", "dateTimeFromNow"), COUNT("Records")],
                       sort=[{"displayName": "Last record", "desc": False}]), 12, 8),
    d.add(ts("Log bytes by service", [vl(f"{APP_LOGS} service.name:in($service) | stats by (service.name) sum_len(*) bytes",
                                         "statsRange", legend="{{service.name}}")],
             "bytes", ds=VL, stack=True, bars=True, legend_calcs=("sum",),
             desc="Uncompressed size of all fields per interval: who drives log volume (storage and cost)."), 12, 8),
):
    p["links"] = PIPELINE_LINK

for p in d.panels:
    if p["type"] in ("timeseries", "status-history") and p["datasource"] is VL:
        p["interval"] = "1m"

LEVEL = {"name": "level", "label": "Level", "type": "custom", "query": "ERROR,WARN,INFO,DEBUG",
         "multi": True, "includeAll": True,
         "current": {"selected": True, "text": ["ERROR", "WARN"], "value": ["ERROR", "WARN"]},
         "options": [{"selected": v in ("ERROR", "WARN"), "text": v, "value": v} for v in ("ERROR", "WARN", "INFO", "DEBUG")]}
LOG_SERVICE = {"name": "service", "label": "Service", "type": "query", "datasource": VL,
               "query": {"type": "fieldValue", "field": "service.name", "query": APP_LOGS, "limit": 100, "refId": "service"},
               "definition": f"service.name: {APP_LOGS}", "refresh": 2, "sort": 1, "multi": True, "includeAll": True,
               "current": {"selected": True, "text": ["All"], "value": ["$__all"]}}
REQUEST_PICK = (f'{LOGS_LEVEL} trace_id:* | sort by (_time desc) | limit 30 | format "<_time>" as t '
                r'| replace ("T", " ") at t | replace_regexp ("\\.[0-9]+Z$", " UTC") at t '
                '| format "<t> · <service.name> · <_msg> · <trace_id>" as option')
REQUEST_VAR = {"name": "request", "label": "Request (latest first)", "type": "query", "datasource": VL,
               "query": {"type": "fieldValue", "field": "option", "query": REQUEST_PICK, "limit": 100, "refId": "request"},
               "definition": "option: latest records with a trace ID", "regex": "/^(?<text>.* · (?<value>[0-9a-f]{32}))$/",
               "sort": 2, "refresh": 2, "multi": False, "includeAll": False, "allowCustomValue": True, "current": {}}
describe(d.panels, {
    "Records by level": "Log records of the selected services per interval by level, after the message search.",
})
DASHBOARD = dashboard(
    "deus-logs", "Deus — Logs", ["deus", "logs"], d.panels, [LOG_SERVICE, LEVEL, SEARCH_VAR, ADHOC, REQUEST_VAR],
    "Application logs of the deus services and the browser from VictoriaLogs: volume and error trends, message "
    "patterns, what failing records have in common, pods and versions, one request across services and the explorer.",
    "now-1h", links=LINKS_DEUS, annotations=ANNOTATIONS)
