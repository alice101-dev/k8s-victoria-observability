import os
import sys

from generator import (common, deus, deus_logs, deus_overview, deus_service, deus_slo, deus_traces, events, gateway,
    mcp, pipeline, platform)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../..")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "infrastructure/configs/grafana/base/dashboards")

os.makedirs(OUT, exist_ok=True)
for module in (events, platform, pipeline, gateway, mcp, deus_overview, deus_slo, deus_service, deus_logs, deus_traces):
    common.write(module.FILE, module.DASHBOARD, OUT)
deus.write_rules(os.path.join(ROOT, "infrastructure/configs/victoria-metrics-k8s-stack/base/deus.yaml"))
