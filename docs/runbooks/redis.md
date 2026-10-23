# Redis runbook (Redis with Sentinel)

The alerts of the `shop-redis` VMRule (`apps/k8s/data/redis-monitoring.yaml`) link to the sections below; the
**Shop — Redis** dashboard (folder **Shop**) shows the same signals. Redis runs as the StatefulSet `redis-node`
(3 pods, one per node), each pod with the containers `redis`, `sentinel`, `exporter` (Redis, job `redis-node`)
and `sentinel-exporter` (the pod's Sentinel, job `redis-sentinel`) (`apps/k8s/data/redis.yaml`). Services find the
master through `redis-sentinel:26379` (master name `shop`, quorum 2, master down after 5s, failover timeout 30s)
with the go-redis `FailoverClient`. Redis holds the product and user caches (keys with a TTL; a miss falls back to
PostgreSQL), payment idempotency keys (`payment:idem:*`, 1 hour TTL) and the notification queue (Redis Stream
`notifications`, consumer group `notifier`, capped at about 10000 entries, no TTL). `maxmemory 96mb`,
`maxmemory-policy volatile-lru`, container limit 128Mi, AOF `appendfsync everysec`.

## Where to look

| Question | Signal |
|---|---|
| Who is master, could Sentinel fail over now? | `redis_instance_info{job="redis-node", role="master"}`, `redis_sentinel_master_ckquorum_status{job="redis-sentinel"}` (1 = quorum reachable), `redis_sentinel_master_ok_sentinels` |
| Did a failover happen, why? | LogsQL on the sentinels: `{kubernetes.pod_namespace="shop", kubernetes.container_name="sentinel"}` with `+sdown`, `+odown`, `+switch-master`, `-failover-abort-*`, `+tilt` |
| Is replication healthy? | `redis_master_link_up`, `redis_connected_slave_lag_seconds`, `redis_master_repl_offset` per pod |
| Memory and evictions | `redis_memory_used_bytes` / `redis_memory_max_bytes`, `redis_evicted_keys_total`, `redis_errors_total{err="OOM"}`, container working set of `container="redis"` |
| What the services see | Spans in VictoriaTraces (`"span_attr:db.system":=redis`, span name = command, `span_attr:db.statement`), pool metrics `goredis_pool_*{client="default" or "stream-consumer"}` |
| Notification queue | `redis_stream_group_lag`, `redis_stream_group_messages_pending`, `redis_stream_group_consumer_idle_seconds` (stream `notifications`) |

```text
# MetricsQL: role, replication link and bytes behind per pod
max by (pod, role) (redis_instance_info{job="redis-node"})
max by (pod) (redis_master_link_up{job="redis-node"})

# LogsQL (VictoriaLogs): what the sentinels decided in the last hour
_time:1h {kubernetes.pod_namespace="shop", kubernetes.container_name="sentinel"}
  _msg:~"[+-](switch-master|sdown|odown|failover|tilt)" | fields _time, kubernetes.pod_name, _msg

# LogsQL (VictoriaTraces): client latency and errors per service and command
_time:1h "span_attr:db.system":=redis | stats by ("resource_attr:service.name", name)
  count() calls, count() if (status_code:=2) errors, quantile(0.99, duration) p99_ns
```

Commands below read the password from the pod's environment, so it never appears on the command line:

```bash
# Roles and replication of each pod
for i in 0 1 2; do kubectl -n shop exec redis-node-$i -c redis -- sh -c \
  'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli info replication | grep -E "^(role|connected_slaves|master_host|master_link_status)"'; done

# Sentinel: the master it follows, quorum check, the other sentinels
kubectl -n shop exec redis-node-0 -c sentinel -- sh -c \
  'export REDISCLI_AUTH="$REDIS_PASSWORD"; redis-cli -p 26379 sentinel get-master-addr-by-name shop;
   redis-cli -p 26379 sentinel ckquorum shop; redis-cli -p 26379 sentinel sentinels shop | grep -A1 -E "^(name|flags)$"'
```

## RedisDown

**Meaning.** The exporter in a pod could not reach its Redis for 2 minutes.

**Diagnose.** Pod status and events (`kubectl -n shop get pods -l app.kubernetes.io/name=redis -o wide`),
`redis` container logs. A lost master is replaced by Sentinel in about 5 seconds (`RedisFailover`); the services
reconnect to the new master.

**Fix.** A preempted pod returns on another node and rejoins as a replica (`init.sh` asks Sentinel for the
master). A pod stuck `Pending` needs a third node (required anti-affinity).

## RedisNoSingleMaster

**Meaning.** The number of pods reporting `role=master` is not exactly one for a minute.

**Impact.** No master: cache writes, payment idempotency checks and notification enqueues fail. Two masters:
split brain, writes on the old master are lost when it rejoins.

**Diagnose.** Roles of each pod and Sentinel's view (commands above), the Topology row of the dashboard (Nodes
table: "Its sentinel follows"), the sentinel logs.

