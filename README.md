# k8s-victoria-observability

Platform repository for a GKE cluster: infrastructure with **Terraform**, everything inside the cluster with
**Argo CD** (ApplicationSets), and full-stack observability with **OpenTelemetry**, VictoriaMetrics,
VictoriaLogs, VictoriaTraces and Grafana.

Application workloads live in a separate repository,
[microservice-deus](https://github.com/alice101-dev/microservice-deus): it holds the code and the deploy values
of each service, while this repository owns the Argo CD objects that deploy them (project, destination, sync
policy) and the shared platform they run on.

```text
k8s-victoria-observability/
├── terraform/      # GCP: VPC, Cloud NAT, firewall, GKE, Artifact Registry, Cloud Storage, Secret Manager, Argo CD bootstrap
├── bootstrap/      # root.yaml: the only hand-written Application, applied by Terraform
├── appsets/        # AppProjects platform and apps, ApplicationSets addons and configs
├── addons/         # one folder per Helm chart: config.yaml (chart, repo, version, namespace) + values.yaml + values-<env>.yaml
├── configs/        # one folder per component: custom resources and monitoring (Kustomize base + <env> overlay)
├── tools/          # dashboard generator
├── ci/             # render and validation scripts used by CI
└── docs/           # observability, runbooks
```

## Delivery

```mermaid
flowchart LR
  eng(["Engineer"])
  tf["Terraform"]
  repo["platform repo"]
  apprepo["microservice-deus repo"]

  subgraph gke["GKE · asia-southeast2"]
    root["Application root"]
    projects["AppProjects<br/>platform · apps"]
    addons["ApplicationSet addons<br/>one Application per addons/*"]
    configs["ApplicationSet configs<br/>one Application per configs/*"]
    workloads["operators, observability,<br/>shared configuration"]
  end

  eng --> tf
  eng -- "pull request" --> repo
  tf -- "Argo CD, cluster secret, root" --> root
  root --> projects & addons & configs
  repo -. "desired state" .-> addons & configs
  addons & configs --> workloads
  apprepo -. "services/*/deploy" .-> gke
```

- **Root app**: `bootstrap/root.yaml` points at `appsets/`. Terraform renders it with the repository URL and
  revision and applies it once; Git is the only way to change the cluster afterwards.
- **ApplicationSets**: a matrix of a Git files generator and the cluster generator. `addons` creates one
  Application per `addons/*/config.yaml` (multi-source: the upstream chart plus `values.yaml` and
  `values-<env>.yaml` from this repository); `configs` creates one per `configs/*/config.yaml` from
  `configs/<name>/<env>`.
- **Components per environment**: the cluster generator only selects clusters labelled `enable_<name>: "true"`,
  where `<name>` is the addon folder, or the `requires` field of a config. Terraform sets these labels from
  `enabled_components` in the environment's `argocd-bootstrap` tfvars, so an addon is switched on or off per
  environment in one reviewed list. Removing a name deletes its Application and the resources it manages.
- **Promotion**: each environment follows one Git revision for everything (`gitops_target_revision` in its
  tfvars). Terraform renders it into the root Application, whose Kustomize patches write the same revision into
  both ApplicationSets (Git generator and sources), so the addon list, chart versions, values and configs all come
  from that revision. Staging follows `main`; a production environment would follow a release tag such as
  `platform-v1.4.0`, moved forward once the change has run in staging.
- **Environments**: Terraform registers the cluster in Argo CD with the label `env=<env>` and annotations
  `platform/project-id`, `platform/image-registry` and `platform/backup-bucket`. The ApplicationSets read the
  environment from the label and inject the annotations where a value contains the project ID, so no project ID
  is stored in Git. The default environment is `staging`; another one needs its own Terraform environment,
  `values-<env>.yaml` files and `configs/*/<env>` overlays.
- **Ordering**: operators and their custom resources are separate Applications. Every Application syncs with
  `SkipDryRunOnMissingResource` and retries with backoff, so custom resources apply as soon as their CRDs exist.
- **Projects**: `platform` may deploy cluster-scoped resources from this repository and the pinned chart
  repositories. `apps` only deploys namespaced workload kinds from the application repository into `deus`.
- **Versions**: every chart, image, dashboard and rule URL is pinned. Renovate opens pull requests for the charts
  in `addons/*/config.yaml`, Terraform providers and GitHub Actions.

## Runtime

```mermaid
flowchart LR
  subgraph deus["ns deus"]
    svc["services<br/>OpenTelemetry SDK"]
    db[("PostgreSQL 18<br/>CloudNativePG")]
    svc --> db
  end
  subgraph mon["ns monitoring"]
    otel["OTel Collector ×2"]
    vm[("VMSingle 30d")]
    vl[("VLSingle 90d")]
    vt[("VTSingle 30d")]
    vma["VMAgent ×2"]
    vla["VLAgent"]
    alert["VMAlert ×2 → Alertmanager ×2"]
    grafana["Grafana"]
    mcp["MCP servers"]
  end
  svc -- "OTLP metrics · logs · traces" --> otel
  otel --> vm & vl & vt
  vma -- "platform /metrics" --> vm
  vla -- "container output" --> vl
  vm & vl --> alert
  vm & vl & vt --> grafana & mcp
```

| Signal | Path | Store | Query |
|---|---|---|---|
| Application metrics | OTLP → collector → `/opentelemetry/v1/metrics` | VMSingle | MetricsQL |
| Application logs | OTLP → collector (redaction, severity filter) → `/insert/opentelemetry/v1/logs` | VLSingle | LogsQL |
| Application traces | OTLP → collector (redaction) → `/insert/opentelemetry/v1/traces` | VTSingle | Jaeger API, LogsQL |
| Platform metrics | VMAgent scrapes VMServiceScrape / VMPodScrape targets | VMSingle | MetricsQL |
| Container output | VLAgent on every node | VLSingle | LogsQL |
| Kubernetes events | OpenTelemetry Collector `k8s_events` receiver | VLSingle | LogsQL |
| Alerts | VMAlert evaluates MetricsQL and LogsQL rules | – | – |

Nothing is exposed publicly yet. Application traffic enters through the Gateway `edge` (kgateway), a ClusterIP
Service for now; Grafana, Argo CD, the MCP servers and the gateway itself are reached with `kubectl port-forward`
through the IAM-gated GKE DNS endpoint. The gateway's spans and access logs go to the collector over OTLP, so a
trace starts at the edge and the access log of every request carries its `trace_id`. Details: [docs/observability.md](docs/observability.md).

## Addons

| Addon | Namespace | Purpose |
|---|---|---|
| `cert-manager` | cert-manager | Certificates (Barman Cloud plugin mTLS) |
| `external-secrets` | external-secrets | Generated and Secret Manager secrets |
| `cloudnative-pg`, `plugin-barman-cloud` | cnpg-system | PostgreSQL operator, backups and WAL archiving to Cloud Storage |
| `victoria-metrics-operator` | vm-operator | Operator and CRDs |
| `victoria-metrics-k8s-stack` | monitoring | Metrics, logs and traces storage, agents, alerting, exporters, vendor dashboards and rules |
| `grafana` | monitoring | Dashboards and exploration |
| `otel-collector` | monitoring | OTLP gateway for the applications |
| `kgateway-crds`, `kgateway` | kgateway-system | Gateway API implementation (Envoy) for the internal edge Gateway |
| `k8s-events` | monitoring | Kubernetes events to VictoriaLogs |
| `mcp-victoriametrics`, `mcp-victorialogs`, `mcp-victoriatraces`, `mcp-victoriatraces-logsql`, `mcp-grafana` | monitoring | Read-only MCP servers (plus `mcp-argocd` and `postgres-mcp` from `configs/`) |

Configs: `namespaces`, `gateway-api` (Gateway API standard CRDs), `kgateway` (Gateway `edge`, telemetry, alerts), `argocd`, `cert-manager`, `external-secrets`, `cloudnative-pg` (cluster `deus-db`,
backups, network policy, alerts), `postgres-mcp`, `grafana`, `victoria-metrics-k8s-stack`, `otel-collector`,
`k8s-events`, `mcp-grafana`, `mcp-argocd` (read-only Argo CD MCP server and its token issuer).

## Getting started

Prerequisites: `gcloud`, Terraform 1.15 (`tfenv`), `kubectl`, `make`.

```bash
cp .env.example .env
gcloud config set project <PROJECT_ID>
gcloud auth application-default login

make -C terraform init
make -C terraform apply-all

gcloud container clusters get-credentials staging-observability \
  --location asia-southeast2-a --project "$(gcloud config get-value project)" --dns-endpoint

kubectl -n argocd get applicationsets,applications
```

Set `PROJECT_ID` in `.env`. The repository URL is taken from `git remote origin` unless `GITOPS_REPO_URL` is set;
for a private fork export `TF_VAR_gitops_repo_password=<read-only token>`.

```bash
kubectl -n monitoring port-forward svc/grafana 3000:80
kubectl -n monitoring get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo
kubectl -n argocd port-forward svc/argocd-server 8080:80
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
```

## Adding a component

- **Helm chart**: create `addons/<name>/config.yaml` (`chart`, `repoURL`, `version`, `namespace`, optional
  `ignoreDifferences`), `values.yaml` and `values-staging.yaml` if the environment differs; add the chart
  repository to the `platform` AppProject.
- **Custom resources or monitoring**: create `configs/<name>/config.yaml` (`requires`, `namespace`, optional
  `ignoreDifferences`), `configs/<name>/base/kustomization.yaml` and `configs/<name>/staging/kustomization.yaml`.
- Add the name to `enabled_components` and apply the `argocd-bootstrap` workspace **before** merging, so the label
  exists when the ApplicationSet discovers the folder.

## Exposing a service

Applications publish routes from their own repository: an `HTTPRoute` in `deus` with
`parentRefs: [{name: edge, namespace: kgateway-system}]`. The Gateway only accepts routes from namespaces labelled
`platform/gateway: edge` (set in `configs/namespaces`), and the `apps` AppProject allows `HTTPRoute`.

```bash
kubectl -n kgateway-system port-forward svc/edge 8080:80
curl http://localhost:8080/<path>
```

## Teardown

```bash
make -C terraform teardown
```

It deletes the root Application and the ApplicationSets without cascading, then the generated Applications
layer by layer (configs first, then addons in reverse dependency order), waits until every PVC is gone so no
persistent disk keeps billing, and destroys the Terraform workspaces. `services` and `secret-manager` are kept.

## Staging defaults vs. production

| Staging | Production |
|---|---|
| One VMSingle, VLSingle and VTSingle (no query HA) | Bigger disks first; for HA two independent copies or cluster mode |
| Small disks (30Gi, 20Gi, 20Gi) | Size for the retention in `values-<env>.yaml` and expand online |
| All nodes Spot, zonal cluster | On-demand pool for stateful workloads, regional cluster |
| DEBUG logs kept by the collector | Base values drop records below INFO |
| No trace sampling | Head sampling in the SDKs or tail sampling in the collector |
| Alertmanager `blackhole` receiver | Slack or PagerDuty receiver with credentials from Secret Manager |
| PostgreSQL primary + 1 synchronous standby, 7-day point-in-time recovery | `instances: 3` across zones, backups in a separate project |
| Single Grafana replica with SQLite | External PostgreSQL for Grafana and 2+ replicas |

## Secrets and configuration

- No secrets or project IDs are committed. `PROJECT_ID` lives in the git-ignored `.env`; values that contain it
  are computed by Terraform and stored on the Argo CD cluster secret.
- Terraform state is local per workspace and git-ignored. `make check-project` refuses to run when the active
  `gcloud` project differs from `PROJECT_ID`.

## Dashboards and CI

The platform dashboards (Cluster — Events, Platform — GitOps & Secrets, Observability — Pipeline health) are
generated by `tools/dashboards/generate.py`; the CloudNativePG dashboard is the upstream one with scripted fixes
(`tools/dashboards/vendor_cloudnative_pg.py`). Both write into `configs/grafana/base/dashboards`. Edit the
generator, never the JSON.

`.github/workflows/ci.yaml`:

| Job | Checks |
|---|---|
| Dashboards | Committed JSON equals the generator output; datasources, variables, links and panel ids are valid |
| Argo CD applications | Generates the Applications from the ApplicationSets like the controller does, renders each one with the Helm and Kustomize versions Argo CD bundles, validates built-in objects with kubeconform and custom resources (Argo CD objects included) against their CRDs |
| Terraform | `fmt -check` and `validate` of every workspace |

Locally: `python3 tools/dashboards/generate.py`, `python3 ci/validate_dashboards.py` and, with helm, kustomize,
kubeconform and `pip install -r ci/requirements.txt`, `python3 ci/render_manifests.py`.

## License

[MIT](LICENSE)
