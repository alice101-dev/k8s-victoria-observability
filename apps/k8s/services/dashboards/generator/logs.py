"""Shop — Logs: volume and error trends, message patterns, facets, pods and versions, one request, explorer, pipeline."""
from .common import (ADHOC, APP_LOGS, COUNT, LEVEL_COLORS, LINKS_ON, PCT, RI, SEARCH, SEARCH_VAR, VL, WHEN,
    by_name, Dash, dashboard, level_colors, logs_panel, logsql_table, right_axis, status_history, thresholds,
    ts, vl, vl_stat, vm)

FILE = "shop-logs.json"

# Every panel follows the Service variable, the message search and the ad hoc Filters. The Level variable narrows
# the panels that list lines (patterns, facets, request picker, explorer); overview and trends split by level.
d = Dash()
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
                  {"title": "Open VictoriaLogs - single-node", "url": "/d/OqPIZTX4z/victorialogs-single-node?${__url_time_range}"}]
LEVEL_CELL = by_name("Level", [{"id": "custom.width", "value": 70}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "mappings", "value": [{"type": "value", "options": {
                                   lvl: {"color": c, "index": i} for i, (lvl, c) in enumerate(LEVEL_COLORS.items())}}]}])
GAUGE = lambda name, unit="short", max_=None, width=150: by_name(name, [
    {"id": "unit", "value": unit}, {"id": "custom.width", "value": width}, {"id": "min", "value": 0},
    {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
    {"id": "color", "value": {"mode": "continuous-BlYlRd"}}] + ([{"id": "max", "value": max_}] if max_ else []))




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
# Request picker: the latest lines of the selected levels that carry a trace ID, labelled
# "<time> · <service> · <message> · <trace id>" and sorted newest first; the regex keeps the trace ID as the value.
# The values limit (100) stays above "limit 30": in cluster mode field_values runs the pipes on every storage node.
REQUEST_PICK = (f'{LOGS_LEVEL} trace_id:* | sort by (_time desc) | limit 30 | format "<_time>" as t '
                r'| replace ("T", " ") at t | replace_regexp ("\\.[0-9]+Z$", " UTC") at t '
                '| format "<t> · <service> · <_msg> · <trace_id>" as option')
REQUEST_VAR = {"name": "request", "label": "Request (latest first)", "type": "query", "datasource": VL,
               "query": {"type": "fieldValue", "field": "option", "query": REQUEST_PICK, "limit": 100, "refId": "request"},
               "definition": "option: latest lines with a trace ID", "regex": "/^(?<text>.* · (?<value>[0-9a-f]{32}))$/",
               "sort": 2, "refresh": 2, "multi": False, "includeAll": False, "allowCustomValue": True, "current": {}}
DASHBOARD = dashboard(
    "shop-logs", "Shop — Logs", ["shop", "logs"], d.panels, [LOG_SERVICE, LEVEL, SEARCH_VAR, ADHOC, REQUEST_VAR],
    "Application logs of the shop services from VictoriaLogs: volume and error trends, message patterns, what failing "
    "lines have in common, pods and versions, one request across services, the explorer and the log pipeline.", "now-1h")
