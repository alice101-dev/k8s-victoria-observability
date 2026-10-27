import os
import sys

from generator import common, events, pipeline, platform

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "../..")
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "configs/grafana/base/dashboards")

os.makedirs(OUT, exist_ok=True)
for module in (events, platform, pipeline):
    common.write(module.FILE, module.DASHBOARD, OUT)
