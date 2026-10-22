# apps — 6 Go microservices + Redis + PostgreSQL 18

A small e-commerce system ("shop") whose services call each other, so a single request produces a
trace across services, logs correlated by `trace_id`, and RED metrics per service.

```text
traffic-generator ──► api-gateway ──► /api/orders ──► order-service ──► user-service ──► Redis (cache) / PostgreSQL
                         │                                  ├──► product-service (price, reserve/release stock) ──► Redis / PostgreSQL
                         │                                  ├──► payment-service (random latency & declines, idempotency) ──► Redis / PostgreSQL
                         │                                  └──► notification-service ──► Redis Stream ──► async worker
                         ├──► /api/users/*    ──► user-service
                         └──► /api/products/* ──► product-service
```

| Service | Data | What it demonstrates |
|---|---|---|
| `api-gateway` | – | Root span, reverse proxy, upstream errors (502) |
| `user-service` | PostgreSQL + Redis | Cache hit/miss visible as Redis vs SQL spans, 404 for unknown users |
| `product-service` | PostgreSQL + Redis | Atomic `UPDATE ... RETURNING` reservations, compensation, background `restock` job |
| `order-service` | PostgreSQL | 5-step checkout saga with compensation when payment is declined |
| `payment-service` | PostgreSQL + Redis | Random latency (≤400ms), 10% declines, idempotency keys |
| `notification-service` | Redis Stream | `traceparent` propagated through an async producer → consumer boundary |

## Instrumentation (`src/internal/platform`)

| Signal | Implementation |
|---|---|
| Traces | OpenTelemetry SDK → OTLP/HTTP → VictoriaTraces; `otelhttp` (server & client), `otelpgx` (every SQL query), `redisotel` (every Redis command) |
| Metrics | `client_golang`: `http_server_requests_total`, `http_server_request_duration_seconds` (labelled by route pattern to keep cardinality low), `http_client_*` per peer, plus business metrics (`orders_total`, `payments_total`, …) |
| Logs | `slog` JSON to stdout with `trace_id`, `span_id` and `service` added from the request context |

All OpenTelemetry settings use the standard environment variables (`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`,
`OTEL_TRACES_SAMPLER`, `OTEL_RESOURCE_ATTRIBUTES` with pod/node/namespace from the Downward API).

Production behaviour built into the platform package: graceful shutdown (readiness → 503, endpoint
propagation delay, request draining), server and client timeouts, startup retries with backoff while
PostgreSQL/Redis are not ready, idempotent migrations under `pg_advisory_lock` (safe with multiple
replicas), and `GOMEMLIMIT` derived from the container memory limit.

## Kubernetes (`k8s/`)

```text
k8s/
├── data/          # Kustomize — Argo CD app "shop-data" (wave -1)
│   ├── postgres.yaml       # CloudNativePG Cluster "shop-db", PostgreSQL 18.6, primary + sync standby, 10Gi each
│   ├── redis.yaml          # Redis 8 + Sentinel (3 pods, AOF) + redis_exporter + password ExternalSecret
│   ├── networkpolicy.yaml  # only shop pods (+ operator, + metrics scraper) can reach PostgreSQL/Redis
│   ├── monitoring.yaml     # VMServiceScrape (Redis), VMPodScrape (CloudNativePG instances + operator), data-tier VMRule
│   ├── postgres-monitoring.yaml  # custom exporter queries: top statements, table health, blocking sessions
│   └── dashboards/         # CloudNativePG dashboard, vendored from upstream with fixes (vendor-cloudnative-pg.py)
└── services/      # Helm chart — Argo CD app "shop-services" (wave 0)
    ├── values.yaml         # image tag, OpenTelemetry, resources, per-service env
    ├── templates/          # Deployment + Service + ServiceAccount + PDB per service, scrape, rules, traffic generator
    └── dashboards/         # Grafana dashboards: SLO Overview, Service, Logs, Microservices Overview (generate.py)
```

- Each service requests `10m CPU / 32Mi` with a `128Mi` limit and runs 2 replicas spread across nodes
  with a PodDisruptionBudget, so losing one Spot node does not take a service down.
- PostgreSQL runs a primary and a standby on different nodes with quorum synchronous replication
  (`dataDurability: preferred`); CloudNativePG fails over automatically and applications keep using
  the `shop-db-rw` service.
- Backups use the CloudNativePG **Barman Cloud plugin** (`k8s/data/backup.yaml`): daily base backups at
  02:00 UTC plus continuous WAL archiving to `gs://<project>-staging-pg-backup`, kept 7 days
  (point-in-time recovery). The bucket name is injected by Argo CD from Terraform, and access goes
  through Workload Identity for the `shop-db` ServiceAccount. The plugin needs cert-manager; both run
  as Argo CD apps (waves -4 and -2).
- Redis runs 3 pods (one per node), each with redis + Sentinel. Sentinel promotes a replica about 5s
  after the master is lost, and services discover the master through `redis-sentinel:26379`
  (go-redis `FailoverClient`). A master that is shut down hands over to a replica first (preStop).
- Application sessions run with `lock_timeout` 2s, `statement_timeout` 5s and
  `idle_in_transaction_session_timeout` 30s, and with `application_name` set to the service name
  (`postgres.sessionSettings`, sent as `PGOPTIONS`/`PGAPPNAME`). A request stuck behind a lock fails fast with
  SQLSTATE 55P03 instead of holding a connection, and every session, metric and log line names its service.
  Other sessions are ended after 5 minutes idle in a transaction.
