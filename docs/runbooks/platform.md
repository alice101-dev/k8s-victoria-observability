# Platform runbook (Argo CD, External Secrets, cert-manager)

The alerts of the `platform` VMRule (`observability/platform/monitoring.yaml`) link to the sections below. The
same directory holds the scrape configs of the three components; the **Platform — GitOps & Secrets** dashboard
(Grafana folder **Platform**) shows them: applications with their sync and health status, syncs, reconciliation
and Git, ExternalSecrets and stores, certificates and issuers, the components' resources and their error logs.

| Component | Installed by | Metrics (job = Service) |
|---|---|---|
| Argo CD | Terraform (`terraform/modules/argocd-bootstrap`), namespace `argocd` | `argocd-application-controller-metrics` (8082), `argocd-server-metrics` (8083), `argocd-repo-server-metrics` (8084) |
| External Secrets | Argo CD app `external-secrets`, namespace `external-secrets` | `external-secrets-metrics`, `external-secrets-webhook`, `external-secrets-cert-controller-metrics` (8080) |
| cert-manager | Argo CD app `cert-manager`, namespace `cert-manager` | `cert-manager`, `cert-manager-cainjector`, `cert-manager-webhook` (9402) |

```text
# MetricsQL: applications that are not Synced and Healthy
argocd_app_info{sync_status!="Synced"} or argocd_app_info{health_status!="Healthy"}

# MetricsQL: time left per certificate, in days
(certmanager_certificate_expiration_timestamp_seconds - time()) / 86400

# LogsQL: errors of the three components (Argo CD logfmt, cert-manager klog, External Secrets JSON)
_time:1h {kubernetes.pod_namespace=~"argocd|external-secrets|cert-manager"}
  (level:in(error, fatal, panic) or _msg:~"level=(error|fatal)" or _msg:~"^[EF][0-9]{4} ")
```

Argo CD's UI and CLI: `kubectl -n argocd port-forward svc/argocd-server 8080:80` (see the main README).

## ArgoCdAppSyncFailed

**Meaning.** A sync operation of the application ended `Failed` or `Error` after its retries (10, with backoff up
to 5 minutes). The alert covers the 10 minutes after the failure; the application stays OutOfSync, which
`ArgoCdAppOutOfSync` keeps reporting.

**Diagnose.** `kubectl -n argocd get application <name> -o jsonpath='{.status.operationState.message}'` and
`.status.operationState.syncResult.resources` name the resource and the reason, for example an admission webhook
denial, a missing CRD, an immutable field, or a namespace outside the AppProject. Argo CD logs the same:
click the application in the dashboard (Errors in the logs row) or LogsQL `_msg:"application=<name>"`.

**Fix.** Fix the manifest in Git (the next commit is synced automatically) or, for an immutable field, delete the
resource and let Argo CD recreate it. `argocd app sync <name>` retries at once.

## ArgoCdAppOutOfSync

**Meaning.** For 15 minutes the live state differs from Git (`OutOfSync`) or Argo CD cannot compare it
(`Unknown`). Automated sync with self-heal normally converges within seconds, so either syncs fail, or a field
is rewritten after every sync, or the manifests cannot be generated.

**Diagnose.** `argocd app diff <name>` (or the Diff tab in the UI) shows the fields. `Unknown`:
`.status.conditions` holds a `ComparisonError` (Helm or Kustomize error, unreachable chart repository, invalid
`$values` file). A diff that comes back after every sync is a field set by a controller or mutating webhook
(defaults, injected CA bundles, replicas set by an autoscaler).

**Fix.** Manifest errors: fix them in Git. A controller-owned field: add it to the application's
`ignoreDifferences` (as `k8s-events` does for `restartedAt`) together with `RespectIgnoreDifferences=true`.

## ArgoCdAppUnhealthy

**Meaning.** For 15 minutes at least one resource of the application is `Degraded` (a Deployment past its
progress deadline, a crash-looping pod, a failed Job), `Missing` (not created yet, often because the sync fails)
or of `Unknown` health.

**Diagnose.** `kubectl -n argocd get application <name> -o json | jq '.status.resources[] | select(.health.status
!= "Healthy")'` names the resource; then `kubectl describe` it and its pods. The Cluster — Events dashboard shows
why pods fail (scheduling, probes, OOM kills, image pulls).

**Fix.** Depends on the resource; a bad rollout is fixed by reverting the commit in Git.

## ArgoCdAppAutoSyncDisabled

**Meaning.** Severity `info` (not sent to Slack). Every Application of this repository has automated sync with
prune and self-heal; this one had it switched off for 2 hours, usually for a manual hotfix.

**Fix.** Put the hotfix in Git, then re-enable it: the child Applications are rendered by the root
Applications, so syncing the root (`argocd app sync observability` or `apps`) restores the policy.

## ArgoCdGitRequestsFailing

**Meaning.** The repo-server's `git fetch` or `git ls-remote` keeps failing for 15 minutes. Nothing new is
deployed; running applications are not affected. The failure counters name the repo-server's local checkout path
instead of the repository URL, so the alert does not say which repository.

**Diagnose.** The applications reading the repository are `Unknown` with a `ComparisonError` such as `failed to
list refs` (Applications table, `ArgoCdAppOutOfSync`); `kubectl -n argocd get applications` and the repo-server
logs (`kubectl -n argocd logs deploy/argocd-repo-server`) name the URL. Git requests panel of the dashboard. Typical causes: GitHub outage or rate limit, egress (Cloud NAT) broken, the repository renamed or
made private, the target revision deleted.

