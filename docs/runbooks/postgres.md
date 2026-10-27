# PostgreSQL runbook (CloudNativePG cluster `deus-db`)

The `Postgres*` alerts of the `postgres` VMRule (`configs/cloudnative-pg/base/monitoring.yaml`) link to the sections
below. For the general approach, field names and query recipes see [README.md](README.md).

## Quick reference

| What | Where |
|---|---|
| Dashboard | Grafana → Platform → CloudNativePG (`/d/cloudnative-pg`) |
| Cluster state, current primary | `kubectl -n deus get cluster deus-db` |
| psql on the primary | `kubectl -n deus exec -it "$(kubectl -n deus get cluster deus-db -o jsonpath='{.status.currentPrimary}')" -c postgres -- psql -d app` |
| PostgreSQL logs (VictoriaLogs) | `{kubernetes.pod_namespace="deus", kubernetes.container_name="postgres"}` |
| Backups | `kubectl -n deus get backups`, sidecar container `plugin-barman-cloud` |
| Configuration | `configs/cloudnative-pg/base/cluster.yaml`, changed through Git (Argo CD app `config-cloudnative-pg`) |

Session limits. Every session is ended after 5 minutes idle in a transaction (`cluster.yaml`). Application
sessions are expected to connect with tighter limits (`lock_timeout`, `statement_timeout`,
`idle_in_transaction_session_timeout`) and `application_name` set to the service name, so blockers and slow
statements name their service.

## SQLSTATE reference

