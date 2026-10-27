#!/usr/bin/env python3
import copy
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
KUBE_VERSION = "1.35.6"
ENV = os.environ.get("RENDER_ENV", "staging")
def tfvars_text(workspace):
    return open(os.path.join(ROOT, "terraform/environments", ENV, workspace, f"{ENV}.tfvars")).read()


def enabled_components():
    block = re.search(r"^enabled_components\s*=\s*\[(.*?)\]", tfvars_text("argocd-bootstrap"), re.M | re.S).group(1)
    return re.findall(r'"([^"]+)"', block)


CLUSTER = {
    "name": "in-cluster",
    "nameNormalized": "in-cluster",
    "server": "https://kubernetes.default.svc",
    "metadata": {
        "labels": {"env": ENV, **{f"enable_{c}": "true" for c in enabled_components()}},
        "annotations": {
            "platform/project-id": "ci-project",
            "platform/image-registry": "ci.invalid/deus",
            "platform/backup-bucket": "ci-bucket",
        },
    },
}
ARGOCD_REPO = "https://argoproj.github.io/argo-helm"
EXPR = re.compile(r"\{\{-?\s*(.*?)\s*-?\}\}")
IANA_SVC_NAME = re.compile(r"^(?=.{1,15}$)(?=.*[a-z])[a-z0-9]+(-[a-z0-9]+)*$")
POD_TEMPLATE = {"Pod": ("spec",), "Deployment": ("spec", "template", "spec"), "StatefulSet": ("spec", "template", "spec"),
                "DaemonSet": ("spec", "template", "spec"), "ReplicaSet": ("spec", "template", "spec"),
                "Job": ("spec", "template", "spec"), "CronJob": ("spec", "jobTemplate", "spec", "template", "spec")}


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout


def load_docs(text):
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict) and d.get("kind")]


def tfvar(workspace, name):
    return re.search(rf'^{name}\s*=\s*"([^"]+)"', tfvars_text(workspace), re.M).group(1)


def lookup(params, expr):
    m = re.fullmatch(r'index\s+\.([\w.]+)\s+"([^"]+)"', expr)
    if m:
        path, key = m.group(1).split("."), m.group(2)
    elif re.fullmatch(r"\.[\w.]+", expr):
        path, key = expr[1:].split("."), None
    else:
        raise RuntimeError(f"unsupported template expression {{{{ {expr} }}}}")
    value = params
    for p in path:
        if not isinstance(value, dict) or p not in value:
            raise RuntimeError(f"missing key {'.'.join(path)} in {{{{ {expr} }}}}")
        value = value[p]
    if key is not None:
        if key not in value:
            raise RuntimeError(f"missing key {key!r} in {{{{ {expr} }}}}")
        value = value[key]
    return value


def render_template(obj, params):
    if isinstance(obj, str):
        return EXPR.sub(lambda m: str(lookup(params, m.group(1))), obj)
    if isinstance(obj, dict):
        return {render_template(k, params): render_template(v, params) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_template(v, params) for v in obj]
    return obj


def git_file_params(pattern):
    out = []
    for f in sorted(glob.glob(os.path.join(ROOT, pattern))):
        rel = os.path.relpath(f, ROOT)
        directory = os.path.dirname(rel)
        params = yaml.safe_load(open(f)) or {}
        params["path"] = {"path": directory, "basename": os.path.basename(directory),
                          "filename": os.path.basename(rel), "basenameNormalized": os.path.basename(directory),
                          "segments": directory.split("/")}
        out.append(params)
    return out


def generator_params(gen):
    if "clusters" in gen:
        selector = gen["clusters"].get("selector", {})
        labels = CLUSTER["metadata"]["labels"]
        for e in selector.get("matchExpressions", []):
            if e["operator"] == "Exists" and e["key"] not in labels:
                return []
        for k, v in selector.get("matchLabels", {}).items():
            if labels.get(k) != v:
                return []
        return [copy.deepcopy(CLUSTER)]
    if "git" in gen:
        return [p for f in gen["git"]["files"] for p in git_file_params(f["path"])]
    if "matrix" in gen:
        first, second = gen["matrix"]["generators"]
        merged = []
        for x in generator_params(first):
            for y in generator_params(render_template(copy.deepcopy(second), x)):
                clash = set(x) & set(y)
                if clash:
                    raise RuntimeError(f"matrix generators share keys {sorted(clash)}")
                merged.append({**x, **y})
        return merged
    raise RuntimeError(f"unsupported generator {list(gen)}")


