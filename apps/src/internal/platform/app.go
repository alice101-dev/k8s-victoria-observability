// Package platform is the shared service scaffolding: JSON logging with trace IDs,
// OpenTelemetry tracing, Prometheus metrics, an HTTP server with graceful shutdown,
// and traced PostgreSQL/Redis/HTTP clients.
package platform

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"os"
	"time"
)

// App bundles the logger, HTTP server and resources to close on shutdown.
type App struct {
	Name    string
	Log     *slog.Logger
	Server  *Server
	closers []func(context.Context) error
}

// MustNew creates the App and global tracing; it exits if tracing cannot be set up.
func MustNew(ctx context.Context, name string) *App {
	log := NewLogger(name)
	slog.SetDefault(log)

	shutdownTracing, err := InitTracing(ctx, name)
	if err != nil {
		log.Error("init tracing failed", "err", err)
		os.Exit(1)
	}

	app := &App{Name: name, Log: log, Server: NewServer(log)}
	app.OnShutdown(shutdownTracing)
	return app
}

// OnShutdown registers a cleanup func; cleanups run in reverse order after the server stops.
func (a *App) OnShutdown(f func(context.Context) error) {
	a.closers = append(a.closers, f)
}

// Run serves HTTP until ctx is cancelled (SIGTERM), then closes all resources.
func (a *App) Run(ctx context.Context) error {
	runErr := a.Server.Run(ctx, Env("HTTP_ADDR", ":8080"))

	// Separate context: the main ctx is already cancelled at this point.
	closeCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	var errs []error
	if runErr != nil {
		errs = append(errs, fmt.Errorf("http server: %w", runErr))
	}
	for i := len(a.closers) - 1; i >= 0; i-- {
		if err := a.closers[i](closeCtx); err != nil {
			errs = append(errs, err)
		}
	}
	a.Log.Info("service stopped")
	return errors.Join(errs...)
}
