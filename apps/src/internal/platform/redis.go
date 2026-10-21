package platform

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

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
	a.OnShutdown(func(context.Context) error { return rdb.Close() })

	ping := func(ctx context.Context) error { return rdb.Ping(ctx).Err() }
	if err := a.waitFor(ctx, "redis", ping); err != nil {
		return nil, err
	}
	return rdb, nil
}
