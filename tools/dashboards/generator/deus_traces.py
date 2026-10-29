from .common import (COUNT, LINKS_ON, PCT, VT, VTL, by_name, code_colors, Dash, dashboard, describe, logsql_table, stat,
    thresholds, ts, vl)
from .deus import ANNOTATIONS, LINKS_DEUS, SPANS as APP_SPANS

FILE = "deus-traces.json"

SPANS = f'{APP_SPANS} "resource_attr:service.name":in($service)'
SERVER = f"{SPANS} kind:=2"
SVC = '| copy "resource_attr:service.name" as service'
CODE = '"span_attr:http.response.status_code"'
IS_5XX, IS_4XX = f'{CODE}:~"^5"', f'{CODE}:~"^4"'
FAILED = f"status_code:=2 -{IS_4XX}"
IS_SQL = '"span_attr:db.system.name":=postgresql'
IN_ENDPOINT_TRACES = f'trace_id:in({APP_SPANS} kind:=2 name:="${{endpoint:raw}}" | fields trace_id)'
OPERATION = ('| copy "span_attr:db.query.text" as sql | replace_regexp ("[ \\t\\n]+", " ") at sql '
             '| format "<name>" as operation | format if (sql:*) "SQL <sql>" as operation')
KEEP_VARS = "${service:queryparam}&${endpoint:queryparam}&${attribute:queryparam}&${min_duration:queryparam}&${__url_time_range}"
TRACE_LINK = [{"title": "Show this trace below", "url": f"/d/deus-traces/deus-traces?{KEEP_VARS}&var-trace_id=${{__value.raw}}"}]
ENDPOINT_LINK = [{"title": "Break down this endpoint (Where the time goes)",
                  "url": "/d/deus-traces/deus-traces?var-service=${__data.fields.Service}&var-endpoint=${__value.raw}"
                         "&${attribute:queryparam}&${min_duration:queryparam}&${__url_time_range}"}]
SERVICE_DASH_LINK = [{"title": "Open Deus — Service", "url": "/d/deus-service/deus-service?var-service=${__value.raw}&${__url_time_range}"}]
NS_UNIT = lambda name, width=90: by_name(name, [{"id": "unit", "value": "ns"}, {"id": "decimals", "value": 1},
                                                 {"id": "custom.width", "value": width}])


def lq_table(title, expr, columns, desc, overrides=(), sort=None, hide=()):
    return logsql_table(title, expr, columns, desc, VTL, overrides, sort, hide)


def lq_stat(title, expr, unit, th, desc, decimals=1):
    p = stat(title, "", unit, th, desc, decimals)
    p["datasource"], p["targets"] = VTL, [vl(expr, "stats", ds=VTL)]
    return p


d = Dash()
d.row("Overview — $service")
d.add(lq_stat("Requests / s", f"{SERVER} | stats rate() rps", "reqps", thresholds(("blue", None)),
              "Server spans per second: every request handled by the selected services, /internal calls included."), 4, 4)
d.add(lq_stat("5xx", f"{SERVER} | stats count() calls, count() if ({IS_5XX}) failed "
                     "| math failed*100/calls as pct_5xx | fields pct_5xx", "percent",
              thresholds(("green", None), ("orange", 0.5), ("red", 1)), "Share of server spans answered with a 5xx.",
              decimals=2), 4, 4)
d.add(lq_stat("p95 latency", f"{SERVER} | stats quantile(0.95, duration) p95", "ns", thresholds(("blue", None)),
              "95th percentile duration of the server spans."), 4, 4)
d.add(lq_stat("p99 latency", f"{SERVER} | stats quantile(0.99, duration) p99", "ns", thresholds(("blue", None)),
              "99th percentile duration of the server spans: the slow tail behind p95. The Slowest requests table "
              "below lists those traces."), 4, 4)
d.add(lq_stat("Requests slower than 1s", f"{SERVER} duration:>1000000000 | stats count() slow", "short",
              thresholds(("green", None), ("orange", 1)),
              "Server spans that took more than one second: what users notice as slow. The Endpoints table and "
              "Where the time goes show which endpoint and why.", decimals=0), 4, 4)
d.add(lq_stat("Failed operations", f"{SPANS} {FAILED} | stats count() failed", "short", thresholds(("green", None), ("red", 1)),
              "Spans with an error status, 4xx answers seen by a client excluded: 5xx, database errors, failed "
              "outbox deliveries.", decimals=0), 4, 4)

