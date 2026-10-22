# terraform — VPC, Cloud NAT, firewall, GKE, Artifact Registry, Cloud Storage, Argo CD bootstrap

Small reusable **modules** in `modules/` and one **workspace per service** in `environments/staging/`,
each with its own state to keep the blast radius small. Workspaces reference each other **by name**
(data sources), not through `terraform_remote_state`.

```text
terraform/
├── .terraform-version          # 1.15.4 (tfenv)
├── Makefile                    # init / validate / plan / apply / apply-all / teardown
├── scripts/teardown.sh         # ordered workload deletion (PVCs/disks included), then destroy
├── modules/
│   ├── services/  vpc/  cloud-nat/  firewall/  artifact-registry/  cloud-storage/  secret-manager/
│   ├── kubernetes-engine/      # GKE Standard, private nodes, Dataplane V2, Workload Identity, Spot-only pools
│   └── argocd-bootstrap/       # helm_release argo-cd + argocd-apps (AppProjects + root applications)
└── environments/staging/
    ├── services/               # 1. enable Google APIs (GKE, Compute, Artifact Registry, IAM, ...)
    ├── vpc/                    # 2. staging-vpc-observability + subnet 10.120.0.0/20
    ├── cloud-nat/              # 3. Cloud NAT (auto-allocated IPs) — private nodes need egress
    ├── firewall/               # 4. SSH through IAP only
    ├── kubernetes-engine/      # 5. staging-observability (zonal) + Spot e2-standard-2 pool (2–4 nodes)
    ├── artifact-registry/      # 6. staging-shop (Docker, immutable tags, cleanup policies)
    ├── cloud-storage/          # 7. <project>-staging-pg-backup (PostgreSQL backups, Workload Identity access)
    ├── secret-manager/         # 8. staging-alertmanager-slack-webhook (value added with gcloud, read by External Secrets)
    └── argocd-bootstrap/       # 9. Argo CD + root applications → everything else is GitOps
```

Providers: `hashicorp/google ~> 7.35.0`, `hashicorp/helm ~> 3.1.0`.

## Enabled APIs

The `services` workspace (applied first) enables every Google API the stack needs, defined in
[`environments/staging/services/staging.tfvars`](environments/staging/services/staging.tfvars):

| API | Used by |
|---|---|
| `container.googleapis.com` | GKE |
| `compute.googleapis.com` | VPC, Cloud NAT, firewall, node VMs |
| `artifactregistry.googleapis.com` | Application images |
| `containerfilesystem.googleapis.com` | GKE image streaming |
| `containersecurity.googleapis.com` | GKE security posture |
| `iam.googleapis.com`, `iamcredentials.googleapis.com` | Node service account, Workload Identity |
| `logging.googleapis.com`, `monitoring.googleapis.com` | GKE system component logs/metrics |
| `cloudresourcemanager.googleapis.com`, `serviceusage.googleapis.com` | Terraform project and API management |
| `storage.googleapis.com` | PostgreSQL backup bucket |
| `secretmanager.googleapis.com` | Alertmanager Slack webhook |

`disable_on_destroy = false`: destroying the workspace never turns APIs off.

## Design

- **Local state** per workspace (`terraform.tfstate`, git-ignored); no deletion guards, so the
  environment can be destroyed at any time.
- **Zonal GKE cluster** in `asia-southeast2-a`: the control plane is covered by the GKE free tier.
- **Spot-only node pools**, enforced by a variable validation.
- Private nodes; `kubectl` and the Terraform Helm provider use the **DNS-based control plane
  endpoint** (IAM-gated), while the IP endpoint is restricted to the VPC.
- Cloud Logging and Cloud Monitoring receive **system components only**, and Managed Prometheus is
  disabled — workload telemetry goes to the Victoria stack.
- **No project ID in the repository**: `project_id` comes from `TF_VAR_project_id`, which the Makefile
  reads from the git-ignored `../.env`. Names that contain it (image registry, backup bucket) are built
  at apply time and injected into the root Applications by `argocd-bootstrap`.
- **Workload Identity without Google service accounts**: the backup bucket grants its roles directly to
  the Kubernetes ServiceAccount `shop/shop-db` (`principal://…/subject/ns/shop/sa/shop-db`), so there is
  no key and no `iam.gke.io/gcp-service-account` annotation.

## Prerequisites

```bash
cp ../.env.example ../.env                # set PROJECT_ID
gcloud config set project <PROJECT_ID>    # must match PROJECT_ID in .env
gcloud auth application-default login     # credentials for the Terraform providers
tfenv install && tfenv use                # Terraform 1.15.4
```

`make check-project` runs before every plan/apply/teardown and fails if the active `gcloud` project
differs from `PROJECT_ID` or if any tfvars file contains a `project_id`. To run `terraform` directly,
export `TF_VAR_project_id` yourself.

## Apply

1. Push this repository to GitHub. The GitOps repository URL is taken from `git remote get-url origin`
   (or `GITOPS_REPO_URL` in `.env`). For a private repository, also
   `export TF_VAR_gitops_repo_password=<read-only token>`.
2. Apply all workspaces in dependency order:

```bash
make init
make apply-all    # services → vpc → cloud-nat → firewall → kubernetes-engine → artifact-registry → cloud-storage → secret-manager → argocd-bootstrap
# or one workspace at a time: make plan WS=vpc && make apply WS=vpc
```

3. Get cluster credentials:

```bash
gcloud container clusters get-credentials staging-observability \
  --location asia-southeast2-a --project "$(gcloud config get-value project)" --dns-endpoint
```

4. Build and push the application images — see [`apps/README.md`](../apps/README.md).

## Teardown

```bash
make teardown     # prompts before each destroy; AUTO_APPROVE=1 to skip prompts
```

The script deletes the Argo CD applications in reverse sync-wave order (workloads → data → operators),
waits until every PVC is gone, then runs `terraform destroy` for argocd-bootstrap → artifact-registry
→ kubernetes-engine → firewall → cloud-nat → vpc, and finally lists any leftover `pvc-*` disks or IPs.

Destroying the cluster directly would leave the persistent disks created for PVCs behind; they are
not managed by Terraform and keep billing. The `services` and `secret-manager` workspaces are intentionally
left in place: the secret values were added by hand and cost a few cents a month.

## Estimated cost (Jakarta, approximate)

| Item | Per month |
|---|---|
| GKE zonal management fee | ~$0 (free tier) |
| 2–3 × e2-standard-2 Spot | ~$25–40 |
| 9 × 10Gi pd-balanced (VM/VL/VT storage, PostgreSQL, Redis) | ~$10 |
| Cloud NAT | ~$1–5 |
| Artifact Registry (<1GB) | <$1 |
