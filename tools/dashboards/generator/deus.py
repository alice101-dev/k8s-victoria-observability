import json
import os
import re

from .common import LINKS, restart_annotations

SERVICES = ["accounts", "banking", "contacts", "identity", "ledger", "notification", "organization", "payments",
            "products", "purchases", "reporting", "sales", "tax", "deus-dashboard"]
APP = 'service_namespace="deus"'
K8S_NS = 'namespace=~"(.+-)?deus"'
LOGS = "service.namespace:=deus"
SPANS = '"resource_attr:service.namespace":=deus'
RUNBOOK = "https://github.com/alice101-dev/k8s-victoria-observability/blob/main/docs/runbooks/deus.md"
REQUESTS = "http_server_request_duration_seconds"
SLI_BASE = f'{APP}, http_route!~"/openapi.*"'
FIVE_XX = ', http_response_status_code=~"5.."'

AVAILABILITY = 0.995
LATENCY = 0.99
LATENCY_THRESHOLD = "0.5"
LATENCY_OVERRIDES = {"identity": "2.5", "payments": "1", "reporting": "1"}
JOURNEYS = [
    ("sign-in", "Sign in and register", 'service_name="identity", http_request_method="POST", http_route="/api/identity/auth/*"', "2.5"),
    ("invoicing", "Create, issue and void invoices", 'service_name="sales", http_request_method=~"POST|PUT|DELETE", http_route=~"/api/sales/.*"', "1"),
    ("payments", "Record receipts and vendor payments", 'service_name="payments", http_request_method="POST", http_route=~"/api/payments/.*"', "1"),
    ("bills", "Enter and approve vendor bills", 'service_name="purchases", http_request_method=~"POST|PUT|DELETE", http_route=~"/api/purchases/.*"', "1"),
    ("reports", "Financial statements", 'service_name="reporting", http_route=~"/api/reporting/.*"', "1"),
    ("browse", "Every page load (GET /api/...)", 'http_request_method="GET", http_route=~"/api/.*", service_name!="identity"', "0.5"),
]
LINKS_DEUS = [{"title": "Deus dashboards", "type": "dashboards", "tags": ["deus"], "asDropdown": True,
               "includeVars": False, "keepTime": True}] + LINKS
ANNOTATIONS = [
    {"name": "Rollouts", "datasource": {"type": "prometheus", "uid": "VictoriaMetrics"}, "enable": True,
     "iconColor": "blue", "step": "1m", "useValueForTime": False,
     "expr": f"changes(max by (deployment) (kube_deployment_metadata_generation{{{K8S_NS}}})[1m]) > 0",
     "titleFormat": "Rollout {{deployment}}", "textFormat": "Spec changed (image, env or resources)"},
    {"name": "Critical alerts", "datasource": {"type": "prometheus", "uid": "VictoriaMetrics"}, "enable": True,
     "iconColor": "red", "step": "1m", "useValueForTime": False,
     "expr": 'max by (alertname, target, slo, deployment) (ALERTS{alertname=~"Deus.*", alertstate="firing", severity="critical"})',
     "titleFormat": "{{alertname}}", "textFormat": "{{target}}{{deployment}} {{slo}}"},
] + restart_annotations(K8S_NS)
SHORT_WINDOWS = ["5m", "30m", "1h", "2h", "6h"]
LONG_WINDOWS = ["1d", "3d"]


def targets():
    out = [{"target": s, "kind": "service", "sel": f'{SLI_BASE}, service_name="{s}"',
            "threshold": LATENCY_OVERRIDES.get(s, LATENCY_THRESHOLD)} for s in SERVICES]
    out += [{"target": name, "kind": "journey", "sel": f"{SLI_BASE}, {sel}", "threshold": threshold, "about": about}
            for name, about, sel, threshold in JOURNEYS]
    return out


def labels(t, slo):
    return {"target": t["target"], "kind": t["kind"], "slo": slo}


def requests(t, window, extra=""):
    return f'sum(rate({REQUESTS}_count{{{t["sel"]}{extra}}}[{window}]))'


def error_ratio(t, slo, window):
    total = requests(t, window)
    if slo == "availability":
        return f'({requests(t, window, FIVE_XX)} or {total} * 0) / {total}'
    good = f'sum(rate({REQUESTS}_bucket{{{t["sel"]}, le="{t["threshold"]}"}}[{window}]))'
    return f"clamp_min(1 - {good} / {total}, 0)"