d.row("Service map")
GATEWAY = '"resource_attr:service.namespace":=kgateway-system'
IN_CLUSTER = '| replace_regexp ("\\\\.[a-z0-9-]+\\\\.svc(\\\\.cluster\\\\.local)?\\\\.?$", "") at {f} '
MAP_SPANS = f"({APP_SPANS} or {GATEWAY})"
MAP_FAILED = f'{FAILED} -"span_attr:http.status_code":~"^4"'
IS_DB = '"span_attr:db.system.name":*'
DB_FIELDS = '| copy "span_attr:db.system.name" as db_system, "span_attr:server.address" as db_host '
TO_DB = DB_FIELDS + '| format if (kind:=3 db_system:*) "<db_system>" as {f} '
ENTRY = 'kind:=3 parent_span_id:"" -"span_attr:db.system.name":*'
MAP_NODES = (f"{MAP_SPANS} (kind:=2 or kind:=4 or (kind:=3 {IS_DB}) or ({ENTRY})) "
             '| copy "resource_attr:service.name" as id ' + TO_DB.format(f="id")
             + '| copy id as title | format if (kind:=3 db_system:*) "<db_host> (<db_system>)" as title '
             + f"| stats by (id, title) rate() rps, quantile(0.95, duration) p95, count() requests, "
               f"count() if ({MAP_FAILED}) failed "
               "| math round(p95/1e6, 0.1) as p95_ms, round(failed*100/requests, 0.01) as failed_pct "
               '| format "p95 <p95_ms> ms · <failed_pct>% failed" as subtitle '
               "| stats by (id, title, subtitle) max(rps) mainstat, max(requests) detail__requests, max(failed) detail__failed "
               "| math (detail__requests-detail__failed)/detail__requests as arc__success, "
               "detail__failed/detail__requests as arc__errors")
CALLERS = (f'{MAP_SPANS} kind:=3 -"span_attr:db.system.name":* '
           '| copy span_id as parent_span_id, "resource_attr:service.name" as source, duration as call_duration '
           "| fields trace_id, parent_span_id, source, call_duration")
MAP_EDGES = (f"{MAP_SPANS} kind:=2 parent_span_id:* "
             '| copy "resource_attr:service.name" as target '
             f"| join by (trace_id, parent_span_id) ({CALLERS}) "
             "| filter source:* -source:eq_field(target) "
             f'| union ({APP_SPANS} kind:=3 {IS_DB} | copy "resource_attr:service.name" as source, '
             '"span_attr:db.system.name" as target, duration as call_duration) '
             '| format "<source>→<target>" as id '
             f"| stats by (id, source, target) rate() mainstat, quantile(0.95, call_duration) p95, count() detail__calls, "
             f"count() if ({MAP_FAILED}) detail__failed "
             "| math round(p95/1e6, 0.1) as detail__p95_ms, round(detail__failed*100/detail__calls, 0.01) as detail__failed_pct "
             "| delete p95")
d.add({"type": "nodeGraph", "title": "Service map", "datasource": VTL,
       "description": "Clients that start traces (the dashboard in the browser), the Gateway kgateway-public, every service and "
                      "PostgreSQL: requests per second in the circle, p95 and the failed share below the name, "
                      "successful (green) and failed (red) spans in the ring. An arrow joins a calling span to the "
                      "span it caused in another service; hover it for calls per second, p95 as the caller sees it and "
                      "the failed share. Client errors (4xx) do not count as failed.",
       "targets": [vl(MAP_NODES, "stats", "A", ds=VTL), vl(MAP_EDGES, "stats", "B", ds=VTL)],
       "transformations": [
           {"id": "labelsToFields", "options": {"mode": "columns", "valueLabel": "__name__"}},
           {"id": "merge", "options": {}, "filter": {"id": "byRefId", "options": "A"}},
           {"id": "merge", "options": {}, "filter": {"id": "byRefId", "options": "B"}},
           {"id": "organize", "options": {"excludeByName": {"Time": True}, "renameByName": {
               "mainstat": "Requests per second", "arc__success": "Success", "arc__errors": "Failed",
               "detail__requests": "Requests", "detail__failed": "Failed requests", "detail__calls": "Calls",
               "detail__p95_ms": "p95 (ms)", "detail__failed_pct": "Failed (%)"}}}],
       "options": {"nodes": {"mainStatUnit": "reqps",
                             "arcs": [{"field": "arc__success", "color": "green"}, {"field": "arc__errors", "color": "red"}]},
                   "edges": {"mainStatUnit": "reqps"}}}, 16, 15)
