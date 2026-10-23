"""Shop — Redis: Redis with Sentinel as a whole: who is master and since when, whether a failover could happen
right now, replication, memory and evictions, commands, what the services see (spans and their connection
pools), the notification stream, persistence, and the Sentinel and client logs."""
from .common import (ANNOTATIONS, CADVISOR, COUNT, LINKS_ON, RI, VL, VM, by_name, Dash, dashboard, logs_panel,
    logsql_table, right_axis, stat, status_history, table, thresholds, ts, vl, vl_stat, vm)

FILE = "shop-redis.json"
UID = "shop-redis"

d = Dash()
VTL = {"type": "victoriametrics-logs-datasource", "uid": "VictoriaTracesLogsQL"}
NODE = 'job="redis-node"'
SENT = 'job="redis-sentinel"'
MASTER_POD = f'max by (pod) (redis_instance_info{{{NODE}, role="master"}})'
SENTINEL_LOGS = '{kubernetes.pod_namespace="shop", kubernetes.container_name="sentinel"}'
REDIS_LOGS = '{kubernetes.pod_namespace="shop", kubernetes.container_name=~"redis|sentinel"}'
# Sentinel events worth reading (https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/):
# subjective/objective down, failover steps, TILT, replicas reconfigured, sentinel or replica restarts.
EVENTS = ('_msg:~"[+-](switch-master|sdown|odown|try-failover|elected-leader|failover-[a-z-]+|selected-slave|'
          'promoted-slave|slave-reconf-[a-z-]+|tilt|reboot|convert-to-slave|fix-slave-config)"')
