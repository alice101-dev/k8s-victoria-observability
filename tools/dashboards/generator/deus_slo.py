from .common import VM, by_name, Dash, dashboard, stat, table, thresholds, ts, vm
from .deus import ANNOTATIONS, K8S_NS, LINKS_DEUS, deus_deployments, sli_doc

FILE = "deus-slo.json"

BURN = "/ on (target, slo, kind) (1 - slo:objective:ratio)"
READY = deus_deployments(f"max by (namespace, deployment) (kube_deployment_status_replicas_available{{{K8S_NS}}})")
SPEC = deus_deployments(f"max by (namespace, deployment) (kube_deployment_spec_replicas{{{K8S_NS}}})")
SVC_LINK = [{"title": "Open Deus — Service", "url": "/d/deus-service/deus-service?var-service=${__data.fields.target}&${__url_time_range}"}]
pct = lambda name, dec=2: by_name(name, [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": dec}])


def slo_table(title, kind, first_col, desc, slo=None, link=None):
    sel = f'kind="{kind}"' + (f', slo="{slo}"' if slo else "")
    q = lambda e: f"sum by (target, slo) ({e})"
    overrides = [
        pct("Objective", 1), pct("Actual (30d)", 3),
        by_name("Budget remaining", [
            {"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 1}, {"id": "min", "value": 0}, {"id": "max", "value": 1},
            {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
            {"id": "thresholds", "value": thresholds(("red", None), ("orange", 0), ("green", 0.25))}]),
        *[by_name(n, [{"id": "decimals", "value": 2}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                      {"id": "thresholds", "value": thresholds(("green", None), ("orange", 1), ("red", lim))}])
          for n, lim in (("Burn rate 1h", 14.4), ("Burn rate 6h", 6))],
        by_name("Threshold", [{"id": "unit", "value": "s"},
                              {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "no 5xx"}}}]}]),
        by_name("Budget gone in", [
            {"id": "unit", "value": "s"}, {"id": "decimals", "value": 1},
            {"id": "custom.cellOptions", "value": {"type": "color-text"}},
            {"id": "thresholds", "value": thresholds(("red", None), ("orange", 86400), ("green", 604800))},
            {"id": "mappings", "value": [
                {"type": "value", "options": {"0": {"text": "exhausted", "color": "red"}}},
                {"type": "range", "options": {"from": 31536000, "to": None, "result": {"text": "not at this rate", "color": "green"}}}]}]),
    ]
    if link:
        overrides.insert(0, by_name(first_col, [{"id": "links", "value": link}]))
    return table(
        title,
        [vm(q(e), ref=ref, instant=True, fmt="table") for ref, e in (
            ("A", f"slo:objective:ratio{{{sel}}}"),
            ("B", f"1 - slo:sli_error:ratio_rate30d{{{sel}}}"),
            ("C", f"slo:error_budget_remaining:ratio{{{sel}}}"),
            ("D", f"slo:sli_error:ratio_rate1h{{{sel}}} {BURN}"),
            ("E", f"slo:sli_error:ratio_rate6h{{{sel}}} {BURN}"),
            ("G", f"slo:sli_threshold:seconds{{{sel}}}"),
            ("H", f"clamp_max(clamp_min(slo:error_budget_remaining:ratio{{{sel}}}, 0) * 2592000"
                  f" / on (target, slo, kind) (slo:sli_error:ratio_rate1h{{{sel}}} {BURN}), 31536000)"))],
        [{"id": "merge", "options": {}},
         {"id": "organize", "options": {
             "excludeByName": {"Time": True, "__name__": True, "slo": slo is not None, "Value #G": slo == "availability"},
             "indexByName": {"target": 0, "slo": 1, "Value #G": 2, "Value #A": 3, "Value #B": 4, "Value #C": 5,
                             "Value #D": 6, "Value #E": 7, "Value #H": 8},
             "renameByName": {"target": first_col, "slo": "SLI", "Value #A": "Objective", "Value #B": "Actual (30d)",
                              "Value #C": "Budget remaining", "Value #D": "Burn rate 1h", "Value #E": "Burn rate 6h",
                              "Value #G": "Threshold", "Value #H": "Budget gone in"}}}],
        overrides=overrides, sort=[{"displayName": "Budget remaining", "desc": False}], desc=desc)


d = Dash()
d.row("User journeys")
d.add(slo_table("Journey SLOs", "journey", "Journey",
                "What users do, measured on the service that answers it. A journey that burns while its service looks "
                "fine usually fails in a dependency the service calls."), 24, 8)

d.row("Service health")
d.add(stat("Services without ready pods", f"count({READY} == 0) or vector(0)",
           "short", thresholds(("green", None), ("red", 1)), decimals=0,
           desc="A service with no ready pod is down even though its SLO rows stay quiet. Alert: DeusServiceDown."), 4, 6)
d.add({"type": "bargauge", "title": "Ready pods per service", "datasource": VM,
       "description": "Share of each service's desired replicas that are available. Orange: some replicas are restarting, "
                      "rolling out or rescheduled after a Spot preemption. Red 0%: the service is down.",
       "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1,
                                    "thresholds": thresholds(("red", None), ("orange", 0.01), ("green", 1))}, "overrides": []},
       "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "color",
                   "namePlacement": "left", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
       "targets": [vm(f"{READY} / {SPEC}", "{{deployment}}", instant=True)]},
      8, 6)
BURN_1H = f"slo:sli_error:ratio_rate1h {BURN}"
p = d.add(stat("Burning fastest now",
               f"topk(1, {BURN_1H} > 0.1) or on () label_replace(label_replace(max({BURN_1H}), "
               f'"target", "every SLO", "", ""), "slo", "below 0.1x", "", "")', "x",
               thresholds(("green", None), ("orange", 1), ("red", 14.4)), decimals=2,
               desc="The journey or service SLO with the highest burn rate over the last hour. Below 1 its budget lasts "
                    "longer than 30 days; at 14.4 DeusSLOBurnRateCritical pages."), 4, 6)
