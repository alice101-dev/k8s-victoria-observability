"""Shop — Microservices Overview: hand-made dashboard; only its links and annotations are generated."""
import json
import os

from .common import ANNOTATIONS, LINKS

FILE = "shop-overview.json"


def patch(out):
    """Give the hand-made overview the same links and annotations as the generated dashboards."""
    p = os.path.join(out, FILE)
    ov = json.load(open(p))
    ov["links"] = LINKS
    ov["annotations"] = {"list": ANNOTATIONS}
    with open(p, "w") as f:
        json.dump(ov, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"{FILE}: links updated")