| Code | Meaning | Usual cause |
|---|---|---|
| `55P03` | lock_not_available: cancelled by `lock_timeout` | another transaction holds the row or table: see [lock contention](#postgreslockcontention) |
| `57014` | query_canceled | `due to statement timeout`: ran past `statement_timeout`. `due to user request`: the client cancelled, usually its own timeout expired (the trace shows which call) |
| `25P03` | idle_in_transaction_session_timeout | a client opened a transaction and stopped sending statements |
| `40P01` | deadlock_detected | two transactions lock the same rows in opposite order |
| `23505` | unique_violation | duplicate key, usually a business outcome |
| `53300` | too_many_connections | connection pools larger than `max_connections` (100) |
| `57P01`, `57P03` | admin shutdown, cannot connect now | restart, switchover or failover in progress |

## Live diagnosis

Run these in psql on the primary, or through the read-only `postgres` MCP server (`execute_sql`; its role is
in `pg_monitor`, so it sees every session but cannot cancel or end one).

Who is blocked and by whom:

```sql
SELECT w.pid AS waiting_pid, w.application_name AS waiting_app, now() - w.query_start AS waiting_for,
       left(w.query, 60) AS waiting_query,
       b.pid AS blocker_pid, b.usename AS blocker_user, b.application_name AS blocker_app,
       b.state AS blocker_state, now() - b.xact_start AS blocker_xact_age, left(b.query, 60) AS blocker_last_query
FROM pg_stat_activity w
CROSS JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS bp(pid)
JOIN pg_stat_activity b ON b.pid = bp.pid
ORDER BY waiting_for DESC;
```

Oldest open transactions:

```sql
SELECT pid, usename, application_name, state, now() - xact_start AS xact_age, left(query, 60) AS query
FROM pg_stat_activity
WHERE xact_start IS NOT NULL
ORDER BY xact_start
LIMIT 10;
```

Sessions per application and state:

```sql
SELECT application_name, usename, state, count(*) FROM pg_stat_activity GROUP BY 1, 2, 3 ORDER BY 4 DESC;
```

Ending a session: `SELECT pg_cancel_backend(<pid>);` cancels its current statement,
`SELECT pg_terminate_backend(<pid>);` closes the session and rolls back its transaction. An idle-in-transaction
blocker has no statement to cancel, so it needs terminate.

## PostgresDown

**Meaning.** The exporter of one instance could not query PostgreSQL for 2 minutes.

**Impact.** On the primary, writes fail until CloudNativePG promotes the standby (seconds once the failure is
detected). On the standby, there is no failover candidate and commits are no longer synchronous.

**Diagnose.**
- `kubectl -n deus get cluster deus-db` and `kubectl -n deus get pods -l cnpg.io/cluster=deus-db -o wide`
- Events: `kubectl -n deus get events --sort-by=.lastTimestamp | tail -20`. Spot preemption shows as a node
  disappearing (`kubectl get nodes`).
- PostgreSQL logs of that pod for `FATAL`/`PANIC`; the "Instance (re)starts" and "Role changes" annotations.

**Fix.** A preempted instance comes back on another node. A pod stuck `Pending` usually lacks a node: instances
need different nodes (required anti-affinity), so check the cluster autoscaler. A crash loop with
`No space left on device` is [PostgresDiskSpaceCritical](#postgresdiskspacecritical).

## PostgresStandbyMissing

**Meaning.** The primary has no streaming standby for 5 minutes.

**Impact.** No automatic failover, and commits are acknowledged without the standby (`dataDurability: preferred`).

**Diagnose.** Standby pod status and logs; `kubectl -n deus get cluster deus-db -o jsonpath='{.status.instancesStatus}'`;
dashboard row Replication. A standby that fell too far behind looks for WAL in the archive (sidecar logs).

**Fix.** A standby that cannot catch up is recloned by deleting its PVC and pod (never the primary's: check
`.status.currentPrimary` first): `kubectl -n deus delete pvc/deus-db-<n> pod/deus-db-<n>`. The operator then
creates a new instance from the primary.

## PostgresReplicationLagHigh

**Meaning.** A standby replays WAL more than 30 seconds behind the primary for 5 minutes.

**Impact.** A failover now loses the transactions not yet replicated.

**Diagnose.** Replication row: "Bytes behind the primary" shows the stage that lags (sent: network or primary,
write/flush: standby disk, replay: standby CPU or a query conflicting with replay). Standby CPU throttling and
disk usage in Storage & I/O.

**Fix.** Remove the bottleneck (resources of the standby, a long query on the standby). Lag that keeps growing
ends in a reclone (see [PostgresStandbyMissing](#postgresstandbymissing)).

## PostgresTooManyConnections

**Meaning.** More than 80% of `max_connections` (100) are in use for 5 minutes.

**Diagnose.** Sessions per application and state ([live diagnosis](#live-diagnosis)), or
`cnpg_backends_total` by `application_name`. Each service pool opens at most `DB_MAX_CONNS` (5) per replica.
Many `idle` sessions of one service mean a pool too large; many `active` ones mean slow queries or lock waits.

**Fix.** Fix the leak or slow query; lower `DB_MAX_CONNS`; for many replicas put PgBouncer in front
(CloudNativePG `Pooler`).

## PostgresDiskSpaceLow

**Meaning.** A PostgreSQL volume is more than 80% full for 10 minutes.

**Diagnose.** Storage & I/O row (PGDATA vs WAL), Database Size, Backups row "pg_wal: waiting to archive and
size" (WAL piling up because archiving fails), Replication row "WAL retained by replication slots".

**Fix.** Grow `spec.storage.size` in `postgres.yaml` (pd-balanced volumes expand online; the operator resizes
each PVC). If WAL is the cause, fix archiving or drop the abandoned slot first.

## PostgresDiskSpaceCritical

**Meaning.** A volume is more than 90% full for 5 minutes. At 100% PostgreSQL stops accepting writes and can
crash.

**Fix.** Same as [PostgresDiskSpaceLow](#postgresdiskspacelow), now: grow the volume first, investigate after.

## PostgresXidAgeHigh

**Meaning.** The oldest unfrozen transaction ID of a database is more than 300 million transactions old.

**Impact.** Near 2 billion PostgreSQL refuses writes to prevent wraparound.

**Diagnose.** Something stops vacuum from freezing rows: a long or idle-in-transaction session
([oldest open transactions](#live-diagnosis)), an abandoned replication slot, or autovacuum not keeping up
(Table health: dead rows, last vacuum).

**Fix.** End the old transaction or slot, then `VACUUM (FREEZE, VERBOSE)` the oldest tables
(`SELECT relname, age(relfrozenxid) FROM pg_class WHERE relkind = 'r' ORDER BY 2 DESC LIMIT 10;`).

## PostgresLongRunningTransaction

**Meaning.** A transaction has been open for more than 5 minutes.

**Impact.** It keeps every lock it took and stops vacuum from removing dead rows (bloat, XID age).

**Diagnose.** [Oldest open transactions](#live-diagnosis): `application_name` names the service; `psql` or an
empty name is a person or a job. Dashboard: Operational Stats "Longest Transaction".

**Fix.** Ask the owner or cancel/terminate it. An application transaction this long is a bug: application
statements are limited to 5 seconds.

## PostgresIdleInTransaction

**Meaning.** A session has been idle inside an open transaction for more than a minute.

**Impact.** Same as a long transaction: locks held, vacuum blocked. PostgreSQL terminates it after 5 minutes
(`25P03`); application sessions already after 30 seconds, so this is usually a manual session or a job.

**Diagnose.** [Oldest open transactions](#live-diagnosis) with `state = 'idle in transaction'`; the alert labels
carry `usename` and `application_name`.

**Fix.** If it blocks others, `pg_terminate_backend(<pid>)` now; otherwise let the timeout end it and tell the
owner.

## PostgresSessionsBlocked

**Meaning.** Sessions have been waiting on locks for 2 minutes.

**Impact.** Application statements give up after 2 seconds (`lock_timeout`), so a sustained wait is usually a
migration or manual DDL waiting for an exclusive lock, and every statement on that table queues behind it.

**Diagnose.** Dashboard row Locks and blocking, "Blocking sessions": pid, user, application, state and
transaction age of each blocker (`cnpg_pg_blocking_*`); [who is blocked and by whom](#live-diagnosis) for the
current picture; PostgreSQL logs `record.message:"still waiting for"` name the pids holding the lock.

**Fix.** End the blocker (or the waiting DDL, if the queue behind it hurts more). Run DDL with a short
`lock_timeout` and retry it, instead of letting it wait.

## PostgresLockContention

**Meaning.** PostgreSQL logged at least 5 lock waits longer than 1 second or lock timeouts (`55P03`) on an
instance in 5 minutes (LogsQL rule on the PostgreSQL logs).

**Impact.** The waiting application requests fail (`55P03` once their `lock_timeout` expires) or slow down;
errors and latency SLO burn usually follow.

**Diagnose.**
1. Locks and blocking row, "Lock waits by statement and blocker": the waiting statement, the lock (`ShareLock on
   transaction` means a row lock held by another transaction) and the pids holding it.
2. Who is the blocker: "Blocking sessions" in the same row (pid, user, application, state, transaction age;
   kept for the selected range), [live diagnosis](#live-diagnosis) while it lasts, or its own log lines
   (`record.process_id:<pid>`).
3. Which rows: traces of the failing requests (the database span with the error and its attributes).

**Fix.** A long transaction holding the lock: end it ([PostgresLongRunningTransaction](#postgreslongrunningtransaction)).
Normal traffic on one hot row: shorten the transaction that holds it, or change the access pattern (queue,
`SKIP LOCKED`).

## PostgresDeadlocks

**Meaning.** PostgreSQL detected a deadlock (`40P01`) in the last 5 minutes and cancelled one of the transactions.

**Diagnose.** The `ERROR 40P01` log line's `record.detail` lists both processes, the locks and their statements:
`{kubernetes.pod_namespace="deus", kubernetes.container_name="postgres"} record.sql_state_code:40P01`.

**Fix.** Make the code paths lock rows in the same order (for example by primary key), or take the locks up
front with `SELECT ... FOR UPDATE` in a fixed order.

## PostgresWalArchivingStalled

**Meaning.** The primary has not archived a WAL segment for 15 minutes (it switches WAL at least every 5).

**Impact.** Point-in-time recovery cannot go past the last archived WAL (data at risk grows), and `pg_wal`
fills the disk.

**Diagnose.** Backups row: "Data at risk", "WAL waiting to archive", sidecar events. Sidecar logs:
`{kubernetes.pod_namespace="deus", kubernetes.container_name="plugin-barman-cloud"}`. Typical causes: bucket
missing or renamed (`kubectl -n deus get objectstore deus-backup -o yaml`), Workload Identity IAM on the
bucket (Terraform workspace `cloud-storage`), sidecar out of memory.

**Fix.** Restore access; the archiver resumes on its own and catches up.

## PostgresWalArchivingFailing

**Meaning.** Archive attempts keep failing for 15 minutes, while some still succeed.

**Diagnose and fix.** As [PostgresWalArchivingStalled](#postgreswalarchivingstalled); intermittent errors point
to timeouts or throttling towards the bucket.

## PostgresBackupTooOld

**Meaning.** The newest successful base backup is more than 26 hours old (the schedule is daily at 02:00 UTC).

**Diagnose.** `kubectl -n deus get backups --sort-by=.metadata.creationTimestamp` and
`kubectl -n deus describe backup <name>`; `kubectl -n deus get scheduledbackup deus-db-daily`; sidecar logs.

**Fix.** Fix the cause, then take a backup now:

```bash
kubectl -n deus create -f - <<'EOF'
apiVersion: postgresql.cnpg.io/v1
kind: Backup
metadata:
  generateName: deus-db-manual-
spec:
  cluster:
    name: deus-db
  method: plugin
  pluginConfiguration:
    name: barman-cloud.cloudnative-pg.io
EOF
```

## PostgresBackupFailed

**Meaning.** A backup attempt failed after the newest successful one.

**Diagnose and fix.** The failed `Backup` object's status has the error (`kubectl -n deus describe backup
<name>`); then as [PostgresBackupTooOld](#postgresbackuptooold). A backup that fails while an instance is being
recreated is retried by the next schedule; take a manual one if the next is far.

To prove that backups restore, run the [restore drill](#restore-drill).

## Restore drill

A backup only counts once it has been restored. `configs/cloudnative-pg/restore-drill/deus-db-restore.yaml`
recovers the backups into a throwaway Cluster (not managed by Argo CD) at a point in time. Its pods run as the
ServiceAccount `deus-db-restore`, which only has read access to the bucket (Terraform `cloud-storage`
workspace), and it has no WAL archiver, so the drill never writes to the backups.

```bash
TARGET="2026-10-22 02:13:45+00"
sed "s|TARGET_TIME|$TARGET|" configs/cloudnative-pg/restore-drill/deus-db-restore.yaml | kubectl apply -f -
kubectl -n deus get cluster deus-db-restore -w
kubectl -n deus exec -it deus-db-restore-1 -c postgres -- psql -d app -c "SELECT now()"
kubectl -n deus delete cluster deus-db-restore
```

`TARGET` must be after the oldest backup in the recovery window. Wait for "Cluster in healthy state" (a recovery
job replays the base backup and WAL first), check the data against the target time, then delete the Cluster (the
PVC goes with it). In a real disaster the same `bootstrap.recovery` section creates the replacement cluster.
