package platform

import (
	"context"
	"fmt"
	"hash/fnv"
	"os"
	"time"

	"github.com/exaring/otelpgx"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
)

// Postgres creates a traced pgx pool (every query is a span, pool stats on /metrics) from
// DATABASE_URL (the "uri" key of the CloudNativePG <cluster>-app Secret), waits for the database
// and runs idempotent migrations under an advisory lock (safe with multiple replicas).
func (a *App) Postgres(ctx context.Context, migrations ...string) (*pgxpool.Pool, error) {
	cfg, err := pgxpool.ParseConfig(os.Getenv("DATABASE_URL"))
	if err != nil {
		return nil, fmt.Errorf("parse DATABASE_URL: %w", err)
	}
	cfg.ConnConfig.Tracer = otelpgx.NewTracer()
	cfg.MaxConns = int32(EnvInt("DB_MAX_CONNS", 5))
	cfg.MinConns = 1
	cfg.MaxConnLifetime = 30 * time.Minute
	cfg.MaxConnIdleTime = 5 * time.Minute
	cfg.HealthCheckPeriod = 30 * time.Second

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("create pg pool: %w", err)
	}
	a.OnShutdown(func(context.Context) error { pool.Close(); return nil })
	prometheus.MustRegister(poolCollector{pool})

	if err := a.waitFor(ctx, "postgres", pool.Ping); err != nil {
		return nil, err
	}
	if err := a.migrate(ctx, pool, migrations); err != nil {
		return nil, fmt.Errorf("migrate: %w", err)
	}
	return pool, nil
}

func (a *App) migrate(ctx context.Context, pool *pgxpool.Pool, stmts []string) error {
	if len(stmts) == 0 {
		return nil
	}
	conn, err := pool.Acquire(ctx)
	if err != nil {
		return err
	}
	defer conn.Release()

	h := fnv.New64a()
	_, _ = h.Write([]byte(a.Name))
	lockKey := int64(h.Sum64())

	if _, err := conn.Exec(ctx, "SELECT pg_advisory_lock($1)", lockKey); err != nil {
		return err
	}
	defer func() {
		_, _ = conn.Exec(context.Background(), "SELECT pg_advisory_unlock($1)", lockKey)
	}()

	for _, s := range stmts {
		if _, err := conn.Exec(ctx, s); err != nil {
			return err
		}
	}
	a.Log.Info("schema migrated", "statements", len(stmts))
	return nil
}

// waitFor pings a dependency with exponential backoff until it succeeds or ctx is cancelled;
// Kubernetes does not guarantee start order, so the app may start before its database.
func (a *App) waitFor(ctx context.Context, name string, ping func(context.Context) error) error {
	backoff := time.Second
	for {
		pingCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
		err := ping(pingCtx)
		cancel()
		if err == nil {
			a.Log.Info("dependency ready", "dependency", name)
			return nil
		}
		a.Log.Warn("waiting for dependency", "dependency", name, "err", err, "retry_in", backoff.String())
		select {
		case <-ctx.Done():
			return fmt.Errorf("wait for %s: %w", name, ctx.Err())
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, 15*time.Second)
	}
}