def applications(appset):
    spec = appset["spec"]
    apps = []
    for params in (p for g in spec["generators"] for p in generator_params(g)):
        app = {"apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
               **render_template(copy.deepcopy(spec["template"]), params)}
        app["metadata"].setdefault("namespace", appset["metadata"]["namespace"])
        if "ignoreDifferences" in params and "ignoreDifferences" in spec.get("templatePatch", ""):
            app["spec"]["ignoreDifferences"] = params["ignoreDifferences"]
        apps.append(app)
    return apps


def helm_template(release, chart, namespace, values_files=(), values=None, repo=None, version=None, crds=True):
    cmd = ["helm", "template", release, chart, "--namespace", namespace, "--kube-version", KUBE_VERSION]
    if crds:
        cmd.append("--include-crds")
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
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, "kustomization.yaml"), "w") as f:
            yaml.safe_dump({"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
                            "resources": [os.path.relpath(os.path.realpath(path), os.path.realpath(tmp))],
                            "patches": patches}, f)
        return run(["kustomize", "build", "--load-restrictor", "LoadRestrictionsNone", tmp])


def local_repo(url):
    return url.rstrip("/").removesuffix(".git").endswith("/k8s-victoria-observability")


def render_app(app):
    spec = app["spec"]
    name, namespace = app["metadata"]["name"], spec["destination"]["namespace"]
    sources = spec.get("sources") or [spec["source"]]
    refs = {s["ref"] for s in sources if "ref" in s}
    out = []
    for src in sources:
        if "ref" in src:
            assert local_repo(src["repoURL"]), f"{name}: ref source outside this repository"
            continue
        helm = src.get("helm", {})
        if "chart" in src:
            files = []
            for f in helm.get("valueFiles", []):
                ref, _, rel = f.partition("/")
                assert ref.startswith("$") and ref[1:] in refs, f"{name}: value file {f} without a ref source"
                path = os.path.join(ROOT, rel)
                if os.path.exists(path):
                    files.append(path)
                elif not helm.get("ignoreMissingValueFiles"):
                    raise RuntimeError(f"{name}: missing value file {rel}")
            out.append(helm_template(helm.get("releaseName", name), src["chart"], namespace, files,
                                     helm.get("valuesObject"), src["repoURL"], src["targetRevision"]))
        elif "path" in src:
            assert local_repo(src["repoURL"]), f"{name}: path source outside this repository"
            path = os.path.join(ROOT, src["path"])
            if os.path.exists(os.path.join(path, "kustomization.yaml")):
                out.append(kustomize_build(path, src.get("kustomize", {}).get("patches")))
            elif os.path.exists(os.path.join(path, "Chart.yaml")):
                out.append(helm_template(helm.get("releaseName", name), path, namespace, values=helm.get("valuesObject")))
            elif glob.glob(os.path.join(path, "*.yaml")):
                out.append("\n---\n".join(open(f).read() for f in sorted(glob.glob(os.path.join(path, "*.yaml")))))
            else:
                raise RuntimeError(f"{name}: {src['path']} has no manifests")
    return "\n---\n".join(out)


def builtin_group(api_version):
    group = api_version.rpartition("/")[0]
    return "." not in group or group.endswith(".k8s.io")


def port_name_errors(d):
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


def strict(schema):
    if isinstance(schema, dict):
        if (schema.get("type") == "object" and "properties" in schema and "additionalProperties" not in schema
                and not schema.get("x-kubernetes-preserve-unknown-fields")):
            schema = dict(schema, additionalProperties=False)
        return {k: strict(v) for k, v in schema.items()}
    if isinstance(schema, list):
        return [strict(x) for x in schema]
    return schema


