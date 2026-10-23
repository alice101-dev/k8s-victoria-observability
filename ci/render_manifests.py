#!/usr/bin/env python3
"""Render every Argo CD Application of the two app-of-apps charts the way Argo CD does, then validate the output.

usage: python3 ci/render_manifests.py [out_dir]          (default: ./rendered)
needs: helm and kustomize in the versions Argo CD bundles (CI pins them), kubeconform, PyYAML, jsonschema

1. Renders observability/argocd and apps/argocd (the root charts Terraform installs) with placeholder values
   for what Terraform injects (repoURL, project-specific registry and bucket names).
2. Renders each Application: upstream charts with their pinned version and `$values/...` files from this
   repository, local Helm charts with their valuesObject, Kustomize paths with the Application's patches.
3. Validates built-in kinds with kubeconform (strict, Kubernetes version of the GKE cluster) and custom
   resources against the CRDs rendered from the operators' charts (unknown fields rejected, as the API server
   prunes them silently). Kinds whose CRD is not part of this repository (Argo CD's) are listed as skipped.
4. Checks what the OpenAPI schemas cannot express but the API server rejects: named ports (container ports,
   string targetPorts, probe ports) must be IANA service names (at most 15 characters).
Exits non-zero on any rendering or validation error.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

import jsonschema
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "https://github.com/ci/placeholder.git"
KUBE_VERSION = "1.35.6"  # GKE version of the staging cluster
ROOTS = [
    ("observability", "observability/argocd", {"repoURL": REPO, "projectId": "ci-project"}),
    ("apps", "apps/argocd", {"repoURL": REPO, "imageRegistry": "ci.invalid/shop", "backupBucket": "ci-bucket"}),
]


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout


def helm_template(release, chart, namespace, values_files=(), values=None, repo=None, version=None):
    cmd = ["helm", "template", release, chart, "--namespace", namespace, "--include-crds",
           "--kube-version", KUBE_VERSION]
    if repo:
        cmd += ["--repo", repo, "--version", version]
    for f in values_files:
        cmd += ["--values", f]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as tmp:
        if values:
            yaml.safe_dump(values, tmp)
            tmp.flush()
            cmd += ["--values", tmp.name]
        return run(cmd)


def kustomize_build(path, patches=None):
    if not patches:
        return run(["kustomize", "build", path])
    # Same as Argo CD: the Application's patches on top of the path's kustomization.
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "kustomization.yaml"), "w") as f:
            yaml.safe_dump({"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
                            "resources": [os.path.relpath(os.path.realpath(path), os.path.realpath(tmp))],
                            "patches": patches}, f)
        return run(["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", tmp])


def render_app(app):
    spec = app["spec"]
    name, namespace = app["metadata"]["name"], spec["destination"]["namespace"]
    out = []
    for src in spec.get("sources") or [spec["source"]]:
        helm = src.get("helm", {})
        if "chart" in src:
            files = [os.path.join(ROOT, f.removeprefix("$values/")) for f in helm.get("valueFiles", [])]
            out.append(helm_template(helm.get("releaseName", name), src["chart"], namespace, files,
                                     helm.get("valuesObject"), src["repoURL"], src["targetRevision"]))
        elif "path" in src:
            assert src["repoURL"] == REPO, f"{name}: path source outside this repository"
            path = os.path.join(ROOT, src["path"])
            if os.path.exists(os.path.join(path, "kustomization.yaml")):
                out.append(kustomize_build(path, src.get("kustomize", {}).get("patches")))
            elif os.path.exists(os.path.join(path, "Chart.yaml")):
                out.append(helm_template(helm.get("releaseName", name), path, namespace,
                                         values=helm.get("valuesObject")))
            else:
                raise RuntimeError(f"{name}: {src['path']} is neither a Kustomize nor a Helm directory")
    return "\n---\n".join(out)


def builtin_group(api_version):
    """Kubernetes' own API groups: the core group, single-word groups (apps, batch, policy) and *.k8s.io."""
    group = api_version.rpartition("/")[0]
    return "." not in group or group.endswith(".k8s.io")


# k8s.io/apimachinery validation.IsValidPortName: lowercase letters, digits and single hyphens, at least one
# letter, at most 15 characters.
IANA_SVC_NAME = re.compile(r"^(?=.{1,15}$)(?=.*[a-z])[a-z0-9]+(-[a-z0-9]+)*$")
POD_TEMPLATE = {"Pod": ("spec",), "Deployment": ("spec", "template", "spec"), "StatefulSet": ("spec", "template", "spec"),
                "DaemonSet": ("spec", "template", "spec"), "ReplicaSet": ("spec", "template", "spec"),
                "Job": ("spec", "template", "spec"), "CronJob": ("spec", "jobTemplate", "spec", "template", "spec")}


