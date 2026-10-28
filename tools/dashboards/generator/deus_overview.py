from .common import (CADVISOR, RI, by_name, Dash, dashboard, describe, logs_panel, stat, table, thresholds, ts,
    vl_stat, vm)
from .deus import ANNOTATIONS, APP, K8S_NS, LINKS_DEUS, LOGS, SERVICES

FILE = "deus-overview.json"

REQ = "http_server_request_duration_seconds"
ALL = f'{APP}, http_route!~"/openapi.*"'
DEPLOY = f'{K8S_NS}, deployment=~"{"|".join(SERVICES)}"'
SVC_LINK = [{"title": "Open Deus — Service", "url": "/d/deus-service/deus-service?var-service=${__value.raw}&${__url_time_range}"}]


def per_minute(sel):
    return f'sum(rate({REQ}_count{{{APP}, {sel}, http_response_status_code=~"2.."}}[5m])) * 60'


d = Dash()
d.row("At a glance")
d.add(stat("Requests / s", f"sum(rate({REQ}_count{{{ALL}}}[5m]))", "reqps", thresholds(("blue", None))), 4, 4)
d.add(stat("5xx", f'(sum(rate({REQ}_count{{{ALL}, http_response_status_code=~"5.."}}[5m])) or vector(0))'
                  f" / sum(rate({REQ}_count{{{ALL}}}[5m]))", "percentunit",
           thresholds(("green", None), ("orange", 0.001), ("red", 0.005)), decimals=2), 4, 4)
d.add(stat("p95 latency", f"histogram_quantile(0.95, sum by (le) (rate({REQ}_bucket{{{ALL}}}[5m])))", "s",
           thresholds(("green", None), ("orange", 0.5), ("red", 1))), 4, 4)
d.add(stat("Services down", f"count(max by (deployment) (kube_deployment_status_replicas_available{{{DEPLOY}}}) == 0) or vector(0)",
           "short", thresholds(("green", None), ("red", 1)), decimals=0), 4, 4)
d.add(stat("Firing alerts", 'count(ALERTS{alertname=~"Deus.*", alertstate="firing"}) or vector(0)', "short",
           thresholds(("green", None), ("orange", 1)), decimals=0), 4, 4)
d.add(vl_stat("Browser errors", f"{LOGS} service.name:=web telemetry.source:=browser severity_text:=ERROR | stats count() errors",
              "short", thresholds(("green", None), ("orange", 1)),
              "Errors the web app reported from users' browsers in the time range."), 4, 4)

d.row("Business activity")
for title, sel, desc in (
    ("Invoices issued / min", 'service_name="sales", http_route="/api/sales/invoices/:id/issue"',
     "Invoices issued per minute (5-minute average)."),
    ("Payments recorded / min", 'service_name="payments", http_request_method="POST", http_route="/api/payments/"',
     "Receipts and vendor payments recorded per minute."),
    ("Bills approved / min", 'service_name="purchases", http_route="/api/purchases/bills/:id/approve"',
     "Vendor bills approved per minute."),
    ("Journals posted / min", 'service_name="ledger", http_request_method="POST", http_route=~"/internal/ledger/journals.*|/api/ledger/journals/:id/post"',
     "Journals the ledger posted per minute: from invoices, bills, payments, VAT returns and bank lines, plus manual ones."),
):
    d.add(stat(title, per_minute(sel), "short", thresholds(("blue", None)), desc, decimals=1, no_value="0"), 4, 4)
d.add(stat("Outbox events waiting", f'sum(outbox_backlog{{{APP}, outbox_state="pending"}}) or vector(0)', "short",
           thresholds(("green", None), ("orange", 20), ("red", 50)), decimals=0,
           desc="Events written but not yet delivered to the receiving service (all services)."), 4, 4)
d.add(stat("Outbox events dead", f'sum(outbox_backlog{{{APP}, outbox_state="dead"}}) or vector(0)', "short",
           thresholds(("green", None), ("red", 1)), decimals=0,
           desc="Events the receiving service rejected or that used every attempt: their documents are missing on the "
                "other side until someone acts. Alert: DeusOutboxDeadEvents."), 4, 4)

