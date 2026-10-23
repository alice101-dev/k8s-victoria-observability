"""Generate the SRE dashboards of the shop app (SLO overview, service drill-down, logs, traces, Redis) and the
Platform dashboards (Kubernetes events; GitOps & Secrets; observability pipeline health).

usage: python3 apps/k8s/services/dashboards/generate.py [out_dir]
       Rewrites shop-slo.json, shop-service.json, shop-logs.json and shop-traces.json next to this file and the
       links and annotations of shop-overview.json (the Helm chart ships every *.json in this folder),
       cluster-events.json in observability/k8s-events/dashboards (shipped by the k8s-events app) and
       platform-gitops-secrets.json and observability-pipeline.json in observability/platform/dashboards (shipped
       by the platform-monitoring app) and shop-redis.json in apps/k8s/data/dashboards (shipped by shop-data).
       With out_dir, everything is written there instead. CI (.github/workflows/ci.yaml) fails when the committed
       JSON differs from what this script generates, so edit the generator, never the JSON.

One module per dashboard in generator/, shared helpers in generator/common.py.
"""
import os
import sys

from generator import common, events, logs, overview, pipeline, platform, redis, service, slo, traces

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else HERE
ROOT = os.path.join(HERE, "../../../..")

for module in (slo, service, logs, traces):
    common.write(module.FILE, module.DASHBOARD, OUT)
overview.patch(OUT)
for module, folder in ((events, "observability/k8s-events/dashboards"), (platform, "observability/platform/dashboards"),
                       (pipeline, "observability/platform/dashboards"), (redis, "apps/k8s/data/dashboards")):
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, folder)
    os.makedirs(out, exist_ok=True)
    common.write(module.FILE, module.DASHBOARD, out)