def bad_rate(t, slo):
    if slo == "availability":
        return f'{requests(t, "5m", FIVE_XX)} or {requests(t, "5m")} * 0'
    good = f'sum(rate({REQUESTS}_bucket{{{t["sel"]}, le="{t["threshold"]}"}}[5m]))'
    return f'clamp_min({requests(t, "5m")} - {good}, 0)'


def record(name, expr, lbl):
    return {"record": name, "expr": expr, "labels": lbl}


def burn(window, factor):
    return f"slo:sli_error:ratio_rate{window} > on (target, slo, kind) ({factor} * (1 - slo:objective:ratio))"


def alert(name, expr, severity, summary, description, for_=None):
    a = {"alert": name, "expr": expr}
    if for_:
        a["for"] = for_
    a["labels"] = {"severity": severity}
    a["annotations"] = {"summary": summary, "description": description, "runbook_url": f"{RUNBOOK}#{name.lower()}"}
    return a


def rule_groups():
    ts = targets()
    sli, long_, budget = [], [], []
    for t in ts:
        for slo, objective in (("availability", AVAILABILITY), ("latency", LATENCY)):
            lbl = labels(t, slo)
            sli.append(record("slo:objective:ratio", f"vector({objective})", lbl))
            sli.append(record("slo:sli_threshold:seconds", f'vector({0 if slo == "availability" else t["threshold"]})', lbl))
            for w in SHORT_WINDOWS:
                sli.append(record(f"slo:sli_error:ratio_rate{w}", error_ratio(t, slo, w), lbl))
            for w in LONG_WINDOWS:
                long_.append(record(f"slo:sli_error:ratio_rate{w}", error_ratio(t, slo, w), lbl))
            sli.append(record("slo:sli_requests:rate5m", requests(t, "5m"), lbl))
            sli.append(record("slo:sli_errors:rate5m", bad_rate(t, slo), lbl))
    budget = [
        {"record": "slo:sli_error:ratio_rate30d",
         "expr": "sum_over_time(slo:sli_errors:rate5m[30d]) / sum_over_time(slo:sli_requests:rate5m[30d])"},
        {"record": "slo:error_budget_remaining:ratio",
         "expr": "1 - slo:sli_error:ratio_rate30d / on (target, slo, kind) (1 - slo:objective:ratio)"},
        {"record": "slo:sli_window:seconds", "expr": "count_over_time(slo:sli_requests:rate5m[30d]) * 30"},
    ]
    deployments = "|".join(s for s in SERVICES)
    alerts = [
        alert("DeusSLOBurnRateCritical",
              f'({burn("1h", 14.4)} and {burn("5m", 14.4)}) or ({burn("6h", 6)} and {burn("30m", 6)})',
              "critical", "{{ $labels.target }} ({{ $labels.kind }}): {{ $labels.slo }} SLO is burning its error budget fast",
              "{{ $value | humanizePercentage }} of requests miss the {{ $labels.slo }} SLI. Start from Deus — SLO Overview, "
              "then Deus — Service of the failing service: its error logs and traces."),
        alert("DeusSLOBurnRateWarning",
              f'({burn("1d", 3)} and {burn("2h", 3)}) or ({burn("3d", 1)} and {burn("6h", 1)})',
              "warning", "{{ $labels.target }} ({{ $labels.kind }}): {{ $labels.slo }} SLO error budget is spent faster than planned",
              "{{ $value | humanizePercentage }} of requests miss the {{ $labels.slo }} SLI over the long window."),
        alert("DeusServiceDown",
              f'max by (namespace, deployment) (kube_deployment_status_replicas_available{{{K8S_NS}, deployment=~"{deployments}"}}) == 0',
              "critical", "{{ $labels.deployment }} has no available replica",
              "Every request to {{ $labels.deployment }} fails, and its own SLI stops moving because no request reaches it. "
              "Check the pod events, the migration Job and the startup logs.", "2m"),
        alert("DeusOutboxDeadEvents",
              f'max by (service_name) (outbox_backlog{{{APP}, outbox_state="dead"}}) > 0',
              "warning", "{{ $labels.service_name }} has {{ $value }} outbox events that will not be retried",
              "The receiving service rejected these events (400, 404, 422) or they used every attempt. The documents they "
              "belong to are missing in the receiving service until someone replays or fixes them.", "5m"),
        alert("DeusOutboxBacklog",
              f'max by (service_name) (outbox_backlog{{{APP}, outbox_state="pending"}}) > 50',
              "warning", "{{ $labels.service_name }} has {{ $value }} outbox events waiting",
              "Events are written faster than they are delivered, or the receiving service is down: journals, allocations "
              "or emails arrive late.", "10m"),
        alert("DeusDatabasePoolExhausted",
              f'max by (service_name) (db_client_connection_pending_requests{{{APP}}}) > 0',
              "warning", "{{ $labels.service_name }} requests wait for a database connection",
              "Every connection of the pool is in use for 10 minutes. Requests queue and slow down; check slow queries "
              "and DB_MAX_CONNECTIONS.", "10m"),
    ]
    log_alerts = [
        alert("DeusErrorLogsSpike",
              f"{LOGS} severity_text:=ERROR | stats by (service.name) count() as error_logs | filter error_logs:>20",
              "warning", '{{ index $labels "service.name" }}: {{ $value }} ERROR logs in 5 minutes',
              f"Open Deus — Logs for the service, or query VictoriaLogs: {LOGS} severity_text:=ERROR"),
    ]
    burn_alerts, other = alerts[:2], alerts[2:]
    return [
        {"name": "deus-slo-sli", "interval": "30s", "rules": sli},
        {"name": "deus-slo-sli-long", "interval": "5m", "rules": long_},
        {"name": "deus-slo-budget", "interval": "1m", "rules": budget},
        {"name": "deus-slo-alerts", "rules": burn_alerts},
        {"name": "deus", "rules": other},
        {"name": "deus-logs", "type": "vlogs", "interval": "5m", "rules": log_alerts},
    ]