p["targets"][0]["legendFormat"] = "{{target}} · {{slo}}"
p["options"].update(textMode="value_and_name", colorMode="background")
d.add(stat("SLO data in window", "max(slo:sli_window:seconds)", "s", thresholds(("orange", None), ("green", 2592000)), decimals=1,
           desc="How much data the 30-day window really holds. On a recently created cluster the budget figures "
                "describe only this period, and a short incident looks much larger."), 4, 6)
d.add(stat("SLO rules last evaluated", "time() - max(timestamp(slo:sli_error:ratio_rate5m))", "s",
           thresholds(("green", None), ("orange", 90), ("red", 300)), decimals=0,
           desc="Age of the newest SLI sample. If vmalert or VictoriaMetrics is down, this dashboard silently goes stale."), 4, 6)

d.row("Services — error budget (30-day window)")
BUDGET_HELP = ("Burn rate 1 spends the budget in exactly 30 days; alerts page at 14.4 (1h) or 6 (6h). Budget gone in = "
               "remaining budget at the current 1h burn rate. Most at risk first; click a service to drill down.")
d.add(slo_table("Availability — requests answered without a 5xx", "service", "Service",
                f"Each service's share of non-5xx responses over 30 days against its objective. {BUDGET_HELP}",
                slo="availability", link=SVC_LINK), 24, 10)
d.add(slo_table("Latency — requests faster than the service's threshold", "service", "Service",
                f"Each service's share of requests answered within its threshold over 30 days. {BUDGET_HELP}",
                slo="latency", link=SVC_LINK), 24, 10)

d.row("Burn rate")
d.add(ts("Burn rate — 1h window", [vm(f"{BURN_1H} > 0", "{{target}} · {{slo}}")], "x",
         th=thresholds(("green", None), ("orange", 1), ("red", 14.4)), decimals=2, min_=0, soft_max=1,
         desc="Pages when both the 1h and 5m burn rates exceed 14.4 (2% of the 30-day budget in one hour)."), 12, 9)
d.add(ts("Burn rate — 6h window", [vm(f"slo:sli_error:ratio_rate6h {BURN} > 0", "{{target}} · {{slo}}")], "x",
         th=thresholds(("green", None), ("orange", 1), ("red", 6)), decimals=2, min_=0, soft_max=1,
         desc="Pages when both the 6h and 30m burn rates exceed 6 (5% of the 30-day budget in six hours)."), 12, 9)

TARGET_LINE = {"matcher": {"id": "byFrameRefID", "options": "B"},
               "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                              {"id": "custom.lineWidth", "value": 2}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}
d.row("SLI against the objective — 1h")
for kind, title in (("journey", "Journey SLIs"), ("service", "Service SLIs")):
    d.add(ts(f"{title} — 1h", [vm(f'1 - slo:sli_error:ratio_rate1h{{kind="{kind}"}}', "{{target}} · {{slo}}"),
                               vm(f'max by (slo) (slo:objective:ratio{{kind="{kind}"}})', "objective · {{slo}}", "B")],
             "percentunit", decimals=2, min_=0.95, max_=1, fill=0, legend_calcs=("lastNotNull", "min"),
             overrides=[TARGET_LINE],
             desc="Share of good requests over the last hour: availability = not 5xx, latency = faster than the "
                  "threshold. The dashed lines are the objectives. The axis stops at 95%: lines leaving it at the "
                  "bottom are incidents (Min in the legend)."), 12, 8)

d.row("Budget and SLI trends")
d.add(ts("Error budget remaining (30d)", [vm("slo:error_budget_remaining:ratio", "{{target}} · {{slo}}")], "percentunit",
         th=thresholds(("red", None), ("green", 0)), decimals=1, legend_calcs=("lastNotNull", "min"), max_=1, soft_min=0, fill=0,
         desc="Below 0 the SLO is violated for the 30-day window."), 12, 9)
d.add(ts("Bad requests ratio — 5m", [vm("slo:sli_error:ratio_rate5m > 0", "{{target}} · {{slo}}")], "percentunit", soft_max=0.01,
         desc="Share of requests that are 5xx (availability) or slower than the threshold (latency)."), 12, 9)

d.row("Alerts")
d.add(table(
    "Deus alerts — firing and pending",
    [vm('ALERTS{alertname=~"Deus.*"}', instant=True, fmt="table")],
    [{"id": "organize", "options": {
        "excludeByName": {"Time": True, "Value": True, "__name__": True, "alertgroup": True},
        "indexByName": {"alertname": 0, "alertstate": 1, "severity": 2, "target": 3, "kind": 4, "slo": 5}}}],
    overrides=[by_name("alertstate", [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                      {"id": "mappings", "value": [{"type": "value", "options": {
                                          "firing": {"color": "red", "index": 0}, "pending": {"color": "orange", "index": 1}}}]}])],
    defaults={"custom": {"align": "auto", "cellOptions": {"type": "auto"}}, "noValue": "No Deus alerts"},
    desc="Alert state written by vmalert (ALERTS series)."), 24, 6)

d.row("SLI definitions")
d.add({"type": "text", "title": "", "transparent": True, "options": {"mode": "markdown", "content": sli_doc()}}, 24, 13)

DASHBOARD = dashboard(
    "deus-slo", "Deus — SLO Overview", ["deus", "slo"], d.panels, [],
    "Availability and latency SLOs of the deus journeys and services: error budgets, burn rates and alerts.",
    links=LINKS_DEUS, annotations=ANNOTATIONS)
