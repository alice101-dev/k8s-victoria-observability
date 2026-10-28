# Deus runbook (the accounting app)

The alerts of the `deus` VMRule (`infrastructure/configs/victoria-metrics-k8s-stack/base/deus.yaml`) link to the sections
below. The rules, the SLO definitions and the dashboards are generated together by `tools/dashboards`
(`generator/deus.py` holds the objectives, thresholds and journeys); edit the generator, never the YAML.

| What | Where |
|---|---|
| Services | Deployments in `<env>-deus` (`staging-deus`), one per service, deployed by the `deus-<service>` Applications |
| Code and deploy values | [microservice-deus](https://github.com/alice101-dev/microservice-deus): `services/<name>/`, `charts/service` |
| Database | CloudNativePG cluster `deus-db`, one database and role per service ([postgres.md](postgres.md)) |
| Synthetic traffic | Deployment `traffic` (k6, `deus-traffic`): browse, sell, buy and deliberate 4xx requests through the gateway |
| Dashboards | Grafana → Deus: Overview, SLO Overview, Service, Logs, Traces |
| Metrics | `http_server_request_duration_seconds_*`, `http_client_request_duration_seconds_*`, `db_client_*`, `outbox_*` (label `service_name`) |
| Logs | LogsQL `service.namespace:=deus` (field `service.name`, `severity_text`, `exception.*`, `trace_id`) |
| Traces | VictoriaTraces, `"resource_attr:service.namespace":=deus` |

```text
# LogsQL: errors of one service with their cause
_time:1h service.namespace:=deus service.name:=<service> severity_text:=ERROR
  | fields _time, _msg, exception.type, exception.message, exception.cause, http.route, trace_id

# MetricsQL: 5xx by service and route
sum by (service_name, http_route) (rate(http_server_request_duration_seconds_count{service_namespace="deus", http_response_status_code=~"5.."}[5m]))
```

## DeusSLOBurnRateCritical

**Meaning.** A service or a user journey spends its 30-day error budget at least 14.4 times too fast (1h and 5m), or 6
times too fast (6h and 30m): 2% of the budget in one hour, or 5% in six. Labels `target`, `kind` (`service`,
`journey`) and `slo` (`availability`: 5xx; `latency`: slower than the threshold).

**Diagnose.** Deus — SLO Overview shows which target burns; open Deus — Service for it. For availability, the 5xx
ratio by route and Top WARN and ERROR messages name the failing endpoint and its exception; the error traces show
whether the failure comes from a dependency (an `/internal` call or the database). For latency, p95 by route and
the slowest traces show where the time goes; check the database pool (waiting for a connection) and CPU against
its request. A journey burning while its service looks fine fails in a service it calls. Rollout annotations
point at a release.

**Fix.** Roll back the release (revert the image tag in `values-<env>.yaml` of the app repository), restore the
dependency, or scale the service. If the threshold is wrong for the endpoint, change it in `generator/deus.py`.

## DeusSLOBurnRateWarning

**Meaning.** The budget is spent faster than planned over a long window (3 times over 1d and 2h, or above 1 over 3d
and 6h): 10% of the budget gone in a day or three. Not urgent, but the SLO will be missed if nothing changes.

**Diagnose.** As above, over the last days: a slow route that got slower with the data volume, a recurring error,
a dependency that times out from time to time.

**Fix.** Open a ticket with the route, the evidence and the expected fix.

## DeusServiceDown

**Meaning.** A deus Deployment has had no available replica for 2 minutes. Every request to it fails; its own SLI
stops moving because no request reaches it, so the journeys and the callers burn instead.

**Diagnose.** `kubectl -n staging-deus get pods -l app.kubernetes.io/name=<service>` and their events (Cluster —
Events dashboard). A pod in `Init` or `Pending` after a release usually waits for the PreSync migration Job
(`<service>-migrate-*`, its logs as `service.name:=<service>-migrations`); `CrashLoopBackOff` means the service fails
at startup (database, configuration, a secret); `Pending` with `FailedScheduling` means the node pool is full.

**Fix.** Fix the migration or the configuration and sync again, or roll back the image tag.

## DeusOutboxDeadEvents

**Meaning.** A service holds outbox events that will not be retried: the receiving service rejected them (400,
404, 422) or they used every attempt. The documents they carry (a journal, an allocation, an email) are missing on
the other side.

**Diagnose.** The relay logged every rejection: `service.namespace:=deus _msg:"outbox"` with `exception.message`.
In the database of the sending service: `select topic, last_error, attempts, created_at from outbox where dead_at is
not null order by created_at desc`. Deus — Traces → Outbox deliveries shows the failed spans; each continues the
trace of the request that wrote the event.

**Fix.** Fix the cause (a closed period, a missing account, a bug in the receiver), then make the events eligible
again: `update outbox set dead_at = null, attempts = 0, available_at = now() where id in (...)`. Delivery is
idempotent, so replaying an event that did arrive is safe.

## DeusOutboxBacklog

**Meaning.** More than 50 events have waited for 10 minutes: the receiving service is down or slow, or events are
written faster than the relay delivers them.

**Diagnose.** Deus — Service of the sending service, row Outbox: delivery delay and failed deliveries by destination.
Check the receiving service (`DeusServiceDown`, its 5xx) and the relay errors in the logs.

**Fix.** Restore the receiver; the backlog drains on its own.

## DeusDatabasePoolExhausted

**Meaning.** Requests of a service have waited for a database connection for 10 minutes: every connection of its
pool is in use.

**Diagnose.** Deus — Service → Database: pool by state, waiting requests, p95 by operation. Long statements or
transactions held open (calls to other services inside a transaction) keep connections busy; the CloudNativePG
dashboard shows locks and the statements on the server side.

**Fix.** Fix the slow statement or the transaction scope. Raise `database.maxConnections` only if the database has
room: every pod opens its own pool (see the CNPG Pooler item in the backlog).

## DeusErrorLogsSpike

**Meaning.** A service logged more than 20 ERROR records in 5 minutes (vmalert LogsQL rule).

**Diagnose.** Deus — Logs for the service: Message patterns names the error and its cause, What these records have
in common points at one route, pod, node or version. Short spikes during rollouts or node changes (database DNS not
resolving, connection timeouts) clear by themselves.

**Fix.** Depends on the error; follow the trace of one failing request.