**Fix.** With no master, check that at least 2 Sentinels see each other (`RedisSentinelQuorumLost`). With two,
the old master is demoted when its Sentinel learns the new epoch; if it is stuck, delete that pod.

## RedisReplicasMissing

**Meaning.** The master has fewer than 2 replicas connected for 5 minutes.

**Diagnose.** Replication info on each pod; a replica in `master_link_status:down` logs why (full resync, auth).

**Fix.** Usually a pod restarting after a preemption; it resyncs by itself. A replica that keeps resyncing may
need more memory (`maxmemory`, container limit) or a larger `repl-backlog-size`.

## RedisReplicationBroken

**Meaning.** A replica has had no replication link to the master for a minute (`master_link_up 0`).

**Impact.** It serves stale data and is not a safe failover candidate.

**Diagnose.** The replica's `redis` logs (`MASTER <-> REPLICA sync`, `Connection with master lost`), the Resyncs
panel; `master_sync_in_progress` while a full resync runs.

**Fix.** A resync after a restart finishes by itself. A replica that cannot reach the master: DNS or
NetworkPolicy (port 6379 between Redis pods), or the master changed and the replica's Sentinel did not
reconfigure it (`RedisSentinelsDisagree`).

## RedisReplicaLagging

**Meaning.** A replica has not acknowledged the replication stream for more than 10 seconds, for 5 minutes.

**Fix.** CPU starvation or a slow disk on the replica's node (Kubernetes / Views / Nodes); a burst of large
writes. A failover now would lose the unacknowledged writes.

## RedisMemoryNearLimit

**Meaning.** Used memory is above 90% of `maxmemory` (96mb) for 10 minutes.

**Impact.** Keys with a TTL are evicted early (`volatile-lru`, `RedisEvictingKeys`): more cache misses and more
load on PostgreSQL. The notification stream has no TTL and is never evicted; once only such keys are left,
writes fail with `OOM` (`RedisOutOfMemoryErrors`).

**Fix.** Raise `maxmemory` together with the container memory limit in `redis.yaml`, or shorten the TTLs.
Find big keys with `redis-cli --bigkeys` on a replica.

## RedisEvictingKeys

**Meaning.** Redis removes keys to stay under `maxmemory`.

**Impact.** Cache hit ratio drops (Overview row). Payment idempotency keys can be evicted before their hour is
over, so a retried payment request is no longer recognised as a duplicate.

**Fix.** As for `RedisMemoryNearLimit`. In production the idempotency keys belong in a store without eviction
(PostgreSQL unique constraint or a separate Redis with `noeviction`).

## RedisOutOfMemoryErrors

**Meaning.** Redis answered writes with `OOM`: it is at `maxmemory` and nothing evictable is left.

**Impact.** Notification enqueues (`XADD`), idempotency checks (`SET NX`) and cache writes fail; payment and
notification requests return errors.

**Fix.** Free memory now (trim the stream: `XTRIM notifications MAXLEN ~ 1000` if the notifier group has read
it, see `RedisNotificationBacklog`), then raise `maxmemory` and the limit.

## RedisContainerMemoryNearLimit

**Meaning.** The `redis` container's working set is above 90% of its 128Mi limit for 10 minutes.

**Impact.** The kernel OOM-kills Redis (not an eviction): a master fails over, a replica needs a full resync.

**Diagnose.** Memory panel: the gap between Redis' dataset and the container working set is replication and
client output buffers, and the AOF rewrite fork's copy-on-write pages (AOF panel: "rewriting").

**Fix.** Raise the container limit (keep `maxmemory` at about 75% of it).

## RedisRejectedConnections

**Meaning.** Redis refused connections because `maxclients` is reached.

**Fix.** Connections panel: a service whose pool grows without bound (leak) or too many replicas × pool size.

## RedisPersistenceFailing

**Meaning.** The last AOF write or rewrite failed on a pod.

**Impact.** With `appendonly yes` Redis refuses writes while AOF writes fail (`MISCONF` errors in the services).

**Fix.** Data volume full or failing (Data volumes panel, `kubectl -n shop describe pvc data-redis-node-N`).
Grow the PVC (the StorageClass expands online) or recreate the replica's volume.

## RedisNotificationBacklog

**Meaning.** More than 100 notifications wait unread in the stream for 10 minutes.

**Diagnose.** notification-service pods and logs (`read stream failed`), the Consumers table.

**Fix.** Restore notification-service; it catches up by itself. Past MAXLEN 10000 the oldest unread entries are
trimmed and lost.

