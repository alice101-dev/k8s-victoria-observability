"""Shop — Microservices Overview: hand-made dashboard; only its links and annotations are generated."""
import json
import os

from .common import ANNOTATIONS, CADVISOR, LINKS, describe

FILE = "shop-overview.json"


DESCRIPTIONS = {
    "Request rate": "Requests per second entering the shop at api-gateway.",
    "Error rate 5xx": "Share of api-gateway requests answered with a 5xx: what users see failing.",
    "p95 latency": "95th percentile response time at api-gateway.",
    "Orders confirmed / min": "Checkouts that ended with a confirmed order, per minute (5-minute average).",
    "Request rate per service": "Requests per second each service answers.",
    "Error rate 5xx per service": "Share of each service's requests answered with a 5xx.",
    "p95 latency per service": "95th percentile response time of each service.",
    "p95 latency per route": "95th percentile response time per service and route.",
    "Status code per service": "Responses per second by HTTP status code: 4xx are client errors (unknown user, "
                               "declined payment), 5xx server errors.",
    "In-flight requests": "Requests being processed right now per service; rises when a service or a dependency slows.",
    "Calls to other services": "HTTP calls each service makes to the others per second, by callee and status code.",
    "p95 latency to other services": "95th percentile duration of calls between services, by caller and callee.",
    "Checkout results": "Checkouts per second by result: confirmed, payment_failed (declined card), "
                        "invalid_user, invalid_product.",
    "Payment": "Payments per second by result (approved, declined).",
    "Stock & notifications": "Stock reservations (reserved, released when a checkout fails) and notifications by "
                             "stage (queued, delivered, reclaimed, dropped), per second.",
    "Latest ERROR & WARN logs (click trace_id → trace)": "Newest WARN and ERROR lines of all shop services; the "
                                                         "trace_id opens the request's trace.",
    "CPU per pod": "CPU used by every shop pod (all its containers).",
    "Memory working set per pod": "Working set memory of every shop pod (what counts against memory limits).",
}


def patch(out):
    """Give the hand-made overview the same links, annotations and panel descriptions as the generated dashboards,
    and count container metrics once."""
    p = os.path.join(out, FILE)
    ov = json.load(open(p))
    ov["links"] = LINKS
    ov["annotations"] = {"list": ANNOTATIONS}
    describe(ov["panels"], DESCRIPTIONS)
    # container_* exist on both kubelet endpoints: count them once (see common.CADVISOR).
    for panel in ov["panels"]:
        for t in panel.get("targets", []):
            for metric in ("container_cpu_usage_seconds_total", "container_memory_working_set_bytes"):
                if t.get("expr", "").find(metric + '{namespace="shop"') >= 0:
                    t["expr"] = t["expr"].replace(metric + '{namespace="shop"', metric + "{" + CADVISOR + ', namespace="shop"')
    with open(p, "w") as f:
        json.dump(ov, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{FILE}: links updated")
