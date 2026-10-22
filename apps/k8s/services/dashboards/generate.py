"""Generate the SRE dashboards of the shop app (SLO overview, service drill-down, logs, traces) and the
cluster-wide Kubernetes events dashboard.

usage: python3 apps/k8s/services/dashboards/generate.py [out_dir]
       Rewrites shop-slo.json, shop-service.json, shop-logs.json and shop-traces.json next to this file and the
       links and annotations of shop-overview.json (the Helm chart ships every *.json in this folder), and
       cluster-events.json in observability/k8s-events/dashboards (shipped by the k8s-events app). With out_dir,
       everything is written there instead. CI (.github/workflows/dashboards.yaml) fails when the committed JSON
       differs from what this script generates, so edit the generator, never the JSON.

One module per dashboard in generator/, shared helpers in generator/common.py.
"""
import os
import sys

from generator import common, events, logs, overview, service, slo, traces

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
OUT_EVENTS = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "../../../../observability/k8s-events/dashboards")

for module in (slo, service, logs, traces):
    common.write(module.FILE, module.DASHBOARD, OUT)
overview.patch(OUT)
os.makedirs(OUT_EVENTS, exist_ok=True)
common.write(events.FILE, events.DASHBOARD, OUT_EVENTS)
