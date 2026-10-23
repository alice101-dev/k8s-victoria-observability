package platform

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/redis/go-redis/extra/redisotel/v9"
	"github.com/redis/go-redis/v9"
)

// Redis creates a traced client (every command is a span) and waits until Redis is reachable.
// With REDIS_SENTINEL_ADDRS set, the master is discovered through Sentinel (REDIS_MASTER_NAME)
// and the client follows failovers; otherwise it connects to REDIS_ADDR directly.
func (a *App) Redis(ctx context.Context) (*redis.Client, error) {
	password := os.Getenv("REDIS_PASSWORD")
	var rdb *redis.Client
	if sentinels := Env("REDIS_SENTINEL_ADDRS", ""); sentinels != "" {
		rdb = redis.NewFailoverClient(&redis.FailoverOptions{
			MasterName:       Env("REDIS_MASTER_NAME", "shop"),
			SentinelAddrs:    strings.Split(sentinels, ","),
			SentinelPassword: password,
			Password:         password,
			DialTimeout:      2 * time.Second,
			ReadTimeout:      time.Second,
			WriteTimeout:     time.Second,
			PoolSize:         EnvInt("REDIS_POOL_SIZE", 10),
		})
	} else {
		rdb = redis.NewClient(&redis.Options{
			Addr:         Env("REDIS_ADDR", "redis:6379"),
			Password:     password,
			DialTimeout:  2 * time.Second,
			ReadTimeout:  time.Second,
			WriteTimeout: time.Second,
			PoolSize:     EnvInt("REDIS_POOL_SIZE", 10),
		})
	}
	if err := redisotel.InstrumentTracing(rdb); err != nil {
		return nil, fmt.Errorf("instrument redis: %w", err)
	}
	RegisterRedisPool("default", rdb)
	a.OnShutdown(func(context.Context) error { return rdb.Close() })

	ping := func(ctx context.Context) error { return rdb.Ping(ctx).Err() }
	if err := a.waitFor(ctx, "redis", ping); err != nil {
		return nil, err
	}
	return rdb, nil
}

// Client connection pool metrics, read from PoolStats() at scrape time (goredis_pool_*, not redis_*: those are
// the server's, from redis_exporter). Timeouts: a command found no free connection within PoolTimeout and
// failed. Waits: it had to wait for one (pool too small for the load, or slow commands holding connections).
var (
	redisPoolLabels       = []string{"client"}
	redisPoolTotalDesc    = prometheus.NewDesc("goredis_pool_connections", "Open connections.", redisPoolLabels, nil)
	redisPoolIdleDesc     = prometheus.NewDesc("goredis_pool_idle_connections", "Idle connections.", redisPoolLabels, nil)
	redisPoolMaxDesc      = prometheus.NewDesc("goredis_pool_max_connections", "Pool size (REDIS_POOL_SIZE).", redisPoolLabels, nil)
	redisPoolPendingDesc  = prometheus.NewDesc("goredis_pool_pending_requests", "Commands waiting for a connection.", redisPoolLabels, nil)
	redisPoolHitsDesc     = prometheus.NewDesc("goredis_pool_hits_total", "Commands that found a free connection.", redisPoolLabels, nil)
	redisPoolMissesDesc   = prometheus.NewDesc("goredis_pool_misses_total", "Commands that found no free connection and opened or waited for one.", redisPoolLabels, nil)
	redisPoolWaitsDesc    = prometheus.NewDesc("goredis_pool_waits_total", "Commands that waited for a connection.", redisPoolLabels, nil)
	redisPoolWaitTimeDesc = prometheus.NewDesc("goredis_pool_wait_duration_seconds_total", "Time spent waiting for connections.", redisPoolLabels, nil)
	redisPoolTimeoutsDesc = prometheus.NewDesc("goredis_pool_timeouts_total", "Commands that failed waiting for a connection.", redisPoolLabels, nil)
	redisPoolStaleDesc    = prometheus.NewDesc("goredis_pool_stale_connections_total", "Connections closed as stale or broken.", redisPoolLabels, nil)
)

type redisPoolCollector struct {
	client string
	rdb    *redis.Client
}

// RegisterRedisPool exposes the pool metrics of rdb, labelled client (several clients per process are allowed).
func RegisterRedisPool(client string, rdb *redis.Client) {
	prometheus.MustRegister(redisPoolCollector{client: client, rdb: rdb})
}

func (c redisPoolCollector) Describe(ch chan<- *prometheus.Desc) { prometheus.DescribeByCollect(c, ch) }

func (c redisPoolCollector) Collect(ch chan<- prometheus.Metric) {
	s := c.rdb.PoolStats()
	gauge := func(d *prometheus.Desc, v float64) {
		ch <- prometheus.MustNewConstMetric(d, prometheus.GaugeValue, v, c.client)
	}
	counter := func(d *prometheus.Desc, v float64) {
		ch <- prometheus.MustNewConstMetric(d, prometheus.CounterValue, v, c.client)
	}
	gauge(redisPoolTotalDesc, float64(s.TotalConns))
	gauge(redisPoolIdleDesc, float64(s.IdleConns))
	gauge(redisPoolMaxDesc, float64(c.rdb.Options().PoolSize))
	gauge(redisPoolPendingDesc, float64(s.PendingRequests))
	counter(redisPoolHitsDesc, float64(s.Hits))
	counter(redisPoolMissesDesc, float64(s.Misses))
	counter(redisPoolWaitsDesc, float64(s.WaitCount))
	counter(redisPoolWaitTimeDesc, time.Duration(s.WaitDurationNs).Seconds())
	counter(redisPoolTimeoutsDesc, float64(s.Timeouts))
	counter(redisPoolStaleDesc, float64(s.StaleConns))
}
