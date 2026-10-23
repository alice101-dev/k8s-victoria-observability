#!/usr/bin/env python3
"""Validate every Grafana dashboard shipped from this repository.

usage: python3 ci/validate_dashboards.py [--offline]

Checks, per dashboard JSON (generated Shop and Platform dashboards, vendored CloudNativePG):
- valid JSON with a title and a uid that no other dashboard uses;
- unique panel ids and panels inside the 24-column grid;
- every datasource reference is a provisioned datasource (uids from observability/values/vm-k8s-stack.yaml),
  a Grafana built-in, or a datasource variable of the dashboard;
- every $variable used in queries, titles and links is defined by the dashboard (Grafana globals such as
  $__rate_interval excepted);
- every /d/<uid> link points at a dashboard that exists: one of these files, or a vendor dashboard from the
  pinned sources in vm-k8s-stack.yaml (fetched; skipped with --offline).
Exits non-zero with one line per problem.
"""
import glob
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARDS = ["apps/k8s/services/dashboards/*.json", "apps/k8s/data/dashboards/*.json",
              "observability/k8s-events/dashboards/*.json", "observability/platform/dashboards/*.json"]
STACK_VALUES = "observability/values/vm-k8s-stack.yaml"
BUILTIN_DATASOURCES = {"-- Grafana --", "grafana", "-- Mixed --", "-- Dashboard --", "__expr__"}
VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)|\$([A-Za-z_][A-Za-z0-9_]*)|\[\[([A-Za-z_][A-Za-z0-9_]*)")
LINK = re.compile(r"/d/([A-Za-z0-9_-]+)")

problems = []


def problem(name, msg):
    problems.append(f"{name}: {msg}")


def walk_panels(panels):
    for p in panels:
        yield p
        yield from walk_panels(p.get("panels", []))


def strings(obj):
    """Every string value inside obj (recursively)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def provisioned_datasources():
    """uids of the datasources provisioned by the k8s-stack chart (defaultDatasources section)."""
    text = open(os.path.join(ROOT, STACK_VALUES)).read()
    section = text.split("\ndefaultDatasources:\n", 1)[1]
    section = re.split(r"\n[a-zA-Z]", section, maxsplit=1)[0]
    return set(re.findall(r"^\s+uid: (\S+)\s*$", section, re.M))


def vendor_dashboard_uids():
    """uids of the vendor dashboards the k8s-stack sync job installs (pinned URLs in vm-k8s-stack.yaml)."""
    text = open(os.path.join(ROOT, STACK_VALUES)).read()
    section = text.split("\ndefaultDashboards:\n", 1)[1].split("\ndefaultRules:\n", 1)[0]
    uids = set()
    for url in re.findall(r"url: (https://\S+)", section):
        body = urllib.request.urlopen(url, timeout=60).read().decode()
        uids.update(re.findall(r'"uid":\s*"([A-Za-z0-9_-]+)"', body))
    return uids


def check(path, d, datasources, seen_uids):
    name = os.path.relpath(path, ROOT)
    if not d.get("title"):
        problem(name, "no title")
    uid = d.get("uid")
    if not uid:
        problem(name, "no uid")
    elif uid in seen_uids:
        problem(name, f"uid {uid} also used by {seen_uids[uid]}")
    else:
        seen_uids[uid] = name

    variables = {v["name"] for v in d.get("templating", {}).get("list", [])}
    ds_variables = {v["name"] for v in d.get("templating", {}).get("list", []) if v.get("type") == "datasource"}

    def check_datasource(where, ds):
        if ds is None:
            return
        uid = ds.get("uid") if isinstance(ds, dict) else ds
        if not uid:
            return
        m = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", uid)
        if m:
            if m.group(1) not in ds_variables:
                problem(name, f"{where}: datasource variable ${m.group(1)} is not defined")
        elif uid not in datasources and uid not in BUILTIN_DATASOURCES:
            problem(name, f"{where}: datasource uid {uid!r} is not provisioned")

    ids = set()
    for p in walk_panels(d.get("panels", [])):
        where = f"panel {p.get('id')} {p.get('title', '')!r}"
        if p.get("id") in ids:
            problem(name, f"{where}: duplicate panel id")
        ids.add(p.get("id"))
        grid = p.get("gridPos", {})
        if grid.get("x", 0) + grid.get("w", 0) > 24:
            problem(name, f"{where}: wider than the 24-column grid")
        if p.get("type") == "row":  # rows do not query (upstream rows may carry stale datasource refs)
            continue
        check_datasource(where, p.get("datasource"))
        for t in p.get("targets", []):
            check_datasource(f"{where} target {t.get('refId')}", t.get("datasource"))
    for v in d.get("templating", {}).get("list", []):
        check_datasource(f"variable {v.get('name')}", v.get("datasource"))
    for a in d.get("annotations", {}).get("list", []):
        check_datasource(f"annotation {a.get('name')}", a.get("datasource"))

    def used_variables(obj):
        return {next(g for g in m.groups() if g) for s in strings(obj) for m in VARIABLE.finditer(s)}

    # Server-side expressions (datasource __expr__) refer to the panel's other queries as $<refId>.
    undefined = set()
    for p in walk_panels(d.get("panels", [])):
        own = {k: v for k, v in p.items() if k != "panels"}
        ref_ids = {t.get("refId") for t in p.get("targets", [])}
        undefined |= used_variables(own) - variables - ref_ids
    undefined |= used_variables({"links": d.get("links", []), "annotations": d.get("annotations", {}),
                                 "templating": d.get("templating", {})}) - variables
    for var in sorted(v for v in undefined if not v.startswith("__")):
        problem(name, f"${var} is used but not defined as a dashboard variable")
    return {u for s in strings(d) for u in LINK.findall(s)}


def main():
    offline = "--offline" in sys.argv
    datasources = provisioned_datasources()
    if not datasources:
        sys.exit(f"no datasource uids found in {STACK_VALUES}")
    files = sorted(f for pattern in DASHBOARDS for f in glob.glob(os.path.join(ROOT, pattern)))
    seen_uids, links = {}, {}
    for path in files:
        try:
            d = json.load(open(path))
        except json.JSONDecodeError as e:
            problem(os.path.relpath(path, ROOT), f"invalid JSON: {e}")
            continue
        for uid in check(path, d, datasources, seen_uids):
            links.setdefault(uid, set()).add(os.path.relpath(path, ROOT))
    known = set(seen_uids)
    if not offline:
        known |= vendor_dashboard_uids()
    for uid, sources in sorted(links.items()):
        if uid not in known and (not offline or uid in seen_uids):
            problem(", ".join(sorted(sources)), f"link to /d/{uid}: no such dashboard")
        elif offline and uid not in seen_uids:
            print(f"skipped (offline): link to vendor dashboard /d/{uid}")
    print(f"{len(files)} dashboards, datasources {sorted(datasources)}, {len(links)} linked dashboard uids")
    for p in problems:
        print(f"ERROR {p}")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
