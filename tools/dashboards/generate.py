import glob
import json
import os
import sys

from generator import (common, deus, deus_logs, deus_overview, deus_service, deus_slo, deus_traces, events, gateway,
    grafana, mcp, pipeline, platform)

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../..")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "infrastructure/configs/grafana/base/dashboards")

os.makedirs(OUT, exist_ok=True)
for module in (events, platform, pipeline, gateway, mcp, grafana, deus_overview, deus_slo, deus_service, deus_logs, deus_traces):
    common.write(module.FILE, module.DASHBOARD, OUT)
deus.write_rules(os.path.join(ROOT, "infrastructure/configs/victoria-metrics-k8s-stack/base/deus.yaml"))


def write_kustomization(directory):
    lines = ["apiVersion: kustomize.config.k8s.io/v1beta1", "kind: Kustomization", "configMapGenerator:"]
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        name = os.path.splitext(os.path.basename(path))[0]
        with open(path) as f:
            tags = json.load(f).get("tags", [])
        lines += [f"  - name: {name}-dashboard", "    files:", f"      - {name}.json", "    options:",
                  "      disableNameSuffixHash: true", "      labels:", '        grafana_dashboard: "1"',
                  "      annotations:", f"        grafana_folder: {'Deus' if 'deus' in tags else 'Platform'}"]
    with open(os.path.join(directory, "kustomization.yaml"), "w") as f:
        f.write("\n".join(lines) + "\n")


write_kustomization(OUT)