def write(out_dir, name, text):
    with open(os.path.join(out_dir, f"{name}.yaml"), "w") as f:
        f.write(text)


def main():
    out_dir = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "rendered"))
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "*.yaml")):
        os.remove(f)
    errors, docs = [], []

    argocd = helm_template("argocd", "argo-cd", "argocd", repo=ARGOCD_REPO,
                           version=tfvar("argocd-bootstrap", "argocd_chart_version"))
    argo_crds = [d for d in load_docs(argocd) if d["kind"] == "CustomResourceDefinition"]
    docs += [("argocd", d) for d in argo_crds]

    root_text = open(os.path.join(ROOT, "bootstrap/root.yaml")).read()
    root_text = root_text.replace("${repo_url}", "https://github.com/alice101-dev/k8s-victoria-observability.git")
    root_text = root_text.replace("${revision}", "main")
    root = load_docs(root_text)
    write(out_dir, "root", root_text)
    docs += [("root", d) for d in root]

    gitops = render_app(root[0])
    write(out_dir, "appsets", gitops)
    gitops_docs = load_docs(gitops)
    docs += [("appsets", d) for d in gitops_docs]

    generated = [a for d in gitops_docs if d["kind"] == "ApplicationSet" for a in applications(d)]
    components = set(enabled_components())
    for kind in ("addons", "configs"):
        for f in sorted(glob.glob(os.path.join(ROOT, kind, "*", "config.yaml"))):
            name = os.path.basename(os.path.dirname(f))
            needs = (yaml.safe_load(open(f)) or {}).get("requires", name) if kind == "configs" else name
            if needs not in components:
                print(f"not enabled in {ENV}: {kind}/{name} (enable_{needs})")
    names = [a["metadata"]["name"] for a in generated]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        errors.append(f"application name {dup} generated more than once")
    projects = {d["metadata"]["name"] for d in gitops_docs if d["kind"] == "AppProject"}
    for app in generated:
        name = app["metadata"]["name"]
        docs.append((f"appset:{name}", app))
        if app["spec"]["project"] not in projects:
            errors.append(f"{name}: project {app['spec']['project']} is not defined in appsets/")
        try:
            rendered = render_app(app)
        except Exception as e:
            errors.append(f"render {name}: {e}")
            continue
        write(out_dir, name, rendered)
        app_docs = load_docs(rendered)
        docs += [(name, d) for d in app_docs]
        print(f"rendered {name}: {len(app_docs)} objects")

    r = subprocess.run(["kubeconform", "-strict", "-summary", "-verbose", "-ignore-missing-schemas",
                        "-kubernetes-version", KUBE_VERSION, "-output", "json", out_dir], capture_output=True, text=True)
    report = json.loads(r.stdout or "{}")
    for res in report.get("resources", []):
        if res["status"] in ("statusInvalid", "statusError"):
            errors.append(f"kubeconform: {res['filename']}: {res.get('kind')} {res.get('name')}: {res.get('msg')}")
        elif (res["status"] == "statusSkipped" and builtin_group(res.get("version", ""))
              and res.get("kind") != "CustomResourceDefinition"):
            errors.append(f"kubeconform: no schema for built-in {res.get('version')}/{res.get('kind')} {res.get('name')}")
    print(f"kubeconform: {json.dumps(report.get('summary', {}))}")
    if r.returncode and not report.get("resources"):
        errors.append(f"kubeconform failed: {r.stderr.strip()}")

    schemas = {}
    for _, d in docs:
        if d["kind"] == "CustomResourceDefinition":
            for v in d["spec"]["versions"]:
                schemas[(d["spec"]["group"], v["name"], d["spec"]["names"]["kind"])] = v["schema"]["openAPIV3Schema"]
    checked, skipped = 0, set()
    for app, d in docs:
        group, _, version = d["apiVersion"].rpartition("/")
        if builtin_group(d["apiVersion"]):
            continue
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
