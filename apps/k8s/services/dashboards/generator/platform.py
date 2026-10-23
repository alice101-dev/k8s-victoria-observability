"""Platform — GitOps & Secrets: Argo CD (applications, syncs, Git), External Secrets and cert-manager, from the
scrape configs and alerts of observability/platform."""
from .common import (CADVISOR, COUNT, RI, SEARCH, SEARCH_VAR, VL, VM, by_name, code_colors, Dash, dashboard, logs_panel,
    restart_annotations, right_axis, stat, status_history, table, thresholds, ts, vl, vm)

FILE = "platform-gitops-secrets.json"
UID = "platform-gitops-secrets"

d = Dash()
DAY = 86400
NS_RE = 'namespace=~"argocd|external-secrets|cert-manager"'
JOBS = 'job=~"argocd-.+|external-secrets.*|cert-manager.*"'
APPS = "argocd_app_info"
APPS_ZERO = f"count({APPS}) * 0"  # 0 instead of no value, as long as Argo CD metrics arrive
SYNCS = 'argocd_app_sync_total{dry_run="false"}'
FAILED_SYNCS = 'argocd_app_sync_total{dry_run="false", phase=~"Error|Failed"}'
LOGS = '{kubernetes.pod_namespace=~"argocd|external-secrets|cert-manager"}'
# Argo CD logs logfmt text (level=error), cert-manager klog (E1023 ...), External Secrets JSON (level field).
ERRORS = '(level:in(error, fatal, panic) or _msg:~"level=(error|fatal)" or _msg:~"^[EF][0-9]{4} ")'
APP_LOGS_LINK = [{"title": "Show this application's errors in the logs below",
                  "url": f"/d/{UID}/platform-gitops-and-secrets?var-search=${{__value.raw}}&${{__url_time_range}}"}]
SYNC_STATES = {"Synced": ("Synced", "green"), "OutOfSync": ("OutOfSync", "orange"), "Unknown": ("Unknown", "red")}
HEALTH = {"Healthy": ("Healthy", "green"), "Progressing": ("Progressing", "blue"), "Suspended": ("Suspended", "orange"),
          "Missing": ("Missing", "red"), "Degraded": ("Degraded", "red"), "Unknown": ("Unknown", "orange")}
# Health over time: one code per group of states (regexp on health_status).
HEALTH_STATES = {"Healthy": ("Healthy", "green"), "Progressing": ("Progressing", "blue"),
                 "Suspended|Missing|Unknown": ("Suspended, Missing or Unknown", "orange"), "Degraded": ("Degraded", "red")}