- Trace sampling is 100% (`otel.samplerArg: "1.0"`); lower it in `k8s/services/values.yaml` for production.
- Secrets are not stored in Git: `shop-db-app` is created by CloudNativePG and `redis-auth` by an
  External Secrets password generator (`refreshPolicy: CreatedOnce`).
- `image.registry` is intentionally empty in Git. It contains the GCP project ID, so Terraform
  (`argocd-bootstrap`) injects it into the `shop-services` Application; Git only holds the image tag.

SLOs per service (`slo` in `k8s/services/values.yaml`, 30-day window):

| SLI | Good event | Objective |
|---|---|---|
| Availability | response is not 5xx (4xx are client or business outcomes) | 99.5% |
| Latency | response faster than 500ms (api-gateway, order, payment) or 100ms (others) | 99% |

The same SLIs are also measured per **user journey** at api-gateway: `checkout` (POST /api/orders,
500ms) and `browse` (GET /api/products/, /api/users/, 100ms). Journeys show what users experience: a
service with no ready pod receives no requests, so its own SLI disappears, while the journeys that
depend on it keep burning.

Recording rules (label `kind` = `service` or `journey`): `slo:sli_error:ratio_rate<window>` for burn
rates, `slo:sli_requests:rate5m` and `slo:sli_errors:rate5m` for a traffic-weighted 30-day SLI,
`slo:objective:ratio`, `slo:error_budget_remaining:ratio` and `slo:sli_window:seconds` (how much data the
30-day window really holds). They feed the dashboards and the burn-rate alerts from the Google SRE
workbook: `ShopSLOBurnRateCritical` (1h and 5m above 14.4×, or 6h and 30m above 6×) and
`ShopSLOBurnRateWarning` (1d and 2h above 3×, or 3d and 6h above 1×).

Alerts shipped with the application (VMRule):

- SLO: `ShopSLOBurnRateCritical`, `ShopSLOBurnRateWarning` (labels `job`, `slo`).
- Metrics: `ShopServiceDown` (a deployment with no available replicas), `ShopPaymentDeclineRateHigh`.
- Logs (LogsQL, `type: vlogs`): `ShopErrorLogsSpike` — more than 20 ERROR logs per service in 5 minutes.
- Data tier: `RedisDown`, `RedisNoSingleMaster`, `RedisReplicasMissing`, `RedisMemoryNearLimit`,
  `PostgresDown`, `PostgresStandbyMissing`, `PostgresReplicationLagHigh`, `PostgresTooManyConnections`,
  `PostgresDiskSpaceLow`/`Critical`, `PostgresXidAgeHigh`, `PostgresLongRunningTransaction`,
  `PostgresIdleInTransaction`, `PostgresSessionsBlocked`, `PostgresWalArchivingStalled`,
  `PostgresWalArchivingFailing`, `PostgresBackupTooOld`, `PostgresBackupFailed`, and from the PostgreSQL
  logs (LogsQL) `PostgresLockContention` and `PostgresDeadlocks`. Each links to its section in
  [docs/runbooks](../docs/runbooks/README.md) (`runbook_url`).

## Restore drill

A backup only counts once it has been restored. `k8s/restore-drill/shop-db-restore.yaml` recovers the
backups into a throwaway Cluster (not managed by Argo CD) at a point in time. Its pods run as the
ServiceAccount `shop-db-restore`, which only has read access to the bucket, and it has no WAL archiver.

```bash
# 1. Point in time to restore to (UTC); it must be after the oldest backup in the recovery window
TARGET="2026-10-22 02:13:45+00"
sed "s|TARGET_TIME|$TARGET|" apps/k8s/restore-drill/shop-db-restore.yaml | kubectl apply -f -

# 2. Wait for "Cluster in healthy state" (a recovery job replays base backup + WAL first)
kubectl -n shop get cluster shop-db-restore -w

# 3. Check the data, e.g. the newest order must be older than TARGET
kubectl -n shop exec -it shop-db-restore-1 -c postgres -- psql -d app -c "SELECT count(*), max(created_at) FROM orders"

# 4. Clean up (the PVC goes with the Cluster)
kubectl -n shop delete cluster shop-db-restore
```

Last drill (2026-10-22): marker rows written 5 seconds before and after the target proved recovery to
the second (the first present, the second absent), and the order, payment, user and product counts matched
the source at the target time exactly. The restored instance was healthy 108 seconds after `kubectl apply`.
In a real disaster the same `bootstrap.recovery` section creates the replacement cluster.

## Releasing images (GitOps)

```bash
make -C apps test       # go vet + go build
make -C apps auth       # once: docker login to Artifact Registry
make -C apps release    # build linux/amd64, push <region>-docker.pkg.dev/<project>/staging-shop/<service>:<git-sha>,
                        # then write the tag to k8s/services/values.yaml
git add apps/k8s/services/values.yaml && git commit -m "release shop <sha>" && git push
```

Commit your code before `make release`: the tag is the git SHA and the Artifact Registry repository
uses immutable tags.

## Trying the API

```bash
kubectl -n shop port-forward svc/api-gateway 8080:8080
curl -s localhost:8080/api/products
curl -s -X POST localhost:8080/api/orders -H 'content-type: application/json' \
  -d '{"user_id":1,"product_id":2,"quantity":1}'
```
