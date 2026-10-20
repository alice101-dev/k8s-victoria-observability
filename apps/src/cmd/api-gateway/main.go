// api-gateway: the single HTTP entry point. Reverse-proxies /api/<resource> to internal
// services, starting the root span and propagating trace context downstream.
package main

import (
	"context"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"

	"shop/internal/platform"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	app := platform.MustNew(ctx, "api-gateway")
	if err := run(ctx, app); err != nil {
		app.Log.Error("service failed", "err", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, app *platform.App) error {
	routes := map[string]string{
		"users":    platform.Env("USER_SERVICE_URL", "http://user-service:8080"),
		"products": platform.Env("PRODUCT_SERVICE_URL", "http://product-service:8080"),
		"orders":   platform.Env("ORDER_SERVICE_URL", "http://order-service:8080"),
	}
	transport := otelhttp.NewTransport(&http.Transport{
		Proxy:                 nil,
		MaxIdleConnsPerHost:   50,
		IdleConnTimeout:       90 * time.Second,
		ResponseHeaderTimeout: platform.EnvDuration("UPSTREAM_TIMEOUT", 10*time.Second),
	})

	for resource, upstream := range routes {
		target, err := url.Parse(upstream)
		if err != nil {
			return err
		}
		proxy := newProxy(app, resource, target, transport)
		// "/api/users" (exact) and "/api/users/" (subtree) → /users...
		app.Server.Handle("/api/"+resource, proxy)
		app.Server.Handle("/api/"+resource+"/", proxy)
	}
	return app.Run(ctx)
}

func newProxy(app *platform.App, resource string, target *url.URL, transport http.RoundTripper) http.Handler {
	return &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(target)
			pr.Out.URL.Path = strings.TrimPrefix(pr.In.URL.Path, "/api")
			pr.Out.URL.RawPath = ""
			pr.SetXForwarded()
		},
		Transport: transport,
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			app.Log.ErrorContext(r.Context(), "upstream request failed", "upstream", resource, "path", r.URL.Path, "err", err)
			platform.WriteError(w, http.StatusBadGateway, "upstream unavailable")
		},
	}
}
