package platform

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"strings"
	"sync/atomic"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

// Server is an HTTP server with instrumented routes, probe endpoints and /metrics.
type Server struct {
	mux   *http.ServeMux
	log   *slog.Logger
	ready atomic.Bool
}

func NewServer(log *slog.Logger) *Server {
	return &Server{mux: http.NewServeMux(), log: log}
}

// Handle registers a route with tracing (span named after the pattern) and RED metrics.
// Patterns use Go 1.22+ ServeMux syntax, e.g. "GET /users/{id}".
func (s *Server) Handle(pattern string, h http.Handler) {
	route := pattern
	if _, path, ok := strings.Cut(pattern, " "); ok {
		route = path
	}
	s.mux.Handle(pattern, otelhttp.NewHandler(instrument(route, h), pattern))
}

func (s *Server) HandleFunc(pattern string, f http.HandlerFunc) {
	s.Handle(pattern, f)
}

// Run serves HTTP until ctx is cancelled, then shuts down gradually: readiness → 503,
// wait for Service endpoints to update, then close the listener and drain requests.
func (s *Server) Run(ctx context.Context, addr string) error {
	s.mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	s.mux.HandleFunc("GET /readyz", func(w http.ResponseWriter, _ *http.Request) {
		if !s.ready.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		w.WriteHeader(http.StatusOK)
	})
	s.mux.Handle("GET /metrics", promhttp.Handler())

	srv := &http.Server{
		Addr:              addr,
		Handler:           s.mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       90 * time.Second,
	}

	errCh := make(chan error, 1)
	go func() {
		s.log.Info("http server listening", "addr", addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			errCh <- err
		}
		close(errCh)
	}()
	s.ready.Store(true)

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
	}

	s.log.Info("shutdown signal received, draining")
	s.ready.Store(false)
	time.Sleep(EnvDuration("SHUTDOWN_DELAY", 5*time.Second))

	shutdownCtx, cancel := context.WithTimeout(context.Background(), EnvDuration("SHUTDOWN_TIMEOUT", 15*time.Second))
	defer cancel()
	return srv.Shutdown(shutdownCtx)
}
