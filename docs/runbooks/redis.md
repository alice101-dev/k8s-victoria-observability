# Redis runbook (Redis with Sentinel)

The `Redis*` alerts of the `shop-data` VMRule (`apps/k8s/data/monitoring.yaml`) link to the sections below.
Redis runs as the StatefulSet `redis-node` (3 pods, one per node), each pod with the containers `redis`,
`sentinel` and `exporter` (`apps/k8s/data/redis.yaml`). Services find the master through
`redis-sentinel:26379` (master name `shop`). Redis holds the product and user caches (keys with a TTL; a miss
falls back to PostgreSQL), payment idempotency keys and the notification queue (Redis Stream `notifications`, consumer group `notifier`,
capped at about 10000 entries, no TTL).

Commands below read the password from the pod's environment, so it never appears on the command line:

```bash
# Roles and replication of each pod
for i in 0 1 2; do kubectl -n shop exec redis-node-$i -c redis -- sh -c \
  'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli info replication | grep -E "^(role|connected_slaves|master_host|master_link_status)"'; done

# The master according to Sentinel
kubectl -n shop exec redis-node-0 -c sentinel -- sh -c \
  'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli -p 26379 sentinel get-master-addr-by-name shop'
```

## RedisDown

**Meaning.** The exporter in a pod could not reach its Redis for 2 minutes.

**Diagnose.** Pod status and events (`kubectl -n shop get pods -l app.kubernetes.io/name=redis -o wide`),
`redis` container logs. A lost master is replaced by Sentinel in about 5 seconds; the services reconnect to the
new master.

**Fix.** A preempted pod returns on another node and rejoins as a replica (`init.sh` asks Sentinel for the
master). A pod stuck `Pending` needs a third node (required anti-affinity).

## RedisNoSingleMaster

**Meaning.** The number of pods reporting `role=master` is not exactly one for a minute.

**Impact.** No master: cache writes and notification enqueues fail. Two masters: split brain, writes on the old
master are lost when it rejoins.

**Diagnose.** Roles of each pod and Sentinel's view (commands above); `sentinel` container logs (`+switch-master`,
`+sdown`, `+odown`).

**Fix.** With no master, check that at least 2 Sentinels see each other (quorum). With two, the one Sentinel does
not report is demoted when it rejoins; if it is stuck, delete that pod.

## RedisReplicasMissing

**Meaning.** The master has fewer than 2 replicas connected for 5 minutes.

**Diagnose.** Replication info on each pod; a replica in `master_link_status:down` logs why (full resync, auth).

**Fix.** Usually a pod restarting after a preemption; it resyncs by itself. A replica that keeps resyncing may
need more memory (`maxmemory`, container limit).

## RedisMemoryNearLimit

**Meaning.** Used memory is above 90% of `maxmemory` (96mb) for 10 minutes.

**Impact.** Keys with a TTL are evicted early (`volatile-lru`): more cache misses and more load on PostgreSQL.
The notification stream has no TTL and is never evicted; once only such keys are left, writes fail with `OOM`.

**Fix.** Raise `maxmemory` together with the container memory limit in `redis.yaml`, or shorten the TTLs.
