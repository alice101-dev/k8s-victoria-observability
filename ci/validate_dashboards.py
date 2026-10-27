#!/usr/bin/env python3
import glob
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASHBOARDS = ["infrastructure/configs/*/base/dashboards/*.json"]
DATASOURCES = "infrastructure/configs/grafana/base/datasources.yaml"
STACK_VALUES = "infrastructure/controllers/victoria-metrics-k8s-stack/values.yaml"
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
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)


def provisioned_datasources():
    text = open(os.path.join(ROOT, DATASOURCES)).read()
    return set(re.findall(r"^\s+uid: (\S+)\s*$", text, re.M))


def vendor_dashboard_uids():
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
        if p.get("type") == "row":
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
        sys.exit(f"no datasource uids found in {DATASOURCES}")
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