d.row("Services")
d.add(table(
    "Services",
    [vm(f"sum by (service_name) (rate({REQ}_count{{{ALL}}}[5m]))", ref="A", instant=True, fmt="table"),
     vm(f'(sum by (service_name) (rate({REQ}_count{{{ALL}, http_response_status_code=~"5.."}}[5m]))'
        f" or sum by (service_name) (rate({REQ}_count{{{ALL}}}[5m])) * 0) / sum by (service_name) (rate({REQ}_count{{{ALL}}}[5m]))",
        ref="B", instant=True, fmt="table"),
     vm(f"histogram_quantile(0.95, sum by (service_name, le) (rate({REQ}_bucket{{{ALL}}}[5m])))", ref="C", instant=True, fmt="table"),
     vm(f'sum by (service_name) (label_replace(kube_deployment_status_replicas_available{{{DEPLOY}}}, "service_name", "$1", "deployment", "(.+)"))',
        ref="D", instant=True, fmt="table"),
     vm('1 - max by (service_name) (label_replace(slo:sli_error:ratio_rate30d{{kind="service", slo="availability"}}, "service_name", "$1", "target", "(.+)"))',
        ref="E", instant=True, fmt="table"),
     vm(f'count by (service_name, service_version) (process_memory_usage_bytes{{{APP}}})', ref="F", instant=True, fmt="table")],
    [{"id": "merge", "options": {}},
     {"id": "organize", "options": {
         "excludeByName": {"Time": True, "Value #F": True},
         "indexByName": {"service_name": 0, "service_version": 1, "Value #D": 2, "Value #A": 3, "Value #B": 4, "Value #C": 5, "Value #E": 6},
         "renameByName": {"service_name": "Service", "service_version": "Version", "Value #D": "Ready pods",
                          "Value #A": "Req/s", "Value #B": "5xx", "Value #C": "p95", "Value #E": "Availability (30d)"}}}],
    overrides=[by_name("Service", [{"id": "links", "value": SVC_LINK}]),
               by_name("Ready pods", [{"id": "decimals", "value": 0}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                      {"id": "thresholds", "value": thresholds(("red", None), ("green", 1))}]),
               by_name("Req/s", [{"id": "unit", "value": "reqps"}, {"id": "decimals", "value": 2}]),
               by_name("5xx", [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 2},
                               {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "thresholds", "value": thresholds(("green", None), ("orange", 0.001), ("red", 0.005))}]),
               by_name("p95", [{"id": "unit", "value": "s"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                               {"id": "thresholds", "value": thresholds(("green", None), ("orange", 0.5), ("red", 1))}]),
               by_name("Availability (30d)", [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 3},
                                              {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                              {"id": "thresholds", "value": thresholds(("red", None), ("orange", 0.99), ("green", 0.995))}])],
    sort=[{"displayName": "Req/s", "desc": True}],
    desc="Every deus service now: ready pods, traffic, errors and latency of the last 5 minutes, and availability over "
         "the SLO window. Click a service for its dashboard."), 24, 10)
d.add(ts("Requests per service", [vm(f"sum by (service_name) (rate({REQ}_count{{{ALL}}}{RI}))", "{{service_name}}")],
         "reqps", stack=True), 8, 8)
d.add(ts("5xx ratio per service", [vm(
    f'sum by (service_name) (rate({REQ}_count{{{ALL}, http_response_status_code=~"5.."}}{RI}))'
    f" / sum by (service_name) (rate({REQ}_count{{{ALL}}}{RI}))", "{{service_name}}")], "percentunit", decimals=2, min_=0), 8, 8)
d.add(ts("p95 latency per service", [vm(
    f"histogram_quantile(0.95, sum by (service_name, le) (rate({REQ}_bucket{{{ALL}}}{RI})))", "{{service_name}}")], "s"), 8, 8)

d.row("Errors")
d.add(logs_panel("Latest ERROR and WARN records", f'{LOGS} severity_text:in(ERROR, WARN) '
                 '| format if (exception.message:*) "<_msg> — <exception.message>" as _msg | format "[<service.name>] <_msg>" as _msg',
                 "Newest WARN and ERROR records of every service and the browser; trace_id opens the request's trace."), 24, 9)

d.row("Resources")
d.add(ts("CPU per pod", [vm(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{CADVISOR}, {K8S_NS}, container!=""}}{RI}))',
                            "{{pod}}")], "cores", decimals=3), 12, 8)
d.add(ts("Memory working set per pod", [vm(f'sum by (pod) (container_memory_working_set_bytes{{{CADVISOR}, {K8S_NS}, container!=""}})',
                                           "{{pod}}")], "bytes"), 12, 8)

describe(d.panels, {
    "Requests / s": "Requests per second answered by every deus service, /internal calls between services included.",
    "5xx": "Share of all requests answered with a 5xx in the last 5 minutes.",
    "p95 latency": "95th percentile response time over every service and route.",
    "Services down": "Services without an available replica. Alert: DeusServiceDown.",
    "Firing alerts": "Deus alerts firing now; the list is on Deus — SLO Overview.",
    "Requests per service": "Requests per second each service answers.",
    "5xx ratio per service": "Share of each service's requests answered with a 5xx.",
    "p95 latency per service": "95th percentile response time of each service.",
    "CPU per pod": "CPU used by every pod of the deus namespace: services, the database, the web server and the traffic generator.",
    "Memory working set per pod": "Working set memory of every pod of the deus namespace (what counts against limits).",
})
DASHBOARD = dashboard(
    "deus-overview", "Deus — Overview", ["deus"], d.panels, [],
    "The deus accounting app at a glance: traffic, errors, latency, business activity, outbox, services and resources.",
    "now-3h", links=LINKS_DEUS, annotations=ANNOTATIONS)