d.add(lq_table("Calls to dependencies",
               f'{APP_SPANS} kind:=3 {SVC} | copy "span_attr:server.address" as peer ' + IN_CLUSTER.format(f="peer")
               + DB_FIELDS + '| format if (db_system:*) "<db_host> (<db_system>)" as peer | filter peer:* '
               f"| stats by (service, peer) count() calls, count() if ({FAILED}) failed, count() if ({IS_4XX}) e4, "
               "quantile(0.95, duration) p95 | sort by (calls desc) | limit 50",
               [("service", "Caller"), ("peer", "Callee"), ("calls", "Calls"), ("failed", "Failed"), ("e4", "4xx"),
                ("p95", "p95")],
               "Client spans by caller and callee: other services' /internal endpoints and PostgreSQL. p95 includes the "
               "network and the callee's work.",
               overrides=[COUNT("Calls", 60), COUNT("Failed", 55), COUNT("4xx", 45), NS_UNIT("p95", 70)],
               sort=[{"displayName": "Calls", "desc": True}]), 8, 15)

d.row("Endpoints — $service")
ENDPOINT_COLUMNS = [("service", "Service"), ("name", "Endpoint"), ("calls", "Calls"), ("rpm", "Req/min"),
                    ("pct_5xx", "5xx %"), ("pct_4xx", "4xx %"), ("p50", "p50"), ("p95", "p95"), ("p99", "p99"),
                    ("slowest", "Max"), ("time_share", "Time share"), ("db_share", "DB share"),
                    ("slowest_trace", "Slowest trace"), ("last_ms", "Last call")]
DASH_FOR_ZERO = {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "–", "index": 0}}},
                                             {"type": "special", "options": {"match": "nan", "result": {"text": "–", "index": 1}}}]}
SHARE = lambda name: by_name(name, [
    {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 1}, {"id": "min", "value": 0}, {"id": "max", "value": 100},
    {"id": "custom.width", "value": 140}, DASH_FOR_ZERO,
    {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}}])
ERROR_PCT = lambda name, color: by_name(name, [
    {"id": "unit", "value": "percent"}, {"id": "decimals", "value": 2}, {"id": "custom.width", "value": 75}, DASH_FOR_ZERO,
    {"id": "custom.cellOptions", "value": {"type": "color-text"}},
    {"id": "thresholds", "value": thresholds(("text", None), (color, 0.001))}])
ENDPOINTS = (f"{SERVER} {SVC} | stats by (service, name) count() calls, rate() rps, count() if ({IS_5XX}) e5, "
             f"count() if ({IS_4XX}) e4, quantile(0.5, duration) p50, quantile(0.95, duration) p95, "
             "quantile(0.99, duration) p99, max(duration) slowest, sum(duration) total, "
             "field_max(duration, trace_id) slowest_trace, max(start_time_unix_nano) last_ns "
             "| total_stats sum(total) all_total "
             "| math round(e5*100/calls, 0.01) as pct_5xx, round(e4*100/calls, 0.01) as pct_4xx, rps*60 as rpm, "
             "round(total*100/all_total, 0.1) as time_share, round(last_ns/1e6) as last_ms "
             "| stats by (service, name, slowest_trace) max(calls) calls, max(rpm) rpm, max(pct_5xx) pct_5xx, "
             "max(pct_4xx) pct_4xx, max(p50) p50, max(p95) p95, max(p99) p99, max(slowest) slowest, "
             "max(time_share) time_share, max(last_ms) last_ms "
             "| sort by (time_share desc) | limit 100")
ENDPOINT_DB = (f"{SERVER} {SVC} | stats by (trace_id, service, name) sum(duration) srv "
               f'| join by (trace_id, service) ({APP_SPANS} kind:=3 {IS_SQL} {SVC} | stats by (trace_id, service) sum(duration) db) '
               "| stats by (service, name) sum(srv) srv, sum(db) db | math round(db*100/srv, 0.1) as db_share")