## RedisNotificationsStuckPending

**Meaning.** Entries were delivered to a consumer of the `notifier` group and never acknowledged for 15
minutes: that consumer (a notification-service pod) stopped while delivering them.

**Impact.** The service does not reclaim pending entries (no `XAUTOCLAIM`), so those notifications are never
sent.

**Fix.** The service reads only new entries (`XREADGROUP ... >`), so claiming them for a live consumer does not
deliver them either. Re-enqueue them (read their fields with `XRANGE`, `XADD` them again, then `XACK` the
originals), or `XACK` them if they must not be sent anymore. Then remove the dead consumers. The lasting fix is
in notification-service: reclaim stale pending entries periodically (`XAUTOCLAIM` with a min idle time) and
deliver them.

```bash
# On the master: pending entries with their consumer and idle time, and the consumers
kubectl -n shop exec redis-node-0 -c redis -- sh -c 'export REDISCLI_AUTH="$REDIS_PASSWORD";
  redis-cli xpending notifications notifier - + 20; redis-cli xinfo consumers notifications notifier'
# XACK notifications notifier <id> ...        acknowledge (after re-enqueuing with XADD if needed)
# XGROUP DELCONSUMER notifications notifier <name>   remove a dead consumer once it has no pending entries
```

## RedisClientPoolTimeouts

**Meaning.** A service's go-redis commands failed because every pooled connection stayed busy for the whole
pool timeout.

**Diagnose.** "Connection pools" and "Pool waits and timeouts" panels (which service and client), server
latency and Slow log (slow Redis holds connections longer), a failover at the same time.

**Fix.** Slow commands first; otherwise raise `REDIS_POOL_SIZE` for that service in
`apps/k8s/services/values.yaml`.

## RedisSentinelQuorumLost

**Meaning.** No sentinel reports that the quorum (2 of 3) is reachable (`SENTINEL CKQUORUM`), or no sentinel
reports at all, for 2 minutes.

**Impact.** Nothing is broken yet, but if the master fails now no replica is promoted and every write fails.

**Diagnose.** Sentinel containers and their readiness, the Nodes table (healthy sentinels seen), sentinel logs
(`+sdown sentinel`). Two Redis pods down at once (two nodes lost) is the usual cause.

**Fix.** Bring the pods back (nodes, scheduling). If the sentinels run but do not see each other: DNS of the
announced hostnames or the NetworkPolicy (port 26379 between Redis pods).

## RedisSentinelsDegraded

**Meaning.** A sentinel sees fewer than 3 healthy sentinels for 5 minutes. The quorum still holds; one more lost
node blocks failovers.

## RedisSentinelMasterDown

**Meaning.** At least 2 sentinels have seen the master as down (`sdown`/`odown`) for a minute and no failover
replaced it.

**Diagnose.** `RedisFailoverAborted`, sentinel logs (`-failover-abort-no-good-slave`: no replica in sync;
`-failover-abort-not-elected`: no leader; TILT).

**Fix.** Restore the master pod, or make a replica healthy so the next failover attempt succeeds. Last resort:
`SENTINEL FAILOVER shop` on a sentinel.

## RedisSentinelsDisagree

**Meaning.** Sentinels follow different masters for 2 minutes.

**Impact.** Clients asking different sentinels write to different nodes (split brain).

**Fix.** Find the outlier in the Nodes table ("Its sentinel follows"); delete that pod so it starts with the
master the other sentinels report (`init.sh`).

## RedisFailover

**Meaning.** Sentinel promoted a replica: the master moved from `old` to `new`.

**Impact.** For a few seconds writes failed (connection errors, `READONLY` on the old master) until the clients
followed. Planned during Redis rollouts (`preStop` asks Sentinel to fail over first), unplanned after a crash, an
OOM kill or a node preemption.

**Diagnose.** Sentinel events panel (`+sdown` → `+odown` → `+switch-master`), the Role over time panel, the old
master's pod events and logs (Cluster — Events), client errors at that time (Clients row).

## RedisFailoverAborted

**Meaning.** A failover started and was aborted; the reason is in the alert (`no-good-slave`, `not-elected`,
`slave-timeout`).

**Fix.** See `RedisSentinelMasterDown`.

## RedisSentinelTilt

**Meaning.** A sentinel entered TILT mode: its timer jumped by more than 2 seconds (clock change, or the process
did not run: CPU starvation, a paused VM). For at least 30 seconds it only observes and authorises no failover.

**Diagnose.** Several TILTs at once on one node point at that node (Kubernetes / Views / Nodes: CPU, steal);
they often follow node preemptions when the remaining nodes take the rescheduled pods.

**Fix.** Give the sentinel container a CPU request it actually gets, or move pods off the starved node.
