// payment-service: simulated payment provider (random latency and declines) with idempotency
// keys in Redis and a ledger in PostgreSQL.
package main

import (
	"context"
	"math/rand/v2"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"

	"shop/internal/platform"
)

var migrations = []string{
	`CREATE TABLE IF NOT EXISTS payments (
		id           BIGSERIAL PRIMARY KEY,
		order_ref    TEXT NOT NULL UNIQUE,
		user_id      BIGINT NOT NULL,
		amount_cents BIGINT NOT NULL,
		status       TEXT NOT NULL,
		created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
	)`,
}

var paymentsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "payments_total",
	Help: "Payments by status (approved/declined/duplicate).",
}, []string{"status"})

type paymentRequest struct {
	OrderRef    string `json:"order_ref"`
	UserID      int64  `json:"user_id"`
	AmountCents int64  `json:"amount_cents"`
}

type Payment struct {
	ID          int64  `json:"id"`
	OrderRef    string `json:"order_ref"`
	AmountCents int64  `json:"amount_cents"`
	Status      string `json:"status"`
}

type handler struct {
	app         *platform.App
	db          *pgxpool.Pool
	rdb         *redis.Client
	failureRate float64
	maxLatency  time.Duration
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	app := platform.MustNew(ctx, "payment-service")
	if err := run(ctx, app); err != nil {
		app.Log.Error("service failed", "err", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, app *platform.App) error {
	db, err := app.Postgres(ctx, migrations...)
	if err != nil {
		return err
	}
	rdb, err := app.Redis(ctx)
	if err != nil {
		return err
	}

	h := &handler{
		app:         app,
		db:          db,
		rdb:         rdb,
		failureRate: platform.EnvFloat("PAYMENT_FAILURE_RATE", 0.1),
		maxLatency:  platform.EnvDuration("PAYMENT_MAX_LATENCY", 400*time.Millisecond),
	}
	app.Server.HandleFunc("POST /payments", h.charge)
	return app.Run(ctx)
}

func (h *handler) charge(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var in paymentRequest
	if err := platform.ReadJSON(r, &in); err != nil || in.OrderRef == "" || in.AmountCents <= 0 {
		platform.WriteError(w, http.StatusBadRequest, "order_ref and positive amount_cents are required")
		return
	}

	// Idempotency: an order_ref is processed once, so order-service retries are safe.
	fresh, err := h.rdb.SetNX(ctx, "payment:idem:"+in.OrderRef, 1, time.Hour).Result()
	if err != nil {
		h.app.Log.ErrorContext(ctx, "idempotency check failed", "order_ref", in.OrderRef, "err", err)
		platform.WriteError(w, http.StatusServiceUnavailable, "idempotency store unavailable")
		return
	}
	if !fresh {
		paymentsTotal.WithLabelValues("duplicate").Inc()
		h.app.Log.WarnContext(ctx, "duplicate payment request", "order_ref", in.OrderRef)
		platform.WriteError(w, http.StatusConflict, "duplicate payment")
		return
	}

	status := h.callProvider(ctx, in)

	var p Payment
	err = h.db.QueryRow(ctx,
		`INSERT INTO payments (order_ref, user_id, amount_cents, status) VALUES ($1, $2, $3, $4)
		 RETURNING id, order_ref, amount_cents, status`,
		in.OrderRef, in.UserID, in.AmountCents, status).
		Scan(&p.ID, &p.OrderRef, &p.AmountCents, &p.Status)
	if err != nil {
		// Release the idempotency key so a retry can be processed.
		if delErr := h.rdb.Del(ctx, "payment:idem:"+in.OrderRef).Err(); delErr != nil {
			h.app.Log.WarnContext(ctx, "release idempotency key failed", "order_ref", in.OrderRef, "err", delErr)
		}
		h.app.Log.ErrorContext(ctx, "persist payment failed", "order_ref", in.OrderRef, "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}

	paymentsTotal.WithLabelValues(status).Inc()
	if status == "declined" {
		h.app.Log.WarnContext(ctx, "payment declined", "order_ref", in.OrderRef, "amount_cents", in.AmountCents)
		platform.WriteJSON(w, http.StatusPaymentRequired, p)
		return
	}
	h.app.Log.InfoContext(ctx, "payment approved", "order_ref", in.OrderRef, "amount_cents", in.AmountCents)
	platform.WriteJSON(w, http.StatusCreated, p)
}

// callProvider simulates the external payment gateway call as a child span.
func (h *handler) callProvider(ctx context.Context, in paymentRequest) string {
	_, span := otel.Tracer("payment-service").Start(ctx, "payment-provider.authorize")
	defer span.End()
	span.SetAttributes(
		attribute.String("payment.order_ref", in.OrderRef),
		attribute.Int64("payment.amount_cents", in.AmountCents),
	)

	time.Sleep(time.Duration(rand.Int64N(int64(h.maxLatency) + 1)))

	if rand.Float64() < h.failureRate {
		span.SetStatus(codes.Error, "card declined")
		span.SetAttributes(attribute.String("payment.status", "declined"))
		return "declined"
	}
	span.SetAttributes(attribute.String("payment.status", "approved"))
	return "approved"
}