endpoints = logsql_table(
    "Endpoints (server spans)", ENDPOINTS, ENDPOINT_COLUMNS,
    "Every endpoint of the selected services, the ones using the most server time first. Time share: its part of all "
    "server time (calls × duration), what to optimise first. DB share: its part spent in PostgreSQL queries of the same "
    "service. Slowest trace opens that call below; 4xx are the caller's mistakes or business rules.",
    VTL, overrides=[LINKS_ON("Endpoint", ENDPOINT_LINK), LINKS_ON("Service", SERVICE_DASH_LINK, 110), COUNT("Calls", 70),
                    by_name("Req/min", [{"id": "unit", "value": "short"}, {"id": "decimals", "value": 1},
                                        {"id": "custom.width", "value": 75}]),
                    ERROR_PCT("5xx %", "red"), ERROR_PCT("4xx %", "orange"),
                    NS_UNIT("p50", 80), NS_UNIT("p95", 80), NS_UNIT("p99", 80), NS_UNIT("Max", 80),
                    SHARE("Time share"), SHARE("DB share"), LINKS_ON("Slowest trace", TRACE_LINK, 270),
                    by_name("Last call", [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "custom.width", "value": 150}])],
    sort=[{"displayName": "Time share", "desc": True}],
    hide=("srv", "db"))
endpoints["targets"].append(vl(ENDPOINT_DB, "stats", "B", ds=VTL))
d.add(endpoints, 24, 11)
d.add(ts("p95 by endpoint", [vl(f"{SERVER} | stats by (name) quantile(0.95, duration) p95", "statsRange",
                                legend="{{name}}", ds=VTL)], "ns", ds=VTL), 12, 8)
d.add(ts("5xx by endpoint", [vl(f"{SERVER} {IS_5XX} | stats by (name) count() failed", "statsRange",
                                legend="{{name}}", ds=VTL)], "short", ds=VTL, stack=True, bars=True, decimals=0,
         legend_calcs=("sum",)), 12, 8)

d.row("Where the time goes — $endpoint")
d.add(lq_table("Operations in $endpoint traces",
               f"{APP_SPANS} {IN_ENDPOINT_TRACES} {SVC} {OPERATION} "
               f"| stats by (service, operation) count() calls, count() if ({FAILED}) failed, "
               "quantile(0.95, duration) p95, sum(duration) total | total_stats max(total) root_total "
               "| math round(total*100/root_total, 0.1) as time_pct, total/calls as avg | sort by (total desc) | limit 40",
               [("service", "Service"), ("operation", "Operation"), ("time_pct", "Share of endpoint time"),
                ("calls", "Calls"), ("failed", "Failed"), ("avg", "Avg"), ("p95", "p95")],
               "Every span of the traces that went through the selected endpoint, across all services, by total time. "
               "Spans nest (a client call contains the server span it calls), so read it top-down rather than adding it "
               "up. Outbox deliveries run after the response.",
               overrides=[by_name("Share of endpoint time", [
                   {"id": "unit", "value": "percent"}, {"id": "min", "value": 0}, {"id": "max", "value": 100},
                   {"id": "custom.width", "value": 220},
                   {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}}]),
                   by_name("Service", [{"id": "custom.width", "value": 130}]), COUNT("Calls"), COUNT("Failed"),
                   NS_UNIT("Avg"), NS_UNIT("p95")],
               sort=[{"displayName": "Share of endpoint time", "desc": True}], hide=("total", "root_total")), 24, 11)
d.add(ts("p95 by operation in $endpoint traces", [vl(
    f"{APP_SPANS} {IN_ENDPOINT_TRACES} {SVC} {OPERATION} "
    "| stats by (service, operation) quantile(0.95, duration) p95", "statsRange", legend="{{service}}: {{operation}}", ds=VTL)],
    "ns", ds=VTL, desc="A step in one operation shows where a slowdown of the endpoint comes from."), 12, 9)
d.add(ts("Failed operations in $endpoint traces", [vl(
    f"{APP_SPANS} {IN_ENDPOINT_TRACES} {FAILED} {SVC} {OPERATION} "
    "| stats by (service, operation) count() failed", "statsRange", legend="{{service}}: {{operation}}", ds=VTL)],
    "short", ds=VTL, stack=True, bars=True, decimals=0, legend_calcs=("sum",),
    desc="The operation that fails first is usually the cause; the spans above it fail because of it."), 12, 9)

