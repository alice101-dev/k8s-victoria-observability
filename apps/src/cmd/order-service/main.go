// order-service: checkout orchestration (simple saga): validate user → fetch price →
// reserve stock → store order → charge payment (on failure: release stock) → notify.
// One checkout produces a trace across 5 services plus PostgreSQL and Redis.
package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/trace"

	"shop/internal/platform"
)

var migrations = []string{
	`CREATE TABLE IF NOT EXISTS orders (
		id           BIGSERIAL PRIMARY KEY,
		user_id      BIGINT NOT NULL,
		product_id   BIGINT NOT NULL,
		quantity     INT NOT NULL,
		amount_cents BIGINT NOT NULL,
		status       TEXT NOT NULL,
		created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
		updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
	)`,
	`CREATE INDEX IF NOT EXISTS orders_user_id_idx ON orders (user_id)`,
}

var ordersTotal = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "orders_total",
	Help: "Checkouts by outcome (confirmed/payment_failed/out_of_stock/invalid_user/invalid_product/error).",
}, []string{"status"})

type Order struct {
	ID          int64     `json:"id"`
	UserID      int64     `json:"user_id"`
	ProductID   int64     `json:"product_id"`
	Quantity    int       `json:"quantity"`
	AmountCents int64     `json:"amount_cents"`
	Status      string    `json:"status"`
	CreatedAt   time.Time `json:"created_at"`
}

type product struct {
	ID         int64  `json:"id"`
	Name       string `json:"name"`
	PriceCents int64  `json:"price_cents"`
}

type handler struct {
	app          *platform.App
	db           *pgxpool.Pool
	users        *platform.Client
	products     *platform.Client
	payments     *platform.Client
	notification *platform.Client
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	app := platform.MustNew(ctx, "order-service")
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

	h := &handler{
		app:          app,
		db:           db,
		users:        platform.NewClient("user-service", platform.Env("USER_SERVICE_URL", "http://user-service:8080")),
		products:     platform.NewClient("product-service", platform.Env("PRODUCT_SERVICE_URL", "http://product-service:8080")),
		payments:     platform.NewClient("payment-service", platform.Env("PAYMENT_SERVICE_URL", "http://payment-service:8080")),
		notification: platform.NewClient("notification-service", platform.Env("NOTIFICATION_SERVICE_URL", "http://notification-service:8080")),
	}
	app.Server.HandleFunc("POST /orders", h.create)
	app.Server.HandleFunc("GET /orders/{id}", h.get)
	return app.Run(ctx)
}

type createOrderRequest struct {
	UserID    int64 `json:"user_id"`
	ProductID int64 `json:"product_id"`
	Quantity  int   `json:"quantity"`
}