**Fix.** Restore access; Argo CD retries on its own. A moved repository: update the root Application in
Terraform (`argocd-bootstrap`).

## ArgoCdControllerDown

**Meaning.** No metrics from `argocd-application-controller` for 10 minutes: the StatefulSet pod is down or not
scraped. Nothing is synced or self-healed and the other Argo CD alerts cannot fire.

**Diagnose.** `kubectl -n argocd get pods`, `kubectl -n argocd describe pod argocd-application-controller-0`
(OOMKilled: its limit is 1 GiB). If the pod runs: `kubectl -n argocd get svc argocd-application-controller-metrics`
and the `argocd` VMServiceScrape in `monitoring`.

**Fix.** Argo CD is installed by Terraform: resources and metrics settings are in
`terraform/modules/argocd-bootstrap/main.tf` (apply the `argocd-bootstrap` workspace).

## ExternalSecretNotSynced

**Meaning.** The ExternalSecret's `Ready` condition is not `True` for 15 minutes: its last refresh failed. The
Secret keeps its previous data, so running workloads are unaffected until the data changes; if the Secret was
never created, pods that mount it do not start.

**Diagnose.** `kubectl -n <namespace> describe externalsecret <name>` (condition message and events). Generators
(`Password`): the generator object exists in the same namespace. Secret Manager: the secret and version exist in
project `PROJECT_ID`, and the store is ready (`SecretStoreNotReady`). Controller logs:
`kubectl -n external-secrets logs deploy/external-secrets`.

**Fix.** Create the missing source (a Secret Manager secret version: `gcloud secrets versions add <secret>
--project <project> --data-file=-`, value from stdin, never in Git), or fix the ExternalSecret in Git. Force a
refresh:
`kubectl -n <namespace> annotate externalsecret <name> force-sync=$(date +%s) --overwrite`.

## SecretStoreNotReady

**Meaning.** A SecretStore or ClusterSecretStore cannot authenticate to its provider for 10 minutes; every
ExternalSecret using it stops syncing.

**Diagnose.** `kubectl -n <namespace> describe secretstore <name>`. GCP Secret Manager: External Secrets
authenticates as its own Kubernetes ServiceAccount (`external-secrets/external-secrets`) through Workload Identity
Federation for GKE, without a Google service account. That principal needs `roles/secretmanager.secretAccessor`
on each secret (Terraform workspace `secret-manager`, `workload_identity` in `staging.tfvars`), and the store's
`projectID` must be the cluster's project (injected by Terraform through the root Application).

**Fix.** Fix the grant (Terraform `secret-manager` workspace) or the store in Git.

## ExternalSecretsControllerDown

**Meaning.** No metrics from the External Secrets controller for 10 minutes: down or not scraped. Secrets are not
created or refreshed and the External Secrets alerts cannot fire.

**Diagnose.** `kubectl -n external-secrets get pods,svc` (the `external-secrets-metrics` Service is enabled in
`observability/values/external-secrets.yaml`), the `external-secrets` VMServiceScrape in `monitoring`.

## CertManagerCertificateNotReady

**Meaning.** The Certificate's `Ready` condition is not `True` for 15 minutes: cert-manager could not issue or
renew it. Today the certificates are the mTLS pair of the CloudNativePG Barman Cloud plugin
(`cnpg-system/barman-cloud-client`, `barman-cloud-server`, self-signed issuer).

**Diagnose.** `kubectl -n <namespace> describe certificate <name>`, then the newest CertificateRequest
(`kubectl -n <namespace> get certificaterequest`) and its issuer (`CertManagerIssuerNotReady`).
Controller logs: `kubectl -n cert-manager logs deploy/cert-manager`.

**Fix.** Fix the issuer or the Certificate spec; `cmctl renew -n <namespace> <name>` retries at once.

## CertManagerCertificateRenewalOverdue

**Meaning.** The certificate's renewal time (`renewBefore` ahead of expiry) passed more than an hour ago and it
was not renewed. A certificate that was never issued has no renewal time and shows as
`CertManagerCertificateNotReady` instead. It keeps working until it expires; `CertManagerCertificateExpiringSoon` turns critical 7 days
before.

**Diagnose and fix.** As for `CertManagerCertificateNotReady`.

## CertManagerCertificateExpiringSoon

**Meaning.** Critical: the renewal is overdue and the certificate expires within 7 days. When it expires, TLS to
the service fails; for the Barman Cloud plugin, CloudNativePG can no longer archive WAL or take backups.

**Fix.** As for `CertManagerCertificateNotReady`; if the issuer cannot be fixed in time, delete the certificate's
Secret so cert-manager issues a new one from scratch, and restart the workloads that load it at startup.

## CertManagerIssuerNotReady

**Meaning.** An Issuer or ClusterIssuer is not ready for 15 minutes; its certificates can be neither issued nor
renewed.

**Diagnose.** `kubectl -n <namespace> describe issuer <name>` (or `clusterissuer`): CA issuer without its CA
Secret, ACME account registration failing, Vault unreachable.

## CertManagerDown

**Meaning.** No metrics from the cert-manager controller for 10 minutes: down or not scraped. Nothing is issued or
renewed and the cert-manager alerts cannot fire.

**Diagnose.** `kubectl -n cert-manager get pods,svc`, the `cert-manager` VMServiceScrape in `monitoring`.
