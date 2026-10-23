"""Shop — SLO Overview: user journeys, service health, error budgets, burn rates, SLIs against objectives."""
import os
import re

from .common import (BURN, NS, VM, by_name, Dash, dashboard, stat, table, thresholds, ts, vm)

FILE = "shop-slo.json"

# The chart values the SLO recording rules are rendered from.
VALUES_YAML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "values.yaml")
d = Dash()
SERVICES = ["api-gateway", "notification-service", "order-service", "payment-service", "product-service", "user-service"]
DEPLOY = f'{NS}, deployment=~"{"|".join(SERVICES)}"'
SVC_LINK = [{"title": "Open service dashboard",
             "url": "/d/shop-service/shop-service?var-service=${__data.fields.job}&${__url_time_range}"}]
GW_LINK = [{"title": "Open api-gateway dashboard",
            "url": "/d/shop-service/shop-service?var-service=api-gateway&${__url_time_range}"}]
pct = lambda name, dec=2: by_name(name, [{"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": dec}])


def slo_table(title, kind, first_col, link, desc, show_window=True, slo=None):
    """One row per job and SLI; with slo, one SLI only (its column and, for availability, the threshold hidden)."""
    sel = f'kind="{kind}"' + (f', slo="{slo}"' if slo else "")
    q = lambda e: f'sum by (job, slo) ({e})'
    return table(
        title,
        # sum by (job, slo): identical label sets in every query, so "merge" joins them into one row
        [vm(q(e), ref=ref, instant=True, fmt="table") for ref, e in (
            ("A", f'slo:objective:ratio{{{sel}}}'),
            ("B", f'1 - slo:sli_error:ratio_rate30d{{{sel}}}'),
            ("C", f'slo:error_budget_remaining:ratio{{{sel}}}'),
            ("D", f'slo:sli_error:ratio_rate1h{{{sel}}} {BURN}'),
            ("E", f'slo:sli_error:ratio_rate6h{{{sel}}} {BURN}'),
            ("F", f'slo:sli_window:seconds{{{sel}}}'),
            ("G", f'slo:sli_threshold:seconds{{{sel}}}'),
            # Time until the 30d budget is gone at the current 1h burn rate (capped at a year).
            ("H", f'clamp_max(clamp_min(slo:error_budget_remaining:ratio{{{sel}}}, 0) * 2592000'
                  f' / on (job, slo, kind) (slo:sli_error:ratio_rate1h{{{sel}}} {BURN}), 31536000)'))],
        [{"id": "merge", "options": {}},
         {"id": "organize", "options": {
             "excludeByName": {"Time": True, "cluster": True, "__name__": True, "Value #F": not show_window,
                               "slo": slo is not None, "Value #G": slo == "availability"},
             "indexByName": {"job": 0, "slo": 1, "Value #G": 2, "Value #A": 3, "Value #B": 4, "Value #C": 5,
                             "Value #D": 6, "Value #E": 7, "Value #H": 8, "Value #F": 9},
             "renameByName": {"job": first_col, "slo": "SLI", "Value #A": "Objective", "Value #B": "Actual (30d)",
                              "Value #C": "Budget remaining", "Value #D": "Burn rate 1h", "Value #E": "Burn rate 6h",
                              "Value #F": "Data in window", "Value #G": "Threshold", "Value #H": "Budget gone in"}}}],
        overrides=[
            by_name(first_col, [{"id": "links", "value": link}]),
            pct("Objective", 1), pct("Actual (30d)", 3),
            by_name("Budget remaining", [
                {"id": "unit", "value": "percentunit"}, {"id": "decimals", "value": 1}, {"id": "min", "value": 0}, {"id": "max", "value": 1},
                {"id": "custom.cellOptions", "value": {"type": "gauge", "mode": "basic", "valueDisplayMode": "text"}},
                {"id": "thresholds", "value": thresholds(("red", None), ("orange", 0), ("green", 0.25))}]),
            *[by_name(n, [{"id": "decimals", "value": 2},
                          {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                          {"id": "thresholds", "value": thresholds(("green", None), ("orange", 1), ("red", lim))}])
              for n, lim in (("Burn rate 1h", 14.4), ("Burn rate 6h", 6))],
            by_name("Data in window", [{"id": "unit", "value": "s"}, {"id": "decimals", "value": 1}]),
            by_name("Threshold", [{"id": "unit", "value": "s"},
                                  {"id": "mappings", "value": [{"type": "value", "options": {"0": {"text": "no 5xx"}}}]}]),
            by_name("Budget gone in", [
                {"id": "unit", "value": "s"}, {"id": "decimals", "value": 1},
                {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                {"id": "thresholds", "value": thresholds(("red", None), ("orange", 86400), ("green", 604800))},
                {"id": "mappings", "value": [
                    {"type": "value", "options": {"0": {"text": "exhausted", "color": "red"}}},
                    {"type": "range", "options": {"from": 31536000, "to": None,
                                                  "result": {"text": "not at this rate", "color": "green"}}}]}]),
        ],
        sort=[{"displayName": "Budget remaining", "desc": False}], desc=desc)


d.row("User journeys — measured at api-gateway")
for name in ("checkout", "browse"):
    d.add(stat(f"{name.capitalize()} · 5m",
               f'1 - slo:sli_error:ratio_rate5m{{job="{name}", slo="availability"}}', "percentunit",
               thresholds(("red", None), ("orange", 0.99), ("green", 0.995)), decimals=2, no_value="no traffic",
               desc=f"Share of {name} requests answered without a 5xx at the edge, including failures of every service behind it."),
          3, 7)
d.add(slo_table("Journey SLOs", "journey", "Journey", GW_LINK,
                "What users experience. A service that is down loses its own SLI (it receives no requests), "
                "but the journeys that depend on it keep burning here.", show_window=False), 18, 7)

d.row("Service health")
d.add(stat("Services without ready pods", f'count(kube_deployment_status_replicas_available{{{DEPLOY}}} == 0) or vector(0)',
           "short", thresholds(("green", None), ("red", 1)), decimals=0,
           desc="A service with no ready pod is down even though its SLO rows stay quiet. Alert: ShopServiceDown."), 4, 6)
d.add({"type": "bargauge", "title": "Ready pods per service", "datasource": VM,
       "description": "Share of each service's desired replicas that are available (passing their readiness probe, "
                      "so they receive traffic). 100%: all replicas serve. Orange: some are restarting, rolling out, "
                      "rescheduled after a node preemption or crash-looping; the service still answers with less "
                      "capacity and redundancy. Red 0%: the service is down. Its own SLI then stops moving (no "
                      "requests reach it), so this panel, Services without ready pods and ShopServiceDown catch it; "
                      "users see it in the journey SLOs.",
       "fieldConfig": {"defaults": {"unit": "percentunit", "min": 0, "max": 1,
                                    "thresholds": thresholds(("red", None), ("orange", 0.01), ("green", 1))}, "overrides": []},
       "options": {"orientation": "horizontal", "displayMode": "basic", "showUnfilled": True, "valueMode": "color",
                   "namePlacement": "left", "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}},
       "targets": [vm(f'max by (deployment) (kube_deployment_status_replicas_available{{{DEPLOY}}})'
                      f' / max by (deployment) (kube_deployment_spec_replicas{{{DEPLOY}}})', "{{deployment}}", instant=True)]},
      8, 6)
# The SLO closest to violation, named: the first place to look when budgets are being spent.
p = d.add(stat("Lowest error budget", "bottomk(1, slo:error_budget_remaining:ratio)", "percentunit",
               thresholds(("red", None), ("orange", 0), ("green", 0.25)), decimals=1,
               desc="The journey or service SLO with the least error budget left in the 30-day window. Below 0 it is "
                    "violated; under 25% only a short incident remains before it is."), 4, 6)
p["targets"][0]["legendFormat"] = "{{job}} · {{slo}}"
p["options"].update(textMode="value_and_name", colorMode="background")
d.add(stat("SLO data in window", "max(slo:sli_window:seconds)", "s", thresholds(("orange", None), ("green", 2592000)), decimals=1,
           desc="How much data the 30-day window really holds. Below 30 days (e.g. a recently created cluster), "
                "budget figures describe only this period and a short incident looks much larger."), 4, 6)
d.add(stat("SLO rules last evaluated", "time() - max(timestamp(slo:sli_error:ratio_rate5m))", "s",
           thresholds(("green", None), ("orange", 90), ("red", 300)), decimals=0,
           desc="Age of the newest SLI sample. If vmalert or the metrics storage is down, the dashboard silently goes stale."), 4, 6)

d.row("Services — error budget (30-day window)")
BUDGET_HELP = ("Burn rate 1 spends the budget in exactly 30 days; alerts page at 14.4 (1h) or 6 (6h). Budget gone in = "
               "remaining budget at the current 1h burn rate. Sorted by budget remaining, the most at risk first. "
               "Click a service to drill down.")
d.add(slo_table("Availability — requests answered without a 5xx", "service", "Service", SVC_LINK,
                f"Each service's share of non-5xx responses over 30 days against its objective. {BUDGET_HELP}",
                slo="availability"), 24, 9)
d.add(slo_table("Latency — requests faster than the service's threshold", "service", "Service", SVC_LINK,
                f"Each service's share of requests answered within its threshold (Threshold column) over 30 days "
                f"against its objective. {BUDGET_HELP}", slo="latency"), 24, 9)

d.row("Burn rate")
d.add(ts("Burn rate — 1h window", [vm(f"slo:sli_error:ratio_rate1h {BURN}", "{{job}} · {{slo}}")], "x",
         th=thresholds(("green", None), ("orange", 1), ("red", 14.4)), decimals=2, min_=0, soft_max=1,
         desc="Pages when both the 1h and 5m burn rates exceed 14.4 (2% of the 30-day budget in one hour)."), 12, 9)
d.add(ts("Burn rate — 6h window", [vm(f"slo:sli_error:ratio_rate6h {BURN}", "{{job}} · {{slo}}")], "x",
         th=thresholds(("green", None), ("orange", 1), ("red", 6)), decimals=2, min_=0, soft_max=1,
         desc="Pages when both the 6h and 30m burn rates exceed 6 (5% of the 30-day budget in six hours)."), 12, 9)

# SLI over the last hour against its objective (dashed): what "99.2% against 99.5%" looks like over time.
TARGET_LINE = {"matcher": {"id": "byFrameRefID", "options": "B"},
               "properties": [{"id": "custom.lineStyle", "value": {"fill": "dash", "dash": [10, 10]}},
                              {"id": "custom.lineWidth", "value": 2}, {"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}}]}
d.row("SLI against the objective — 1h")
for kind, title, who in (("journey", "Journey SLIs", "checkout and browse, measured at api-gateway"),
                         ("service", "Service SLIs", "each service's own requests")):
    d.add(ts(f"{title} — 1h", [vm(f'1 - slo:sli_error:ratio_rate1h{{kind="{kind}"}}', "{{job}} · {{slo}}"),
                               vm(f'max by (slo) (slo:objective:ratio{{kind="{kind}"}})', "objective · {{slo}}", "B")],
             # Fixed 95-100%: an incident dipping to 50% would otherwise flatten the band around the objective.
             "percentunit", decimals=2, min_=0.95, max_=1, fill=0, legend_calcs=("lastNotNull", "min"),
             overrides=[TARGET_LINE],
             desc=f"Share of good requests over the last hour ({who}): availability = not 5xx, latency = faster "
                  "than the threshold. The dashed lines are the objectives; below them the error budget is spent "
                  "faster than planned. The axis stops at 95%: lines leaving it at the bottom are incidents (Min in "
                  "the legend). Definitions at the bottom."), 12, 8)

d.row("Budget and SLI trends")
d.add(ts("Error budget remaining (30d)", [vm("slo:error_budget_remaining:ratio", "{{job}} · {{slo}}")], "percentunit",
         th=thresholds(("red", None), ("green", 0)), decimals=1, legend_calcs=("lastNotNull", "min"),
         max_=1, soft_min=0, fill=0,
         desc="Below 0 the SLO is violated for the 30-day window."), 12, 9)
d.add(ts("Bad requests ratio — 5m", [vm("slo:sli_error:ratio_rate5m", "{{job}} · {{slo}}")], "percentunit", soft_max=0.01,
         desc="Share of requests that are 5xx (availability) or slower than the threshold (latency)."), 12, 9)

d.row("Alerts")
d.add(table(
    "Shop alerts — firing and pending",
    [vm('ALERTS{alertname=~"Shop.*"}', instant=True, fmt="table")],
    [{"id": "organize", "options": {
        "excludeByName": {"Time": True, "Value": True, "__name__": True, "cluster": True, "alertgroup": True},
        "indexByName": {"alertname": 0, "alertstate": 1, "severity": 2, "job": 3, "slo": 4, "service": 5}}}],
    overrides=[by_name("alertstate", [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                      {"id": "mappings", "value": [{"type": "value", "options": {
                                          "firing": {"color": "red", "index": 0},
                                          "pending": {"color": "orange", "index": 1}}}]}])],
    defaults={"custom": {"align": "auto", "cellOptions": {"type": "auto"}}, "noValue": "No Shop alerts"},
    desc="Alert state written by vmalert (ALERTS series). Burn-rate alerts carry the job and slo labels."), 24, 6)

# SLI definitions, read from the chart values the SLO recording rules are rendered from (values.yaml), so the
# text cannot drift from the rules.
VALUES = open(VALUES_YAML).read().split("\nslo:\n", 1)[1].split("\n\n", 1)[0]
val = lambda key: re.search(rf"^  {key}: \"?([0-9.]+)\"?", VALUES, re.M).group(1)
pct_ = lambda v: f"{float(v) * 100:g}%"
ms = lambda v: f"{float(v) * 1000:g} ms"
overrides_ = re.findall(r"^    ([a-z-]+): \{ latencyThreshold: \"([0-9.]+)\" \}", VALUES, re.M)
journeys_ = re.findall(r"^    ([a-z]+): \{ method: ([A-Z]+), route: \"?([^,\"]+)\"?, latencyThreshold: \"([0-9.]+)\" \}", VALUES, re.M)
assert overrides_ and journeys_, "slo values not parsed"
SLI_DOC = f"""
| SLI | Good events | Valid events | Objective (30 days) |
|---|---|---|---|
| **Availability** | responses that are not 5xx (4xx are the client's mistake and count as good) | all HTTP requests | {pct_(val("availability"))} |
| **Latency** | responses faster than the latency threshold | all HTTP requests | {pct_(val("latency"))} |

**Where they are measured.** *Journeys* at the edge (api-gateway), so they include failures of every service behind it:
{"; ".join(f"**{n}** = `{m} {r.replace('|', ' or ')}` (threshold {ms(t)})" for n, m, r, t in journeys_)}.
*Services* on each service's own requests (threshold {ms(val("latencyThreshold"))};
{"; ".join(", ".join(n for n, t in overrides_ if t == th) + f": {ms(th)}" for th in sorted({t for _, t in overrides_}))}:
the checkout path includes the payment provider).

**Error budget and burn rate.** Budget = 1 − objective over a rolling 30 days. Burn rate = error ratio / budget: 1 spends
the budget in exactly 30 days. Pages (critical): burn rate above 14.4 over 1h and 5m, or above 6 over 6h and 30m.
Tickets (warning): above 3 over 1d and 2h, or above 1 over 3d and 6h. Recording rules: `apps/k8s/services/templates/monitoring.yaml`.
"""
d.row("SLI definitions")
d.add({"type": "text", "title": "", "transparent": True, "options": {"mode": "markdown", "content": SLI_DOC.strip()}}, 24, 9)

DASHBOARD = dashboard(
    "shop-slo", "Shop — SLO Overview", ["shop", "slo"], d.panels, [],
    "Availability and latency SLOs per service: error budget, burn rates and alerts.")
