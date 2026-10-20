package platform

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/redis/go-redis/extra/redisotel/v9"
	"github.com/redis/go-redis/v9"
)

// Redis creates a traced client (every command is a span) from REDIS_ADDR and REDIS_PASSWORD
// and waits until Redis is reachable.
func (a *App) Redis(ctx context.Context) (*redis.Client, error) {
	rdb := redis.NewClient(&redis.Options{
		Addr:         Env("REDIS_ADDR", "redis:6379"),
		Password:     os.Getenv("REDIS_PASSWORD"),
		DialTimeout:  2 * time.Second,
		ReadTimeout:  time.Second,
		WriteTimeout: time.Second,
		PoolSize:     EnvInt("REDIS_POOL_SIZE", 10),
	})
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
