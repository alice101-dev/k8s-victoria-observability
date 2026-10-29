from .common import RI, Dash, dashboard, stat, thresholds, ts, vm

FILE = "platform-redis.json"
UID = "platform-redis"

d = Dash()
HITS = "rate(redis_keyspace_hits_total[5m])"
MISSES = "rate(redis_keyspace_misses_total[5m])"

d.row("Overview")
for p in (
    stat("Up", "min(redis_up) or vector(0)", "short", thresholds(("red", None), ("green", 1)),
         "Every Redis instance answers the exporter.", decimals=0),
    stat("Memory of maxmemory", "max(redis_memory_used_bytes / redis_memory_max_bytes)", "percentunit",
         thresholds(("green", None), ("orange", 0.75), ("red", 0.9)),
         "Memory in use against maxmemory. At maxmemory, keys with a TTL are evicted first (volatile-lru)."),
    stat("Connected clients", "sum(redis_connected_clients)", "short", thresholds(("blue", None)),
         "Client connections: one per service pod that uses the cache, plus the exporter.", decimals=0),
    stat("Commands / s", "sum(rate(redis_commands_processed_total[5m]))", "ops", thresholds(("blue", None)),
         "Commands Redis executed per second."),
    stat("Keyspace hit ratio", f"sum({HITS}) / (sum({HITS}) + sum({MISSES}))", "percentunit",
         thresholds(("orange", None), ("green", 0.8)),
         "Reads that found their key. Session lookups for unknown tokens and cold caches count as misses."),
    stat("Evicted keys", "sum(increase(redis_evicted_keys_total[$__range])) or vector(0)", "short",
         thresholds(("green", None), ("orange", 1)),
         "Keys dropped because maxmemory was reached, in the time range. Sessions and cache entries removed early.",
         decimals=0),
):
    d.add(p, 4, 4)

d.row("Commands")
d.add(ts("Commands by name", [vm(f"sum by (cmd) (rate(redis_commands_total{RI}))", "{{cmd}}")], "ops", stack=True,
         desc="GET and SET for cached reads, GETDEL and EVAL for sessions, verifications and rate limits."), 12, 8)
d.add(ts("Average duration by command",
         [vm(f"sum by (cmd) (rate(redis_commands_duration_seconds_total{RI})) / sum by (cmd) (rate(redis_commands_total{RI}))",
             "{{cmd}}")], "s",
         desc="Time Redis spends executing each command, without the network. The client side is in Deus — Service, "
              "Cache."), 12, 8)

d.row("Memory and keys")
d.add(ts("Memory", [vm("sum by (pod) (redis_memory_used_bytes)", "used {{pod}}"),
                    vm("max by (pod) (redis_memory_max_bytes)", "maxmemory {{pod}}", "B")], "bytes", min_=0,
         desc="Used memory against maxmemory."), 8, 8)
d.add(ts("Keys", [vm("sum by (db) (redis_db_keys)", "keys {{db}}"),
                  vm("sum by (db) (redis_db_keys_expiring)", "with TTL {{db}}", "B")], "short", min_=0, decimals=0,
         desc="Keys per database. Every key the services write has a TTL, so both lines should match."), 8, 8)
d.add(ts("Expired and evicted keys", [vm(f"sum(rate(redis_expired_keys_total{RI}))", "expired"),
                                      vm(f"sum(rate(redis_evicted_keys_total{RI}))", "evicted", "B")], "ops", min_=0,
         desc="Expired: their TTL ran out (normal). Evicted: removed under memory pressure (raise maxmemory)."), 8, 8)

d.row("Network and persistence")
d.add(ts("Network", [vm(f"sum(rate(redis_net_input_bytes_total{RI}))", "in"),
                     vm(f"sum(rate(redis_net_output_bytes_total{RI}))", "out", "B")], "Bps", min_=0), 12, 7)
d.add(ts("Append-only file", [vm("max by (pod) (redis_aof_current_size_bytes)", "AOF size {{pod}}"),
                              vm("max by (pod) (redis_aof_last_write_status)", "last write ok {{pod}}", "B")],
         "bytes", min_=0, desc="The append-only file keeps sessions across restarts. A last write status of 0 means "
                               "Redis could not write to its volume."), 12, 7)

DASHBOARD = dashboard(UID, "Platform — Redis", ["platform", "redis"], d.panels, [],
                      "The Redis instance behind sessions, login rate limits and cached reads: availability, memory "
                      "against maxmemory, commands, hit ratio, evictions and persistence (redis_exporter).")