SPANS = '"resource_attr:service.namespace":=shop "span_attr:db.system":=redis'
SVC = '| copy "resource_attr:service.name" as service'
TRACE_LINK = [{"title": "Open this trace", "url": "/d/shop-traces/shop-traces?var-trace_id=${__value.raw}&${__url_time_range}"}]
STREAM = 'stream="notifications"'
ROLE = {"2": ("Master", "blue"), "1": ("Replica", "green"), "0": ("Down", "red")}
fixed = lambda name, color, matcher="byName": {"matcher": {"id": matcher, "options": name},
                                               "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}


def mappings(states):
    return [{"type": "value", "options": {v: {"text": t, "color": c, "index": i}
                                          for i, (v, (t, c)) in enumerate(states.items())}}]


def mapped(name, width, states, cell="color-text"):
    return by_name(name, [{"id": "custom.width", "value": width}, {"id": "custom.cellOptions", "value": {"type": cell}},
                          {"id": "mappings", "value": mappings(states)}])


# Bytes a pod is behind the master's replication offset (0 for the master; scrapes are seconds apart, so small
# negative values are clamped).
BEHIND = (f"clamp_min(scalar(max(redis_master_repl_offset{{{NODE}}} * on (pod) group_left () {MASTER_POD})) "
          f"- max by (pod) (redis_master_repl_offset{{{NODE}}}), 0)")


def instant(expr, ref):
    return vm(expr, ref=ref, instant=True, fmt="table")


def per_interval(p, interval="5m"):
    p["interval"] = interval
    return p


def on_master(expr):
    """Keep only the series of the current master (the stream and keyspace replicate to every pod)."""
    return f"max(({expr}) * on (pod) group_left () {MASTER_POD})"


def vt_stat(title, expr, unit, th, desc, decimals=0):
    p = vl_stat(title, expr, unit, th, desc, sparkline=False, decimals=decimals)
    p["datasource"], p["targets"][0]["datasource"] = VTL, VTL
    return p


d.row("Overview — is Redis healthy, and could it fail over right now?")
p = d.add(stat("Master", MASTER_POD, "short", thresholds(("blue", None)),
               "The pod that takes the writes now. Clients find it through Sentinel (service redis-sentinel).",
               decimals=0, no_value="No master"), 4, 4)
p["targets"][0]["legendFormat"] = "{{pod}}"
p["options"].update(textMode="name", colorMode="background")
for p in (
    stat("Failover possible", f"max(redis_sentinel_master_ckquorum_status{{{SENT}}})", "short",
         thresholds(("red", None), ("green", 1)), "SENTINEL CKQUORUM: enough sentinels (2 of 3) agree to promote a "
         "replica if the master fails. No means the next master failure is an outage.", decimals=0,
         no_value="No sentinel data"),
    stat("Healthy sentinels", f"min(redis_sentinel_master_ok_sentinels{{{SENT}}})", "short",
         thresholds(("red", None), ("orange", 2), ("green", 3)), "Sentinels the least informed sentinel sees as "
         "healthy (of 3). Quorum is 2: at 2 one more lost node blocks failovers.", decimals=0, no_value="–"),
    stat("Replicas in sync", f"count(redis_master_link_up{{{NODE}}} == 1) or vector(0)", "short",
         thresholds(("red", None), ("orange", 1), ("green", 2)), "Replicas with a live replication link to the "
         "master (of 2): the failover candidates.", decimals=0),
    vl_stat("Failovers", f'{SENTINEL_LOGS} "+failover-end" | stats count() failovers', "short",
            thresholds(("green", None), ("orange", 1)), "Completed failovers in the time range (logged by the "
            "sentinel that led them). Each one fails writes for a few seconds; annotations mark when.",
            sparkline=False),
    vl_stat("Sentinel TILT events", f'{SENTINEL_LOGS} "+tilt" | stats count() tilts', "short",
            thresholds(("green", None), ("orange", 1)), "Times a sentinel entered TILT (clock jump or stalled "
            "process, usually CPU starvation): for 30s it authorises no failover.", sparkline=False),
):
    d.add(p, 4, 4)
d.panels[-5]["fieldConfig"]["defaults"]["mappings"] = [{"type": "value", "options": {
    "1": {"text": "Yes", "index": 0}, "0": {"text": "No quorum", "index": 1}}}]
for p in (
    stat("Commands / s", f"sum(rate(redis_commands_processed_total{{{NODE}}}[5m]))", "short",
         thresholds(("blue", None)), "Commands processed by all nodes (replication traffic included).", decimals=0),
    stat("Cache hit ratio", f"sum(rate(redis_keyspace_hits_total{{{NODE}}}[$__range])) / (sum(rate(redis_keyspace_hits_total{{{NODE}}}[$__range])) "
         f"+ sum(rate(redis_keyspace_misses_total{{{NODE}}}[$__range])))", "percentunit",
         thresholds(("red", None), ("orange", 0.5), ("green", 0.8)), "Key lookups that found the key, over the time "
         "range (user and product cache reads, idempotency checks).", decimals=1),
    stat("Memory of maxmemory", on_master(f"redis_memory_used_bytes{{{NODE}}} / redis_memory_max_bytes{{{NODE}}}"),
         "percentunit", thresholds(("green", None), ("orange", 0.8), ("red", 0.9)), "The master's dataset against "
         "maxmemory (96mb). At 100% keys with a TTL are evicted, then writes fail with OOM.", decimals=1),
    stat("Evicted keys", f"sum(round(increase(redis_evicted_keys_total{{{NODE}}}[$__range])))", "short",
         thresholds(("green", None), ("red", 1)), "Keys removed for memory in the time range: cache entries and "
         "payment idempotency keys.", decimals=0, no_value="0"),
    vt_stat("Client p99 latency", f"{SPANS} | stats quantile(0.99, duration) p99", "ns",
            thresholds(("green", None), ("orange", 20e6), ("red", 100e6)), "99th percentile of every Redis call "
            "as the services measure it (spans): network, pool wait and Redis together."),
    stat("Notification backlog", f'max(redis_stream_group_lag{{{NODE}, {STREAM}}})', "short",
         thresholds(("green", None), ("orange", 100), ("red", 1000)), "Notifications in the stream not yet read by "
         "the notifier group.", decimals=0),
):
    d.add(p, 4, 4)

d.row("Topology and failover")
NODE_COLUMNS = [("pod", "Pod"), ("node", "Node"), ("role", "Role"), ("Value #C", "Master link"),
                ("Value #D", "Behind master"), ("Value #E", "Clients"), ("Value #F", "Memory"),
                ("Value #G", "Fragmentation"), ("Value #H", "Up for"), ("follows", "Its sentinel follows"),
                ("Value #I", "Sentinel sees master"), ("Value #J", "Healthy sentinels seen"), ("Value #K", "TILT")]
d.add(table("Nodes", [
    instant(f"max by (pod, role) (redis_instance_info{{{NODE}}})", "A"),
    instant('max by (pod, node) (kube_pod_info{namespace="shop", pod=~"redis-node-[0-9]+"})', "B"),
    instant(f"max by (pod) (redis_master_link_up{{{NODE}}})", "C"),
    instant(BEHIND, "D"),
    instant(f"max by (pod) (redis_connected_clients{{{NODE}}})", "E"),
    instant(f"max by (pod) (redis_memory_used_bytes{{{NODE}}})", "F"),
    instant(f"max by (pod) (redis_mem_fragmentation_ratio{{{NODE}}})", "G"),
    instant(f"max by (pod) (redis_uptime_in_seconds{{{NODE}}})", "H"),
    instant(f'max by (pod, follows) (label_replace(redis_sentinel_master_status{{{SENT}}}, "follows", "$1", '
            f'"master_address", "([^.:]+).*"))', "I"),
    instant(f"max by (pod) (redis_sentinel_master_ok_sentinels{{{SENT}}})", "J"),
    instant(f"max by (pod) (redis_sentinel_tilt{{{SENT}}})", "K")],
    [{"id": "merge", "options": {}},
     {"id": "organize", "options": {"excludeByName": {"Time": True, "Value #A": True, "Value #B": True},
                                    "indexByName": {f: i for i, (f, _) in enumerate(NODE_COLUMNS)},
                                    "renameByName": dict(NODE_COLUMNS)}}],
    overrides=[by_name("Pod", [{"id": "custom.width", "value": 120}]),
               mapped("Role", 90, {"master": ("Master", "blue"), "slave": ("Replica", "green")}, "color-background"),
               mapped("Master link", 100, {"1": ("Up", "green"), "0": ("Down", "red")}),
               by_name("Master link", [{"id": "noValue", "value": "–"}]),
               by_name("Behind master", [{"id": "unit", "value": "bytes"}, {"id": "custom.width", "value": 120},
                                         {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                         {"id": "thresholds", "value": thresholds(("green", None), ("orange", 1e5), ("red", 1e7))}]),
               COUNT("Clients", 80),
               by_name("Memory", [{"id": "unit", "value": "bytes"}, {"id": "custom.width", "value": 90}]),
               by_name("Fragmentation", [{"id": "decimals", "value": 2}, {"id": "custom.width", "value": 110}]),
               by_name("Up for", [{"id": "unit", "value": "dtdurations"}, {"id": "custom.width", "value": 150}]),
               by_name("Its sentinel follows", [{"id": "custom.width", "value": 150}]),
               mapped("Sentinel sees master", 150, {"1": ("ok", "green"), "0": ("down", "red")}),
               COUNT("Healthy sentinels seen", 160),
               mapped("TILT", 70, {"0": ("no", "green"), "1": ("TILT", "red")})],
    desc="Every Redis pod: its node, role, replication link and how many bytes it is behind the master, clients, "
         "memory, fragmentation (RSS / used: above 1.5 wastes RAM once the dataset is large; below ~100 MB the "
         "allocator's fixed overhead makes it high and meaningless), uptime, and what the sentinel in the same pod believes: "
         "which master it follows, whether that master is ok, how many sentinels it sees healthy, TILT. All "
         "sentinels must follow the same master (RedisSentinelsDisagree).",
    sort=[{"displayName": "Pod", "desc": False}]), 24, 6)
d.add(status_history("Role over time",
                     vm(f"{MASTER_POD} * 2 or max by (pod) (redis_instance_info{{{NODE}, role=\"slave\"}}) "
                        f"or max by (pod) (redis_up{{{NODE}}}) * 0", "{{pod}}"),
                     "Which pod was master when: a change of colour between pods is a failover.",
                     thresholds(("red", None))), 10, 8)
d.panels[-1]["fieldConfig"]["defaults"]["mappings"] = mappings(ROLE)
# Sentinel prefixes warnings with "#" and notices with "*"; the event starts after it.
d.add(logs_panel("Sentinel events", f"{SENTINEL_LOGS} {EVENTS} | extract_regexp \" [#*] (?P<event>[+-].*)\" from _msg "
                 "| format \"<kubernetes.pod_name>: <event>\" as _msg",
                 "What the sentinels decided: +sdown (one sentinel lost contact for 5s), +odown (the quorum agrees "
                 "the master is down), +try-failover / +elected-leader / +selected-slave / +promoted-slave, "
                 "+switch-master (clients now go to the new master), +tilt, -failover-abort-* (no replica could "
                 "be promoted)."), 14, 8)

d.row("Replication")
d.add(ts("Bytes behind the master",
         [vm(BEHIND, "{{pod}}")],
         "bytes", min_=0, desc="Replication offset of each replica against the master's: what a failover to that "
         "pod would lose. Normally a few bytes (scrapes of the pods are a few seconds apart)."), 8, 7)
d.add(ts("Replica acknowledgement lag",
         [vm(f"max by (slave_ip) (label_replace(redis_connected_slave_lag_seconds{{{NODE}}}, \"slave_ip\", \"$1\", "
             f"\"slave_ip\", \"([^.]+).*\"))", "{{slave_ip}}")],
         "s", min_=0, decimals=0, desc="Seconds since each replica last acknowledged the stream from the master "
         "(RedisReplicaLagging above 10s)."), 8, 7)
d.add(per_interval(ts("Resyncs", [vm(f"sum(round(increase(redis_replica_resyncs_full{{{NODE}}}[$__interval])))", "full"),
                                  vm(f"sum(round(increase(redis_replica_partial_resync_accepted{{{NODE}}}[$__interval])))", "partial", "B"),
                                  vm(f"sum(round(increase(redis_replica_partial_resync_denied{{{NODE}}}[$__interval])))", "partial denied", "C")],
                      "short", bars=True, stack=True, decimals=0, legend_calcs=("sum",),
                      overrides=[fixed("full", "orange"), fixed("partial denied", "red")],
                      desc="Replicas reconnecting: partial (from the backlog) is cheap; full copies the whole "
                           "dataset (after a restart, a failover, or a backlog too small for the outage).")), 8, 7)

d.row("Memory and keys")
d.add(ts("Memory", [vm(f"max by (pod) (redis_memory_used_bytes{{{NODE}}})", "{{pod}} dataset"),
                    vm(f"max by (pod) (container_memory_working_set_bytes{{{CADVISOR}, namespace=\"shop\", container=\"redis\"}})",
                       "{{pod}} container", "B"),
                    vm(f"max(redis_memory_max_bytes{{{NODE}}})", "maxmemory", "C"),
                    vm('max(kube_pod_container_resource_limits{namespace="shop", container="redis", resource="memory"})',
                       "container limit", "D")],
         "bytes", min_=0, overrides=[fixed("maxmemory", "orange"), fixed("container limit", "red")],
         desc="Redis' own memory per pod against maxmemory (evictions start there) and the container's working set "
              "against its limit (the kernel OOM-kills there): replication and client buffers and the AOF "
              "rewrite fork sit between the two."), 12, 8)
d.add(ts("Evicted and expired keys", [vm(f"sum by (pod) (rate(redis_evicted_keys_total{{{NODE}}}{RI}))", "{{pod}} evicted"),
                                      vm(f"sum(rate(redis_expired_keys_total{{{NODE}}}{RI}))", "expired (TTL)", "B")],
         "short", min_=0, overrides=[fixed(".* evicted", "red", "byRegexp")],
         desc="Keys per second removed for memory (evicted: should be 0) and at the end of their TTL (expired: "
              "normal cache turnover)."), 6, 8)
d.add(ts("Keys", [vm(on_master(f"sum by (pod) (redis_db_keys{{{NODE}}})"), "keys"),
                  vm(on_master(f"sum by (pod) (redis_db_keys_expiring{{{NODE}}})"), "with a TTL (evictable)", "B")],
         "short", min_=0, decimals=0, desc="Keys on the master; only those with a TTL can be evicted "
         "(volatile-lru). The notification stream has none."), 6, 8)

d.row("Commands and latency — the server side")
d.add(ts("Commands by command", [vm(f"topk(8, sum by (cmd) (rate(redis_commands_total{{{NODE}}}{RI})))", "{{cmd}}")],
         "short", stack=True, desc="Top commands per second on all nodes (replicas run the replicated writes)."), 8, 7)
d.add(ts("Server latency p99 by command",
         [vm(f'max by (cmd) (redis_latency_percentiles_usec{{{NODE}, quantile="99"}}) '
             f'and on (cmd) topk(8, sum by (cmd) (rate(redis_commands_total{{{NODE}}}{RI})))', "{{cmd}}")],
         "µs", min_=0, desc="Time Redis spends executing each command (LATENCY HISTOGRAM, since start). Compare "
         "with the client latency below: the difference is network and pool waiting."), 8, 7)
d.add(ts("Errors and rejections", [vm(f"sum by (err) (rate(redis_errors_total{{{NODE}}}{RI})) > 0", "{{err}} replies"),
                                   vm(f"sum(rate(redis_rejected_connections_total{{{NODE}}}{RI})) > 0", "rejected connections", "B"),
                                   vm(f"sum by (cmd) (rate(redis_commands_rejected_calls_total{{{NODE}}}{RI})) > 0", "{{cmd}} rejected", "C")],
         "short", min_=0, desc="Error replies by type: OOM (memory full), READONLY (write on a replica, during a "
         "failover), LOADING (replica loading its data), NOAUTH (client before AUTH), BUSYGROUP (the stream group "
         "exists, harmless at service start)."), 8, 7)
d.add(ts("Connections", [vm(f"max by (pod) (redis_connected_clients{{{NODE}}})", "{{pod}} clients"),
                         vm(f"max by (pod) (redis_blocked_clients{{{NODE}}}) > 0", "{{pod}} blocked", "B")],
         "short", min_=0, decimals=0, desc="Client connections per pod (the services' pools, sentinels, replicas, "
         "the exporter); blocked: clients waiting in XREADGROUP BLOCK."), 12, 7)
d.add(ts("Slow log", [vm(f"max by (pod) (redis_slowlog_length{{{NODE}}})", "{{pod}} entries"),
                      vm(f"max by (pod) (redis_last_slow_execution_duration_seconds{{{NODE}}})", "{{pod}} last slow command", "B")],
         "short", min_=0, overrides=[right_axis("B", "s")],
         desc="Commands slower than slowlog-log-slower-than (10ms): entries kept (left) and the duration of the "
              "last one (right). redis-cli SLOWLOG GET shows them."), 12, 7)

d.row("Clients — what the services see")
d.add(ts("Client latency p99 by service and command",
         [vl(f"{SPANS} {SVC} | stats by (service, name) quantile(0.99, duration) p99", "statsRange", ds=VTL,
             legend="{{service}} {{name}}")],
         "ns", ds=VTL, min_=0, desc="99th percentile of the Redis calls as each service sees them (redisotel "
         "spans): includes network and waiting for a pooled connection."), 12, 8)
d.add(ts("Client calls and errors",
         [vl(f"{SPANS} {SVC} | stats by (service) count() calls", "statsRange", ds=VTL, legend="{{service}} calls"),
          vl(f"{SPANS} status_code:=2 {SVC} | stats by (service) count() errors", "statsRange", ref="B", ds=VTL,
             legend="{{service}} errors")],
         "short", ds=VTL, min_=0, overrides=[fixed(".* errors", "red", "byRegexp"), right_axis("B", "short")],
         desc="Redis calls per interval by service (left) and the failed ones (right): connection errors and "
              "READONLY during a failover, timeouts."), 12, 8)
d.add(ts("Connection pools: in use against the pool size",
         [vm('sum by (job, client) (goredis_pool_connections{namespace="shop"} - goredis_pool_idle_connections{namespace="shop"})',
             "{{job}} {{client}} in use"),
          vm('max(goredis_pool_max_connections{namespace="shop"})', "pool size", "B")],
         "short", min_=0, decimals=0, overrides=[fixed("pool size", "red")],
         desc="Connections each service's go-redis pool has busy, against REDIS_POOL_SIZE. The stream consumer "
              "keeps one in XREADGROUP BLOCK."), 8, 7)
d.add(ts("Pool waits and timeouts",
         [vm(f'sum by (job) (rate(goredis_pool_waits_total{{namespace="shop"}}{RI})) > 0', "{{job}} waits"),
          vm(f'sum by (job) (rate(goredis_pool_timeouts_total{{namespace="shop"}}{RI})) > 0', "{{job}} timeouts", "B"),
          vm(f'sum by (job) (rate(goredis_pool_stale_connections_total{{namespace="shop"}}{RI})) > 0', "{{job}} stale closed", "C")],
         "short", min_=0, overrides=[fixed(".* timeouts", "red", "byRegexp")],
         desc="Commands per second that waited for a free connection, failed waiting (RedisClientPoolTimeouts), "
              "and broken connections thrown away (expected right after a failover)."), 8, 7)
d.add(logsql_table("Slowest Redis calls",
                   f'{SPANS} {SVC} | copy "span_attr:db.statement" as statement '
                   "| stats by (trace_id, service, name, statement) max(duration) took | sort by (took desc) | limit 20",
                   [("took", "Took"), ("service", "Service"), ("name", "Command"), ("statement", "Statement"),
                    ("trace_id", "Trace")],
                   "The 20 slowest Redis calls in the time range; open the trace to see what waited.", VTL,
                   overrides=[by_name("Took", [{"id": "unit", "value": "ns"}, {"id": "custom.width", "value": 90}]),
                              by_name("Service", [{"id": "custom.width", "value": 150}]),
                              by_name("Command", [{"id": "custom.width", "value": 90}]),
                              LINKS_ON("Trace", TRACE_LINK, 270)],
                   sort=[{"displayName": "Took", "desc": True}]), 8, 7)

d.row("Notification stream")
d.add(ts("Stream and the notifier group",
         [vm(on_master(f"redis_stream_length{{{NODE}, {STREAM}}}"), "length"),
          vm(on_master(f"redis_stream_group_lag{{{NODE}, {STREAM}}}"), "not yet read (lag)", "B"),
          vm(on_master(f"redis_stream_group_messages_pending{{{NODE}, {STREAM}}}"), "read, not acknowledged", "C"),
          vm(f"sum(rate(redis_stream_entries_added_total{{{NODE}, {STREAM}}}{RI}) * on (pod) group_left () {MASTER_POD})",
             "added / s", "D"),
          vm(f'sum by (stage) (rate(notifications_total{{namespace="shop", stage=~"reclaimed|dropped"}}{RI})) > 0',
             "{{stage}} / s", "E")],
         "short", min_=0, decimals=0, overrides=[right_axis("D", "short"), right_axis("E", "short"),
                                                 fixed("read, not acknowledged", "orange"), fixed("dropped / s", "red")],
         desc="The notifications stream (MAXLEN ~10000, so the length stays near 10000): entries the group has "
              "not read yet, entries delivered but not acknowledged yet, and on the right the enqueue rate and "
              "the entries notification-service reclaimed from a consumer that stopped (after a minute) or "
              "dropped after 5 failed deliveries."), 12, 7)
d.add(table("Consumers",
            [instant(f"max by (consumer) (redis_stream_group_consumer_idle_seconds{{{NODE}, {STREAM}}} "
                     f"* on (pod) group_left () {MASTER_POD})", "A"),
             instant(f"max by (consumer) (redis_stream_group_consumer_messages_pending{{{NODE}, {STREAM}}} "
                     f"* on (pod) group_left () {MASTER_POD})", "B")],
            [{"id": "merge", "options": {}},
             {"id": "organize", "options": {"excludeByName": {"Time": True},
                                            "indexByName": {"consumer": 0, "Value #A": 1, "Value #B": 2},
                                            "renameByName": {"consumer": "Consumer (notification-service pod)",
                                                             "Value #A": "Idle", "Value #B": "Pending"}}}],
            overrides=[by_name("Idle", [{"id": "unit", "value": "s"}, {"id": "custom.width", "value": 110},
                                        {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                        {"id": "thresholds", "value": thresholds(("green", None), ("orange", 60), ("red", 600))}]),
                       by_name("Pending", [{"id": "custom.width", "value": 90}, {"id": "decimals", "value": 0},
                                           {"id": "custom.cellOptions", "value": {"type": "color-text"}},
                                           {"id": "thresholds", "value": thresholds(("green", None), ("red", 1))}])],
            desc="Consumers of the notifier group. A live pod polls every 5s (idle < 5s); a long idle consumer is a "
                 "pod that is gone. Its pending entries are reclaimed by a live pod after a minute, and the "
                 "consumer itself is removed once it has been idle for an hour with nothing pending.",
            sort=[{"displayName": "Idle", "desc": True}]), 12, 7)

d.row("Persistence")
d.add(ts("Append-only file", [vm(f"max by (pod) (redis_aof_current_size_bytes{{{NODE}}})", "{{pod}} size"),
                              vm(f"max by (pod) (redis_aof_rewrite_in_progress{{{NODE}}}) > 0", "{{pod}} rewriting", "B")],
         "bytes", min_=0, overrides=[right_axis("B", "short")],
         desc="AOF size per pod (appendfsync everysec); a rewrite compacts it in a forked child (memory "
              "copy-on-write: RedisContainerMemoryNearLimit)."), 8, 7)
d.add(status_history("AOF health",
                     vm(f"min by (pod) (redis_aof_last_write_status{{{NODE}}} * redis_aof_last_bgrewrite_status{{{NODE}}})",
                        "{{pod}}"),
                     "Whether the last AOF write and the last AOF rewrite of each pod succeeded. A failed write makes "
                     "Redis refuse writes (RedisPersistenceFailing).", thresholds(("red", None), ("green", 1))), 8, 7)
d.panels[-1]["fieldConfig"]["defaults"]["mappings"] = mappings({"1": ("ok", "green"), "0": ("failing", "red")})
d.add(ts("Data volumes", [vm('max by (persistentvolumeclaim) (1 - kubelet_volume_stats_available_bytes{namespace="shop", '
                             'persistentvolumeclaim=~"data-redis-node-[0-9]+"} / kubelet_volume_stats_capacity_bytes'
                             '{namespace="shop", persistentvolumeclaim=~"data-redis-node-[0-9]+"})', "{{persistentvolumeclaim}}")],
         "percentunit", min_=0, max_=1, desc="Used share of each pod's data volume (AOF and its rewrites)."), 8, 7)

d.row("Logs")
# Redis log levels: "#" warning, "*" notice (mostly routine AOF and replication steps: only the ones about
# roles, lost links and errors are kept).
d.add(logs_panel("Redis and Sentinel logs", f"{REDIS_LOGS} (_msg:~\" # \" or _msg:~\"(?i)error|fail|lost|refused|OOM|MASTER MODE|"
                 "REPLICAOF|Connecting to MASTER|unable to PSYNC|Full resync\") "
                 "| format \"<kubernetes.pod_name>/<kubernetes.container_name>: <_msg>\" as _msg",
                 "Warnings of Redis and Sentinel, and notices about roles (MASTER MODE, REPLICAOF), lost links, "
                 "full resyncs and errors; routine AOF and replication steps are left out."), 12, 10)
d.add(logs_panel("go-redis client messages in the services",
                 '{kubernetes.pod_namespace="shop", kubernetes.container_name="app"} _msg:~"(?i)^redis: |sentinel: |'
                 'READONLY|connection pool timeout|MASTERDOWN" '
                 '| format "<kubernetes.pod_name>: <_msg>" as _msg',
                 "What the clients logged: Sentinel discovering a new master, pool timeouts, READONLY writes during "
                 "a failover. go-redis logs through its own logger, so these lines have no service or level field."),
      12, 10)

for p in d.panels:
    if p["type"] == "timeseries" and p["datasource"] in (VL, VTL):
        p["interval"] = "1m"

MASTER_CHANGES = {"name": "Redis master changes", "datasource": VM, "enable": True, "iconColor": "yellow",
                  "step": "1m", "useValueForTime": False,
                  "expr": f"{MASTER_POD} unless {MASTER_POD.replace('role=\"master\"}', 'role=\"master\"} offset 1m')}",
                  "titleFormat": "Redis master: {{pod}}", "textFormat": "Failover (planned during Redis rollouts)"}
shop_redis = dashboard(UID, "Shop — Redis", ["shop", "redis"], d.panels, [],
                       "Redis with Sentinel: master and failover readiness, failovers, replication, memory and "
                       "evictions, server and client latency, connection pools, the notification stream, "
                       "persistence and the Sentinel logs.", "now-6h")
shop_redis["annotations"] = {"list": [MASTER_CHANGES] + ANNOTATIONS}
DASHBOARD = shop_redis