READY = {"1": ("Ready", "green"), "0": ("Not ready", "red")}
TIME_LEFT = thresholds(("red", None), ("orange", 7 * DAY), ("green", 14 * DAY))  # dtdurations, decimals 1: two units
fixed = lambda name, color, matcher="byName": {"matcher": {"id": matcher, "options": name},
                                               "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


def mappings(states):
    """Value mappings {value: (text, colour)}; values are label values or, for state codes, the code."""
    return [{"type": "value", "options": {v: {"text": t, "color": c, "index": i}
                                          for i, (v, (t, c)) in enumerate(states.items())}}]


def mapped(name, width, states, cell="color-text"):
    return by_name(name, [{"id": "custom.width", "value": width}, {"id": "custom.cellOptions", "value": {"type": cell}},
                          {"id": "mappings", "value": mappings(states)}])


def failures(name):
    """A count column that shows 0 for no data and turns red from 1."""
    return by_name(name, [{"id": "unit", "value": "short"}, {"id": "decimals", "value": 0}, {"id": "noValue", "value": "0"},
                          {"id": "custom.width", "value": 100}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                          {"id": "thresholds", "value": thresholds(("green", None), ("red", 1))}])


def merged(columns, hide=("Time",), first=None):
    """Instant table queries merged on their common labels; columns = [(field, header)] in order. first: a field
    whose highest values come first (then hidden), e.g. a 0/1 needs-attention score."""
    order = [{"id": "sortBy", "options": {"fields": {}, "sort": [{"field": first, "desc": True}]}}] if first else []
    organize = {"id": "organize", "options": {"excludeByName": {h: True for h in hide},
                                              "indexByName": {f: i for i, (f, _) in enumerate(columns)},
                                              "renameByName": dict(columns)}}
    return [{"id": "merge", "options": {}}] + order + [organize]


def state_history(title, label, states, desc):
    """One row per application, coloured by the state of `label`: each state is a code, the highest wins."""
    expr = "max by (name) (" + " or ".join(f'{APPS}{{{label}=~"{rx}"}} * {i}' for i, rx in enumerate(states)) + ")"
    p = status_history(title, vm(expr, "{{name}}"), desc, thresholds(("green", None)))
    p["fieldConfig"]["defaults"]["mappings"] = mappings({str(i): v for i, v in enumerate(states.values())})
    return p


def instant(expr, ref):
    return vm(expr, ref=ref, instant=True, fmt="table")


def per_interval(p, interval="5m"):
    """Bars of counts per interval (round(increase(...[$__interval]))) need a fixed interval."""
    p["interval"] = interval
    return p


d.row("Overview")
for p in (
    stat("Applications out of sync", f'count({APPS}{{sync_status!="Synced"}}) or {APPS_ZERO}', "short",
         thresholds(("green", None), ("orange", 1)), "Applications whose live state differs from Git (OutOfSync) or "
         "cannot be compared with it (Unknown). With automated sync and self-heal this is back to 0 within seconds.",
         decimals=0),
    stat("Applications not healthy", f'count({APPS}{{health_status!="Healthy"}}) or {APPS_ZERO}', "short",
         thresholds(("green", None), ("orange", 1)), "Progressing during a rollout; Degraded, Missing or Unknown "
         "otherwise (the Health column says which).", decimals=0),
    stat("Failed syncs", f"sum(round(increase({FAILED_SYNCS}[$__range]))) or {APPS_ZERO}", "short",
         thresholds(("green", None), ("red", 1)), "Sync operations that ended Failed or Error in the time range, "
         "after their retries.", decimals=0),
    stat("ExternalSecrets not synced", 'sum(externalsecret_status_condition{condition="Ready", status!="True"})',
         "short", thresholds(("green", None), ("red", 1)), "ExternalSecrets whose last refresh failed; their Secrets "
         "keep the previous data.", decimals=0),
    stat("Certificates not ready", 'sum(certmanager_certificate_ready_status{condition!="True"})', "short",
         thresholds(("green", None), ("red", 1)), "Certificates cert-manager could not issue or renew.", decimals=0),
    stat("Next certificate expiry", "min(certmanager_certificate_expiration_timestamp_seconds > 0) - time()",
         "dtdurations", TIME_LEFT, "Time until the first certificate expires. cert-manager renews ahead of expiry "
         "(renewBefore), so a low value means renewals fail.", decimals=1),
):
    d.add(p, 4, 4)

d.row("Argo CD — applications")
d.add(table("Applications",
            # A: 1 when not Synced or not Healthy, those applications come first. Merged rows keep the order of the
            # first query (by name), so the rest stays alphabetical.
            [instant(f'max by (name) ({APPS}{{sync_status!="Synced"}} or {APPS}{{health_status!="Healthy"}} '
                     f"or {APPS} * 0)", "A"),
             instant(f"max by (name, project, dest_namespace, sync_status, health_status, autosync_enabled) ({APPS})", "B"),
             instant(f"sum by (name) (round(increase({SYNCS}[$__range])))", "C"),
             instant(f"sum by (name) (round(increase({FAILED_SYNCS}[$__range])))", "D")],
            merged([("name", "Application"), ("project", "Project"), ("dest_namespace", "Namespace"),
                    ("sync_status", "Sync"), ("health_status", "Health"), ("autosync_enabled", "Auto-sync"),
                    ("Value #C", "Syncs"), ("Value #D", "Failed syncs")], hide=("Time", "Value #A", "Value #B"),
                   first="Value #A"),
            overrides=[by_name("Application", [{"id": "links", "value": APP_LOGS_LINK}]),
                       by_name("Project", [{"id": "custom.width", "value": 130}]),
                       by_name("Namespace", [{"id": "custom.width", "value": 150}]),
                       mapped("Sync", 120, SYNC_STATES), mapped("Health", 120, HEALTH),
                       mapped("Auto-sync", 100, {"true": ("On", "green"), "false": ("Off", "orange")}),
                       COUNT("Syncs", 80), failures("Failed syncs")],
            desc="Every Argo CD Application with its project, destination namespace, sync and health status, whether "
                 "it syncs automatically, and its sync operations in the time range; applications that are not Synced "
                 "and Healthy come first. Click an application to list its errors in the Errors in the logs row."),
      24, 12)
d.add(state_history("Health over time", "health_status", HEALTH_STATES,
                    "Health of each application: when it was Progressing (rollouts), Degraded or Missing."), 12, 10)
d.add(state_history("Sync status over time", "sync_status", SYNC_STATES,
                    "When each application differed from Git (OutOfSync, until the automated sync applied it) or "
                    "could not be compared (Unknown: manifest generation or the cluster cache failed)."), 12, 10)
d.add(per_interval(ts("Syncs", [vm(f"sum by (phase) (round(increase({SYNCS}[$__interval])))", "{{phase}}")],
                      "short", stack=True, bars=True, decimals=0, legend_calcs=("sum",),
                      overrides=[fixed("Succeeded", "green"), fixed("Failed", "red"), fixed("Error", "red")],
                      desc="Sync operations per 5 minutes by result. Every merge to Git and every self-heal of drift "
                           "is a sync; Failed and Error count once per operation, after its retries.")), 8, 7)
d.add(ts("Reconciliation", [vm(f"histogram_quantile(0.5, sum by (le) (rate(argocd_app_reconcile_bucket{RI})))", "p50"),
                            vm(f"histogram_quantile(0.95, sum by (le) (rate(argocd_app_reconcile_bucket{RI})))", "p95", "B"),
                            vm(f"sum(rate(argocd_app_reconcile_count{RI}))", "reconciliations/s", "C")],
         "s", overrides=[right_axis("C", "ops")],
         desc="How long the controller takes to compare an application with the cluster (left) and how often it "
              "does (right). Slow reconciliations delay syncs and self-heal."), 8, 7)
d.add(ts("Kubernetes API requests", [vm(f'sum by (response_code) (rate(argocd_app_k8s_request_total{{dry_run="false"}}{RI}))',
                                        "{{response_code}}")],
         "reqps", stack=True, overrides=code_colors(),
         desc="Requests of the application controller to the Kubernetes API by response code. 4xx: a resource is "
              "rejected (validation, admission webhook, RBAC); 5xx: the API server fails."), 8, 7)

d.row("Argo CD — Git, repo-server and API")
d.add(ts("Git requests", [vm(f"sum by (request_type) (rate(argocd_git_request_total{RI}))", "{{request_type}}"),
                          vm(f"sum(rate(argocd_git_fetch_fail_total{RI}))", "fetch failures", "B"),
                          vm(f"sum(rate(argocd_git_lsremote_fail_total{RI}))", "ls-remote failures", "C")],
         "reqps", overrides=[fixed(".* failures", "red", "byRegexp")],
         desc="Requests of the repo-server to the Git repository: ls-remote resolves the branch on every refresh, "
              "fetch downloads new commits. Failures: network, credentials, or the repository is gone."), 6, 7)
d.add(ts("Git request duration (p95)",
         [vm(f"histogram_quantile(0.95, sum by (le, request_type) (rate(argocd_git_request_duration_seconds_bucket{RI})))",
             "{{request_type}}")],
         "s", desc="95th percentile of the Git request duration by type."), 6, 7)
d.add(ts("Pending work", [vm("sum(argocd_repo_pending_request_total)", "manifest requests waiting for a repository lock"),
                          vm("sum by (command) (argocd_kubectl_exec_pending)", "kubectl {{command}} pending", "B")],
         "short", decimals=0, min_=0,
         desc="Work queued in the repo-server (manifest generation waiting for a repository) and in the controller "
              "(kubectl apply or auth running). A queue that stays up means Argo CD is short of CPU or blocked."), 6, 7)
d.add(ts("API requests by gRPC code",
         [vm(f'sum by (grpc_code) (rate(grpc_server_handled_total{{job="argocd-server-metrics", '
             f'grpc_service!="grpc.health.v1.Health"}}{RI})) > 0', "{{grpc_code}}")],
         "reqps", stack=True, overrides=[fixed("OK", "green"), fixed("Unauthenticated|PermissionDenied", "orange", "byRegexp"),
                                         fixed("Internal|Unavailable|Unknown|DeadlineExceeded", "red", "byRegexp")],
         desc="Requests to the Argo CD API server (UI, CLI) by result, health checks excluded."), 6, 7)

d.row("External Secrets")
d.add(table("ExternalSecrets",
            [instant('max by (namespace, name) (externalsecret_status_condition{condition="Ready", status="True"})', "A"),
             instant("sum by (namespace, name) (round(increase(externalsecret_sync_calls_total[$__range])))", "B"),
             instant("sum by (namespace, name) (round(increase(externalsecret_sync_calls_error[$__range])))", "C"),
             instant("max by (namespace, name) (externalsecret_reconcile_duration)", "D")],
            merged([("namespace", "Namespace"), ("name", "ExternalSecret"), ("Value #A", "Ready"),
                          ("Value #B", "Reconciles"), ("Value #C", "Errors"), ("Value #D", "Last reconcile took")]),
            overrides=[by_name("Namespace", [{"id": "custom.width", "value": 130}]),
                       mapped("Ready", 110, READY, "color-background"), COUNT("Reconciles", 100), failures("Errors"),
                       by_name("Last reconcile took", [{"id": "unit", "value": "ns"}, {"id": "custom.width", "value": 150}])],
            desc="Every ExternalSecret with its Ready condition (the last refresh succeeded), its reconciles and "
                 "errors in the time range. A few errors right after a restart are normal (the webhook and the "
                 "generators start in parallel); Not ready means the Secret no longer follows its source.",
            sort=[{"displayName": "Namespace", "desc": False}]), 12, 8)
d.add(per_interval(ts("Reconciles and errors",
                      [vm("sum(round(increase(externalsecret_sync_calls_total[$__interval])))", "reconciles"),
                       vm("sum by (namespace, name) (round(increase(externalsecret_sync_calls_error[$__interval]))) > 0",
                          "{{namespace}}/{{name}} errors", "B")],
                      "short", bars=True, decimals=0, legend_calcs=("sum",),
                      overrides=[fixed(".* errors", "red", "byRegexp")],
                      desc="ExternalSecret reconciles per 5 minutes (refresh interval, changes, retries) and the "
                           "ExternalSecrets that failed.")), 12, 8)
d.add(table("Secret stores",
            [instant('label_replace(max by (namespace, name) (secretstore_status_condition{condition="Ready", status="True"}), '
                     '"kind", "SecretStore", "", "") or label_replace(max by (name) (clustersecretstore_status_condition'
                     '{condition="Ready", status="True"}), "kind", "ClusterSecretStore", "", "")', "A")],
            merged([("kind", "Kind"), ("namespace", "Namespace"), ("name", "Store"), ("Value", "Ready")]),
            overrides=[mapped("Ready", 110, READY, "color-background")],
            desc="SecretStores and ClusterSecretStores (provider connections, e.g. GCP Secret Manager). Empty while "
                 "every ExternalSecret uses a generator."), 8, 6)
d.add(ts("Controller reconciles", [vm(f'sum by (result) (rate(controller_runtime_reconcile_total{{job="external-secrets-metrics"}}{RI}))',
                                      "{{result}}")],
         "ops", stack=True, overrides=[fixed("success", "green"), fixed("error", "red")],
         desc="Reconciles of the External Secrets controller (all kinds) by result; requeue_after is the scheduled "
              "refresh."), 8, 6)
d.add(ts("Webhook requests", [vm(f'sum by (code) (rate(controller_runtime_webhook_requests_total{{job="external-secrets-webhook"}}{RI}))',
                                 "{{code}}")],
         "reqps", stack=True, overrides=code_colors(),
         desc="Validation of ExternalSecrets and SecretStores on create and update. Errors here make Argo CD syncs "
              "of these objects fail."), 8, 6)

d.row("cert-manager")
# Timestamps are 0 until a certificate is first issued: empty cells instead of 1970.
CERT_EXPIRY = "max by (namespace, name) (certmanager_certificate_expiration_timestamp_seconds > 0)"
d.add(table("Certificates",
            [instant('max by (namespace, name, issuer_kind, issuer_name) (certmanager_certificate_ready_status{condition="True"})', "A"),
             instant("max by (namespace, name) (certmanager_certificate_renewal_timestamp_seconds > 0) * 1000", "B"),
             instant(f"{CERT_EXPIRY} * 1000", "C"),
             instant(f"{CERT_EXPIRY} - time()", "D")],
            merged([("namespace", "Namespace"), ("name", "Certificate"), ("issuer_kind", "Issuer kind"),
                          ("issuer_name", "Issuer"), ("Value #A", "Ready"), ("Value #B", "Renews"),
                          ("Value #C", "Expires"), ("Value #D", "Time left")]),
            overrides=[by_name("Namespace", [{"id": "custom.width", "value": 130}]),
                       by_name("Issuer kind", [{"id": "custom.width", "value": 110}]),
                       mapped("Ready", 110, READY, "color-background"),
                       by_name("Renews", [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "custom.width", "value": 130}]),
                       by_name("Expires", [{"id": "unit", "value": "dateTimeFromNow"}, {"id": "custom.width", "value": 130}]),
                       by_name("Time left", [{"id": "unit", "value": "dtdurations"}, {"id": "decimals", "value": 1},
                                             {"id": "custom.width", "value": 190},
                                             {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                                             {"id": "thresholds", "value": TIME_LEFT}])],
            desc="Every cert-manager Certificate with its issuer, whether it is ready, when cert-manager renews it "
                 "(renewBefore ahead of expiry) and when it expires. A renewal time in the past means the renewal "
                 "fails (CertManagerCertificateRenewalOverdue).",
            sort=[{"displayName": "Time left", "desc": False}]), 16, 7)
d.add(table("Issuers",
            [instant('label_replace(max by (namespace, name) (certmanager_issuer_ready_status{condition="True"}), '
                     '"kind", "Issuer", "", "") or label_replace(max by (name) (certmanager_clusterissuer_ready_status'
                     '{condition="True"}), "kind", "ClusterIssuer", "", "")', "A")],
            merged([("kind", "Kind"), ("namespace", "Namespace"), ("name", "Issuer"), ("Value", "Ready")]),
            overrides=[mapped("Ready", 110, READY, "color-background")],
            desc="Issuers and ClusterIssuers; a certificate can only be issued or renewed by a ready issuer."), 8, 7)
d.add(per_interval(ts("Controller syncs",
                      [vm("sum by (controller) (round(increase(certmanager_controller_sync_call_count[$__interval]))) > 0",
                          "{{controller}}"),
                       vm("sum by (controller) (round(increase(certmanager_controller_sync_error_count[$__interval]))) > 0",
                          "{{controller}} errors", "B")],
                      "short", stack=True, bars=True, decimals=0, legend_calcs=("sum",),
                      overrides=[fixed(".* errors", "red", "byRegexp")],
                      desc="Syncs of the cert-manager controllers per 5 minutes (certificates, requests, issuers) and "
                           "their errors.")), 12, 7)
d.add(ts("Webhook requests", [vm(f'sum by (code) (rate(controller_runtime_webhook_requests_total{{job="cert-manager-webhook"}}{RI}))',
                                 "{{code}}")],
         "reqps", stack=True, overrides=code_colors(),
         desc="Validation and defaulting of cert-manager objects on create and update. Errors here make Argo CD "
              "syncs of Certificates and Issuers fail."), 12, 7)

d.row("Components")
d.add(status_history("Scrape targets up",
                     vm(f'label_replace(min by (job) (up{{{JOBS}}}), "job", "$1", "job", "(.+)-metrics")', "{{job}}"),
                     "Every metrics endpoint of Argo CD, External Secrets and cert-manager; Down means the component "
                     "is restarting or not reachable.", thresholds(("red", None), ("green", 1))), 8, 7)
d.panels[-1]["fieldConfig"]["defaults"]["mappings"] = mappings({"1": ("Up", "green"), "0": ("Down", "red")})
d.add(ts("CPU", [vm(f'sum by (pod) (rate(container_cpu_usage_seconds_total{{{CADVISOR}, {NS_RE}, container!=""}}{RI}))', "{{pod}}")],
         "cores", min_=0, desc="CPU of every pod in argocd, external-secrets and cert-manager."), 8, 7)
d.add(ts("Memory", [vm(f'sum by (pod) (container_memory_working_set_bytes{{{CADVISOR}, {NS_RE}, container!=""}})', "{{pod}}")],
         "bytes", min_=0, desc="Working set memory of every pod (what counts against the memory limit)."), 8, 7)

d.row("Errors in the logs")
d.add(ts("Error lines by container",
         [vl(f"{LOGS} {ERRORS} {SEARCH} | stats by (kubernetes.container_name) count() lines", "statsRange",
             legend="{{kubernetes.container_name}}")],
         "short", ds=VL, stack=True, bars=True, decimals=0, legend_calcs=("sum",),
         desc="Error lines of the Argo CD, External Secrets and cert-manager containers."), 8, 10)
d.panels[-1]["interval"] = "5m"
d.add(logs_panel("Error lines", f'{LOGS} {ERRORS} {SEARCH} '
                 '| format "<kubernetes.pod_namespace>/<kubernetes.container_name>: <_msg>" as _msg',
                 "Error lines as <namespace>/<container>: <message>, newest first. Argo CD names the application "
                 "(application=<name>); narrow with Message contains."), 16, 10)

PLATFORM_LINKS = [
    {"title": "Cluster — Events", "type": "link", "url": "/d/cluster-events/cluster-events", "keepTime": True},
    {"title": "Explore logs", "type": "link", "url": "/explore?left={\"datasource\":\"VictoriaLogs\"}", "targetBlank": True},
]
SYNC_ANNOTATIONS = [
    {"name": name, "datasource": VM, "enable": True, "iconColor": color, "step": "1m", "useValueForTime": False,
     "expr": f'sum by (name, phase) (round(increase(argocd_app_sync_total{{dry_run="false", phase=~"{phases}"}}[1m]))) > 0',
     "titleFormat": "Sync {{name}}: {{phase}}", "textFormat": text}
    for name, phases, color, text in (
        ("Argo CD syncs", "Succeeded", "blue", "Applied a Git change or reverted drift"),
        ("Failed syncs", "Error|Failed", "red", "The sync did not complete; see the Applications table"))]
platform = dashboard(UID, "Platform — GitOps & Secrets", ["platform", "argocd", "external-secrets", "cert-manager"],
                     d.panels, [SEARCH_VAR],
                     "Argo CD applications (sync, health, failed syncs, reconciliation, Git), External Secrets "
                     "(ExternalSecrets, stores, webhook) and cert-manager (certificates, renewals, issuers), with the "
                     "components' resources and error logs.", "now-6h")
platform["links"] = PLATFORM_LINKS
platform["annotations"] = {"list": SYNC_ANNOTATIONS + restart_annotations(NS_RE)}
DASHBOARD = platform
