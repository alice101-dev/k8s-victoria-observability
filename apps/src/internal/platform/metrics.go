package platform

import (
	"net/http"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

// RED metrics for HTTP servers and clients. service/pod/namespace labels are added by
// vmagent from the scrape target (VMServiceScrape), not here.
var (
	latencyBuckets = []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5}

	httpServerRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_server_requests_total",
		Help: "HTTP requests served by method, route and status code.",
	}, []string{"method", "route", "code"})

	httpServerDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_server_request_duration_seconds",
		Help:    "Server-side HTTP request latency.",
		Buckets: latencyBuckets,
	}, []string{"method", "route", "code"})

	httpServerInflight = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "http_server_requests_inflight",
		Help: "HTTP requests currently in flight.",
	})

	httpClientRequests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_client_requests_total",
		Help: "Outgoing HTTP requests to other services by peer, method and status code.",
	}, []string{"peer", "method", "code"})

	httpClientDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_client_request_duration_seconds",
		Help:    "Outgoing HTTP request latency to other services.",
		Buckets: latencyBuckets,
	}, []string{"peer", "method", "code"})
)

// statusRecorder captures the response status code for metric labels.
type statusRecorder struct {
	http.ResponseWriter
	code int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.code = code
	r.ResponseWriter.WriteHeader(code)
}

// Unwrap is used by http.ResponseController (e.g. Flush in ReverseProxy).
func (r *statusRecorder) Unwrap() http.ResponseWriter { return r.ResponseWriter }

// instrument wraps a handler with RED metrics labelled by route (the ServeMux pattern,
// not the raw URL) to keep cardinality low.
func instrument(route string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		httpServerInflight.Inc()
		defer httpServerInflight.Dec()

		rec := &statusRecorder{ResponseWriter: w, code: http.StatusOK}
		next.ServeHTTP(rec, r)

		code := strconv.Itoa(rec.code)
		httpServerRequests.WithLabelValues(r.Method, route, code).Inc()
		httpServerDuration.WithLabelValues(r.Method, route, code).Observe(time.Since(start).Seconds())
	})
}