func (h *handler) create(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var in createOrderRequest
	if err := platform.ReadJSON(r, &in); err != nil || in.UserID <= 0 || in.ProductID <= 0 || in.Quantity <= 0 {
		platform.WriteError(w, http.StatusBadRequest, "user_id, product_id and quantity are required")
		return
	}
	trace.SpanFromContext(ctx).SetAttributes(
		attribute.Int64("order.user_id", in.UserID),
		attribute.Int64("order.product_id", in.ProductID),
		attribute.Int("order.quantity", in.Quantity),
	)
	log := h.app.Log.With("user_id", in.UserID, "product_id", in.ProductID, "quantity", in.Quantity)

	// 1. Validate the user.
	if err := h.users.DoJSON(ctx, http.MethodGet, "/users/"+strconv.FormatInt(in.UserID, 10), nil, nil); err != nil {
		if statusIs(err, http.StatusNotFound) {
			ordersTotal.WithLabelValues("invalid_user").Inc()
			log.WarnContext(ctx, "checkout rejected: unknown user")
			platform.WriteError(w, http.StatusUnprocessableEntity, "unknown user")
			return
		}
		h.fail(ctx, w, "validate user", err, http.StatusBadGateway)
		return
	}

	// 2. Fetch the product price.
	var p product
	if err := h.products.DoJSON(ctx, http.MethodGet, "/products/"+strconv.FormatInt(in.ProductID, 10), nil, &p); err != nil {
		if statusIs(err, http.StatusNotFound) {
			ordersTotal.WithLabelValues("invalid_product").Inc()
			platform.WriteError(w, http.StatusUnprocessableEntity, "unknown product")
			return
		}
		h.fail(ctx, w, "get product", err, http.StatusBadGateway)
		return
	}
	amount := p.PriceCents * int64(in.Quantity)

	// 3. Reserve stock.
	qty := map[string]int{"quantity": in.Quantity}
	reservePath := fmt.Sprintf("/products/%d/reserve", in.ProductID)
	if err := h.products.DoJSON(ctx, http.MethodPost, reservePath, qty, nil); err != nil {
		if statusIs(err, http.StatusConflict) {
			ordersTotal.WithLabelValues("out_of_stock").Inc()
			log.WarnContext(ctx, "checkout rejected: out of stock")
			platform.WriteError(w, http.StatusConflict, "out of stock")
			return
		}
		h.fail(ctx, w, "reserve stock", err, http.StatusBadGateway)
		return
	}

	// 4. Store the order (pending).
	var o Order
	err := h.db.QueryRow(ctx,
		`INSERT INTO orders (user_id, product_id, quantity, amount_cents, status)
		 VALUES ($1, $2, $3, $4, 'pending')
		 RETURNING id, user_id, product_id, quantity, amount_cents, status, created_at`,
		in.UserID, in.ProductID, in.Quantity, amount).
		Scan(&o.ID, &o.UserID, &o.ProductID, &o.Quantity, &o.AmountCents, &o.Status, &o.CreatedAt)
	if err != nil {
		h.releaseStock(ctx, in.ProductID, in.Quantity)
		h.fail(ctx, w, "insert order", err, http.StatusInternalServerError)
		return
	}
	trace.SpanFromContext(ctx).SetAttributes(attribute.Int64("order.id", o.ID))
	log = log.With("order_id", o.ID)

	// 5. Charge the payment.
	payment := map[string]any{"order_ref": fmt.Sprintf("ord-%d", o.ID), "user_id": in.UserID, "amount_cents": amount}
	if err := h.payments.DoJSON(ctx, http.MethodPost, "/payments", payment, nil); err != nil {
		// Compensate: release stock and mark the order as failed.
		h.releaseStock(ctx, in.ProductID, in.Quantity)
		h.setStatus(ctx, o.ID, "payment_failed")
		if statusIs(err, http.StatusPaymentRequired) {
			ordersTotal.WithLabelValues("payment_failed").Inc()
			log.WarnContext(ctx, "checkout failed: payment declined")
			platform.WriteError(w, http.StatusPaymentRequired, "payment declined")
			return
		}
		h.fail(ctx, w, "charge payment", err, http.StatusBadGateway)
		return
	}
	h.setStatus(ctx, o.ID, "confirmed")
	o.Status = "confirmed"

	// 6. Notify — best effort, failures do not cancel the order.
	note := map[string]any{"user_id": in.UserID, "channel": "email", "message": fmt.Sprintf("Order #%d confirmed", o.ID)}
	if err := h.notification.DoJSON(ctx, http.MethodPost, "/notifications", note, nil); err != nil {
		log.WarnContext(ctx, "send notification failed", "err", err)
	}

	ordersTotal.WithLabelValues("confirmed").Inc()
	log.InfoContext(ctx, "order confirmed", "amount_cents", amount)
	platform.WriteJSON(w, http.StatusCreated, o)
}

func (h *handler) get(w http.ResponseWriter, r *http.Request) {
	id, err := platform.PathInt64(r, "id")
	if err != nil {
		platform.WriteError(w, http.StatusBadRequest, err.Error())
		return
	}
	var o Order
	err = h.db.QueryRow(r.Context(),
		`SELECT id, user_id, product_id, quantity, amount_cents, status, created_at FROM orders WHERE id = $1`, id).
		Scan(&o.ID, &o.UserID, &o.ProductID, &o.Quantity, &o.AmountCents, &o.Status, &o.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		platform.WriteError(w, http.StatusNotFound, "order not found")
		return
	}
	if err != nil {
		h.app.Log.ErrorContext(r.Context(), "query order failed", "order_id", id, "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	platform.WriteJSON(w, http.StatusOK, o)
}

func (h *handler) releaseStock(ctx context.Context, productID int64, quantity int) {
	path := fmt.Sprintf("/products/%d/release", productID)
	if err := h.products.DoJSON(ctx, http.MethodPost, path, map[string]int{"quantity": quantity}, nil); err != nil {
		h.app.Log.ErrorContext(ctx, "compensation failed: release stock", "product_id", productID, "err", err)
	}
}

func (h *handler) setStatus(ctx context.Context, orderID int64, status string) {
	if _, err := h.db.Exec(ctx, `UPDATE orders SET status = $1, updated_at = now() WHERE id = $2`, status, orderID); err != nil {
		h.app.Log.ErrorContext(ctx, "update order status failed", "order_id", orderID, "status", status, "err", err)
	}
}

// fail records an unexpected error and replies with code: 502 for downstream failures,
// 500 for internal ones.
func (h *handler) fail(ctx context.Context, w http.ResponseWriter, step string, err error, code int) {
	ordersTotal.WithLabelValues("error").Inc()
	trace.SpanFromContext(ctx).RecordError(err)
	h.app.Log.ErrorContext(ctx, "checkout failed", "step", step, "err", err)
	platform.WriteError(w, code, step+" failed")
}

func statusIs(err error, code int) bool {
	var se *platform.StatusError
	return errors.As(err, &se) && se.Code == code
}