def port_name_errors(d):
    """Named ports the API server would reject (kubeconform's schemas have no length or pattern for them)."""
    bad = []
    if d["kind"] == "Service":
        bad += [p["targetPort"] for p in d["spec"].get("ports", []) if isinstance(p.get("targetPort"), str)]
    elif d["kind"] in POD_TEMPLATE and builtin_group(d["apiVersion"]):
        spec = d
        for k in POD_TEMPLATE[d["kind"]]:
            spec = (spec or {}).get(k) or {}
        for c in spec.get("containers", []) + spec.get("initContainers", []):
            bad += [p["name"] for p in c.get("ports", []) if "name" in p]
            for probe in ("livenessProbe", "readinessProbe", "startupProbe"):
                for handler in ("httpGet", "tcpSocket", "grpc"):
                    port = ((c.get(probe) or {}).get(handler) or {}).get("port")
                    if isinstance(port, str):
                        bad.append(port)
    return [f"{d['kind']} {d['metadata'].get('name')}: port name {n!r} is not a valid IANA service name "
            "(lowercase alphanumerics and '-', at least one letter, at most 15 characters)"
            for n in bad if not IANA_SVC_NAME.match(n)]


def load_docs(text):
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict) and d.get("kind")]


def strict(schema):
    """Reject unknown fields like a structural schema prunes them (instead of silently dropping them)."""
    if isinstance(schema, dict):
        if (schema.get("type") == "object" and "properties" in schema and "additionalProperties" not in schema
                and not schema.get("x-kubernetes-preserve-unknown-fields")):
            schema = dict(schema, additionalProperties=False)
        return {k: strict(v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [strict(x) for x in schema]
    return schema


def main():
    out_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "rendered"))
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "*.yaml")):
        os.remove(f)
    errors, docs = [], []

    for root_name, chart, values in ROOTS:
        text = helm_template(root_name, os.path.join(ROOT, chart), "argocd", values=values)
        open(os.path.join(out_dir, f"root-{root_name}.yaml"), "w").write(text)
        for app in (d for d in load_docs(text) if d["kind"] == "Application"):
            name = app["metadata"]["name"]
            try:
                rendered = render_app(app)
            except Exception as e:  # report every app, not only the first failure
                errors.append(f"render {name}: {e}")
                continue
            open(os.path.join(out_dir, f"{name}.yaml"), "w").write(rendered)
            app_docs = load_docs(rendered)
            docs += [(name, d) for d in app_docs]
            print(f"rendered {name}: {len(app_docs)} objects")

    # Built-in kinds: kubeconform against the Kubernetes OpenAPI schemas. Custom resources have no schema there
    # and are skipped by kubeconform; a skipped built-in kind is an error (wrong apiVersion or kind), except
    # CustomResourceDefinitions (the schema registry has none; they come from the upstream operator charts).
    r = subprocess.run(["kubeconform", "-strict", "-summary", "-verbose", "-ignore-missing-schemas",
                        "-kubernetes-version", KUBE_VERSION, "-output", "json", out_dir], capture_output=True, text=True)
    report = json.loads(r.stdout or "{}")
    for res in report.get("resources", []):
        group = res.get("kind", "")
        if res["status"] in ("statusInvalid", "statusError"):
            errors.append(f"kubeconform: {res['filename']}: {res.get('kind')} {res.get('name')}: {res.get('msg')}")
        elif (res["status"] == "statusSkipped" and builtin_group(res.get("version", ""))
              and res.get("kind") != "CustomResourceDefinition"):
            errors.append(f"kubeconform: no schema for built-in {res.get('version')}/{group} {res.get('name')}")
    print(f"kubeconform: {json.dumps(report.get('summary', {}))}")
    if r.returncode and not report.get("resources"):
        errors.append(f"kubeconform failed: {r.stderr.strip()}")

    # Custom resources: the CRDs rendered from the operators' charts.
    schemas = {}
    for _, d in docs:
        if d["kind"] == "CustomResourceDefinition":
            for v in d["spec"]["versions"]:
                schemas[(d["spec"]["group"], v["name"], d["spec"]["names"]["kind"])] = v["schema"]["openAPIV3Schema"]
    checked, skipped = 0, set()
    for app, d in docs:
        group, _, version = d["apiVersion"].rpartition("/")
        if builtin_group(d["apiVersion"]):
            continue  # validated by kubeconform
        schema = schemas.get((group, version, d["kind"]))
        if schema is None:
            skipped.add(f"{d['apiVersion']}/{d['kind']}")
            continue
        try:
            jsonschema.validate(d, strict(schema))
            checked += 1
        except jsonschema.ValidationError as e:
            path = "/".join(str(p) for p in e.absolute_path)
            errors.append(f"{app}: {d['kind']} {d['metadata'].get('name')}: {path}: {e.message[:300]}")
    print(f"custom resources validated against their CRDs: {checked}")

    port_errors = [f"{app}: {e}" for app, d in docs for e in port_name_errors(d)]
    errors += port_errors
    print(f"named ports checked: {len(port_errors)} invalid")
    if skipped:
        print(f"no CRD in this repository, not validated: {', '.join(sorted(skipped))}")

    for e in errors:
        print(f"ERROR {e}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