d.row("Database and outbox, as the services see them — $service")
d.add(lq_table("SQL statements",
               f'{SPANS} {IS_SQL} "span_attr:db.query.text":* {SVC} | copy "span_attr:db.query.text" as sql '
               '| replace_regexp ("[ \\t\\n]+", " ") at sql '
               "| stats by (service, sql) count() calls, count() if (status_code:=2) failed, quantile(0.5, duration) p50, "
               "quantile(0.95, duration) p95, max(duration) slowest | sort by (p95 desc) | limit 50",
               [("service", "Service"), ("sql", "Statement"), ("calls", "Calls"), ("failed", "Failed"), ("p50", "p50"),
                ("p95", "p95"), ("slowest", "Max")],
               "Client-side duration of each statement: network, lock waits and execution together. Compare with Top "
               "statements in the CloudNativePG dashboard (server side only): a gap means waiting outside PostgreSQL's "
               "execution, e.g. for a connection or a lock.",
               overrides=[by_name("Service", [{"id": "custom.width", "value": 130}]), COUNT("Calls"), COUNT("Failed"),
                          NS_UNIT("p50"), NS_UNIT("p95"), NS_UNIT("Max")],
               sort=[{"displayName": "p95", "desc": True}]), 24, 8)
d.add(lq_table("Outbox deliveries",
               f'{SPANS} kind:=4 {SVC} | copy "span_attr:messaging.destination.name" as topic '
               f"| stats by (service, topic) count() deliveries, count() if ({FAILED}) failed, "
               'max("span_attr:outbox.attempts") attempts, quantile(0.95, duration) p95 | sort by (deliveries desc) | limit 30',
               [("service", "Service"), ("topic", "Destination"), ("deliveries", "Deliveries"), ("failed", "Failed"),
                ("attempts", "Most attempts"), ("p95", "p95")],
               "Producer spans of the outbox relays by destination. Each delivery continues the trace of the request "
               "that wrote the event, so a failed delivery opens on the original request.",
               overrides=[COUNT("Deliveries"), COUNT("Failed"), COUNT("Most attempts", 110), NS_UNIT("p95")],
               sort=[{"displayName": "Deliveries", "desc": True}]), 12, 8)
d.add(ts("Outbox delivery p95 by destination", [
    vl(f'{SPANS} kind:=4 | copy "span_attr:messaging.destination.name" as topic | stats by (topic) quantile(0.95, duration) p95',
       "statsRange", legend="{{topic}}", ds=VTL)], "ns", ds=VTL,
    desc="Duration of the call to the receiving service per delivery; the delay before the delivery is in Deus — Service."), 12, 8)

d.row("Errors — $service")
d.add(ts("Failed spans by operation", [vl(f"{SPANS} {FAILED} {SVC} | stats by (service, name) count() failed",
                                          "statsRange", legend="{{service}}: {{name}}", ds=VTL)],
         "short", ds=VTL, stack=True, bars=True, decimals=0, legend_calcs=("sum",),
         desc="4xx answers seen by a client are not counted."), 12, 9)
d.add(lq_table("Top failure messages",
               f"{SPANS} {FAILED} {SVC} | stats by (service, name, status_message) count() failed "
               "| sort by (failed desc) | limit 30",
               [("service", "Service"), ("name", "Operation"), ("status_message", "Message"), ("failed", "Count")],
               "Span status messages: the exception of a 5xx, a database error or a failed delivery.",
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
               "Server spans grouped by the selected attribute, worst first: one route, pod, node or version standing "
               "out points to the cause.",
               overrides=[COUNT("Calls"), PCT("5xx %", 0.5, 1), NS_UNIT("p95"), NS_UNIT("Max")],
               sort=[{"displayName": "5xx %", "desc": True}], hide=("e5",)), 12, 9)
d.add(ts("5xx by ${attribute:text}", [vl(
    f'{SERVER} "${{attribute:raw}}":* {IS_5XX} | copy "${{attribute:raw}}" as value | stats by (value) count() failed',
    "statsRange", legend="{{value}}", ds=VTL)], "short", ds=VTL, stack=True, bars=True, decimals=0,
    legend_calcs=("sum",)), 12, 9)
d.add(ts("Invoice issue outcomes", [vl(
    f'{APP_SPANS} "resource_attr:service.name":=sales kind:=2 name:="POST /api/sales/invoices/:id/issue" '
    f"| copy {CODE} as code | stats by (code) count() requests", "statsRange", legend="{{code}}", ds=VTL)],
    "short", ds=VTL, stack=True, bars=True, decimals=0, overrides=code_colors(), legend_calcs=("sum",),
    desc="What users got when issuing an invoice: 200 issued, 409 already issued, 422 rejected by a business rule "
         "(inactive customer, closed period), 5xx failures."), 12, 8)
