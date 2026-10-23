"""Shop — Traces: span analytics with LogsQL on VictoriaTraces (datasource "VictoriaTraces (LogsQL)")."""
from .common import (COUNT, LINKS_ON, PCT, VT, by_name, describe, code_colors, Dash, dashboard, logsql_table, stat,
    thresholds, ts, vl)

FILE = "shop-traces.json"

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
# The tail p95 hides: requests a user waits more than a second for (the slowest endpoint is in the table below).
d.add(lq_stat("Requests slower than 1s", f"{SERVER} duration:>1000000000 | stats count() slow", "short",
              thresholds(("green", None), ("orange", 1)),
              "Server spans that took more than one second in the time range: what users notice as slow. The "
              "Endpoints table and \"Where the time goes\" show which endpoint and why.", decimals=0), 4, 4)
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
# The values limit (100) stays above "limit 30": in cluster mode field_values runs the pipes on every storage node.
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
describe(d.panels, {
    "p95 by endpoint": "95th percentile duration of each endpoint's server spans (the request as the service "
                       "handles it).",
    "5xx by endpoint": "Server spans answered with a 5xx per interval, by endpoint.",
    "5xx by ${attribute:text}": "Requests answered with a 5xx per interval, split by the attribute chosen in the "
                                "Attribute variable: whether failures concentrate on one product, user, pod or version.",
})
DASHBOARD = dashboard(
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
    "now-1h")