PLAIN = re.compile(r"[A-Za-z][A-Za-z0-9_./-]*(:[A-Za-z0-9_./-]+)*")


def yaml_value(value, indent):
    if isinstance(value, str) and PLAIN.fullmatch(value) and value not in ("true", "false", "null", "yes", "no"):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+[smhd]", value):
        return value
    if isinstance(value, str) and len(value) > 100:
        pad = " " * (indent + 2)
        return "|\n" + "\n".join(pad + line for line in value.split("\n"))
    return json.dumps(value, ensure_ascii=False)


def yaml(obj, indent=0):
    pad = " " * indent
    lines = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                lines.append(f"{pad}{k}:")
                lines.append(yaml(v, indent + 2))
            else:
                lines.append(f"{pad}{k}: {yaml_value(v, indent)}")
    else:
        for item in obj:
            body = yaml(item, indent + 2).split("\n")
            lines.append(f"{pad}- {body[0].lstrip()}")
            lines.extend(body[1:])
    return "\n".join(lines)


def write_rules(path):
    doc = {"apiVersion": "operator.victoriametrics.com/v1beta1", "kind": "VMRule",
           "metadata": {"name": "deus", "namespace": "monitoring"}, "spec": {"groups": rule_groups()}}
    with open(path, "w") as f:
        f.write(yaml(doc) + "\n")
    print(f"{os.path.basename(path)}: {sum(len(g['rules']) for g in doc['spec']['groups'])} rules")


def pct(value):
    return f"{value * 100:g}%"


def ms(value):
    return f"{float(value) * 1000:g} ms"


def sli_doc():
    overrides = ", ".join(f"{s} {ms(t)}" for s, t in sorted(LATENCY_OVERRIDES.items()))
    journeys = "\n".join(f"| **{name}** | {about} | `{sel}` | {ms(threshold)} |" for name, about, sel, threshold in JOURNEYS)
    return f"""
| SLI | Good events | Valid events | Objective (30 days) |
|---|---|---|---|
| **Availability** | responses that are not 5xx (4xx are the caller's mistake and count as good) | all HTTP requests | {pct(AVAILABILITY)} |
| **Latency** | responses faster than the latency threshold | all HTTP requests | {pct(LATENCY)} |

**Services**: each service's own requests, `/openapi` excluded; threshold {ms(LATENCY_THRESHOLD)} ({overrides}).

**Journeys**: what a user does, measured on the service that answers it:

| Journey | What | Requests | Threshold |
|---|---|---|---|
{journeys}

**Error budget and burn rate.** Budget = 1 − objective over a rolling 30 days. Burn rate = error ratio / budget: 1 spends
the budget in exactly 30 days. Pages (critical): above 14.4 over 1h and 5m, or above 6 over 6h and 30m. Tickets
(warning): above 3 over 1d and 2h, or above 1 over 3d and 6h. The rules are generated with this text by
`tools/dashboards` into `infrastructure/configs/victoria-metrics-k8s-stack/base/deus.yaml`.
""".strip()
