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
│   ├── postgres.yaml       # CloudNativePG Cluster "shop-db", PostgreSQL 18.6, 1 instance, 10Gi
│   ├── redis.yaml          # Redis 8 StatefulSet (AOF) + redis_exporter + password ExternalSecret
│   ├── networkpolicy.yaml  # only shop pods (+ operator, + metrics scraper) can reach PostgreSQL/Redis
│   └── monitoring.yaml     # VMServiceScrape (Redis), VMPodScrape (CloudNativePG), data-tier VMRule
└── services/      # Helm chart — Argo CD app "shop-services" (wave 0)
    ├── values.yaml         # image tag, OpenTelemetry, resources, per-service env
    ├── templates/          # Deployment + Service + ServiceAccount + PDB per service, scrape, rules, traffic generator
    └── dashboards/         # Grafana dashboard "Shop — Microservices Overview"
```

- Each service requests `10m CPU / 32Mi` with a `128Mi` limit and runs 2 replicas spread across nodes
  with a PodDisruptionBudget, so losing one Spot node does not take a service down.
- PostgreSQL runs a single instance; set `instances: 3` for automatic failover (applications already
  connect through the `shop-db-rw` service).
- Trace sampling is 100% (`otel.samplerArg: "1.0"`); lower it in `k8s/services/values.yaml` for production.
- Secrets are not stored in Git: `shop-db-app` is created by CloudNativePG and `redis-auth` by an
  External Secrets password generator (`refreshPolicy: CreatedOnce`).
- `image.registry` is intentionally empty in Git. It contains the GCP project ID, so Terraform
  (`argocd-bootstrap`) injects it into the `shop-services` Application; Git only holds the image tag.

Alerts shipped with the application (VMRule):

- Metrics: `ShopHighErrorRate` (>5% 5xx), `ShopHighLatencyP95` (>1s), `ShopPaymentDeclineRateHigh`.
- Logs (LogsQL, `type: vlogs`): `ShopErrorLogsSpike` — more than 20 ERROR logs per service in 5 minutes.
- Data tier: `RedisDown`, `RedisMemoryNearLimit`, `PostgresDown`, `PostgresTooManyConnections`.

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
