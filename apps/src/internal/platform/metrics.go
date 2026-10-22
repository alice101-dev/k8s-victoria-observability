package platform

import (
	"net/http"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
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

// Connection pool metrics, read from pgxpool.Stat() at scrape time. Empty acquires had to wait for a
// connection: a pool too small for the load, or connections held too long (slow queries, lock waits).
var (
	poolAcquiredDesc      = prometheus.NewDesc("pgxpool_acquired_connections", "Connections currently in use.", nil, nil)
	poolIdleDesc          = prometheus.NewDesc("pgxpool_idle_connections", "Idle connections in the pool.", nil, nil)
	poolConstructingDesc  = prometheus.NewDesc("pgxpool_constructing_connections", "Connections being opened.", nil, nil)
	poolMaxDesc           = prometheus.NewDesc("pgxpool_max_connections", "Maximum pool size (DB_MAX_CONNS).", nil, nil)
	poolAcquiresDesc      = prometheus.NewDesc("pgxpool_acquires_total", "Successful connection acquisitions.", nil, nil)
	poolAcquireTimeDesc   = prometheus.NewDesc("pgxpool_acquire_duration_seconds_total", "Time spent acquiring connections.", nil, nil)
	poolEmptyAcquiresDesc = prometheus.NewDesc("pgxpool_empty_acquires_total", "Acquisitions that waited because no connection was idle.", nil, nil)
	poolEmptyWaitDesc     = prometheus.NewDesc("pgxpool_empty_acquire_wait_seconds_total", "Time spent waiting in empty acquisitions.", nil, nil)
	poolCanceledDesc      = prometheus.NewDesc("pgxpool_canceled_acquires_total", "Acquisitions abandoned by their context while waiting.", nil, nil)
	poolNewConnsDesc      = prometheus.NewDesc("pgxpool_new_connections_total", "Connections opened.", nil, nil)
)

type poolCollector struct{ pool *pgxpool.Pool }

func (c poolCollector) Describe(ch chan<- *prometheus.Desc) { prometheus.DescribeByCollect(c, ch) }

func (c poolCollector) Collect(ch chan<- prometheus.Metric) {
	s := c.pool.Stat()
	gauge := func(d *prometheus.Desc, v float64) { ch <- prometheus.MustNewConstMetric(d, prometheus.GaugeValue, v) }
	counter := func(d *prometheus.Desc, v float64) {
		ch <- prometheus.MustNewConstMetric(d, prometheus.CounterValue, v)
	}
	gauge(poolAcquiredDesc, float64(s.AcquiredConns()))
	gauge(poolIdleDesc, float64(s.IdleConns()))
	gauge(poolConstructingDesc, float64(s.ConstructingConns()))
	gauge(poolMaxDesc, float64(s.MaxConns()))
	counter(poolAcquiresDesc, float64(s.AcquireCount()))
	counter(poolAcquireTimeDesc, s.AcquireDuration().Seconds())
	counter(poolEmptyAcquiresDesc, float64(s.EmptyAcquireCount()))
	counter(poolEmptyWaitDesc, s.EmptyAcquireWaitTime().Seconds())
	counter(poolCanceledDesc, float64(s.CanceledAcquireCount()))
	counter(poolNewConnsDesc, float64(s.NewConnsCount()))
}