d.add(ts("Payment outcomes", [vl(
    f'{APP_SPANS} "resource_attr:service.name":=payments kind:=2 name:="POST /api/payments/" '
    f"| copy {CODE} as code | stats by (code) count() requests", "statsRange", legend="{{code}}", ds=VTL)],
    "short", ds=VTL, stack=True, bars=True, decimals=0, overrides=code_colors(), legend_calcs=("sum",),
    desc="Receipts and vendor payments: 201 recorded, 400 invalid input, 422 an allocation larger than the amount due."), 12, 8)

d.row("Traces — $service")
REQUEST_COLUMNS = [("started_ms", "Time"), ("trace_id", "Trace ID"), ("service", "Service"), ("name", "Endpoint"),
                   ("code", "Status"), ("took", "Duration")]
REQUEST_OVERRIDES = [by_name("Time", [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 165}]),
                     LINKS_ON("Trace ID", TRACE_LINK, 250), by_name("Service", [{"id": "custom.width", "value": 110}]),
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
CLIENT_REQUESTS = (f'{APP_SPANS} {ENTRY} | copy "resource_attr:service.name" as client, '
                   '"span_attr:http.request.method" as method, "span_attr:http.response.status_code" as code '
                   '| extract_regexp "^https?://[^/]+(?P<path>/[^?#]*)" from "span_attr:url.full" '
                   '| format "<method> <path>" as request '
                   "| stats by (trace_id, client, request, code) max(duration) took, max(start_time_unix_nano) started "
                   "| math round(started/1e6) as started_ms | sort by (started_ms desc) | limit 50")
d.add(lq_table("Requests from clients", CLIENT_REQUESTS,
               [("started_ms", "Time"), ("trace_id", "Trace ID"), ("client", "Client"), ("request", "Request"),
                ("code", "Status"), ("took", "Duration")],
               "Newest calls that started a trace in a client, such as the dashboard in the browser, with everything "
               "they caused behind the gateway. Click a trace ID to show the whole trace below.",
               overrides=[by_name("Time", [{"id": "unit", "value": "dateTimeAsLocal"}, {"id": "custom.width", "value": 165}]),
                          LINKS_ON("Trace ID", TRACE_LINK, 250), by_name("Client", [{"id": "custom.width", "value": 110}]),
                          by_name("Status", [{"id": "custom.width", "value": 60}]), NS_UNIT("Duration", 85)],
               sort=[{"displayName": "Time", "desc": True}], hide=("started",)), 24, 8)
d.add({"type": "traces", "title": "Trace: ${trace_id:text}", "datasource": VT,
       "description": "The slowest or failed request of the time range by default; pick another in the Trace variable "
                      "or click a trace ID in the tables above. Every span with its timing, attributes and links to the "
                      "logs and metrics of its service.",
       "options": {}, "targets": [{"datasource": VT, "refId": "A", "query": "${trace_id}"}]}, 24, 60)

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
ATTRIBUTES = [("route", "span_attr:http.route"), ("HTTP status", "span_attr:http.response.status_code"),
              ("pod", "resource_attr:k8s.pod.name"), ("node", "resource_attr:k8s.node.name"),
              ("version", "resource_attr:service.version"), ("caller", "span_attr:user_agent.original")]
DURATIONS = [(t, str(int(ms * 1e6))) for t, ms in (("100ms", 100), ("250ms", 250), ("500ms", 500), ("1s", 1000), ("2s", 2000))]
describe(d.panels, {
    "p95 by endpoint": "95th percentile duration of each endpoint's server spans.",
    "5xx by endpoint": "Server spans answered with a 5xx per interval, by endpoint.",
    "5xx by ${attribute:text}": "Requests answered with a 5xx per interval, split by the attribute chosen in Hot spots "
                                "by: whether failures concentrate on one route, pod, node or version.",
})
DASHBOARD = dashboard(
    "deus-traces", "Deus — Traces", ["deus", "traces"], d.panels, [
        lq_var("service", "Service", "resource_attr:service.name", APP_SPANS, True, None),
        lq_var("endpoint", "Endpoint", "name", f'{APP_SPANS} "resource_attr:service.name":in($service) kind:=2', False,
               "POST /api/sales/invoices/:id/issue"),
        custom("attribute", "Hot spots by", ATTRIBUTES, "route"),
        custom("min_duration", "Slow request threshold", DURATIONS, "500ms"),
        TRACE_VAR],
    "Distributed tracing analytics from the deus spans (LogsQL on VictoriaTraces): service map, endpoints, where the "
    "time goes inside a request, SQL and outbox deliveries, failures, hot spots and single traces.",
    "now-1h", links=LINKS_DEUS, annotations=ANNOTATIONS)
