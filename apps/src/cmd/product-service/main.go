// product-service: product catalog and stock in PostgreSQL, cached in Redis. Provides
// stock reserve/release for checkouts (including compensation when payment fails).
package main

import (
	"context"
	"encoding/json"
	"errors"
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
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"

	"shop/internal/platform"
)

const cacheTTL = 30 * time.Second

var migrations = []string{
	`CREATE TABLE IF NOT EXISTS products (
		id          BIGSERIAL PRIMARY KEY,
		name        TEXT NOT NULL,
		price_cents BIGINT NOT NULL CHECK (price_cents > 0),
		stock       INT NOT NULL CHECK (stock >= 0)
	)`,
	`INSERT INTO products (name, price_cents, stock)
	 SELECT 'Product ' || g, (g * 1500)::bigint, 1000 FROM generate_series(1, 20) g
	 WHERE NOT EXISTS (SELECT 1 FROM products)`,
}

var stockReservations = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "product_stock_reservations_total",
	Help: "Stock reservation attempts by result (reserved/out_of_stock/released).",
}, []string{"result"})

type Product struct {
	ID         int64  `json:"id"`
	Name       string `json:"name"`
	PriceCents int64  `json:"price_cents"`
	Stock      int    `json:"stock"`
}

type handler struct {
	app *platform.App
	db  *pgxpool.Pool
	rdb *redis.Client
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	app := platform.MustNew(ctx, "product-service")
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

	h := &handler{app: app, db: db, rdb: rdb}
	app.Server.HandleFunc("GET /products", h.list)
	app.Server.HandleFunc("GET /products/{id}", h.get)
	app.Server.HandleFunc("POST /products/{id}/reserve", h.reserve)
	app.Server.HandleFunc("POST /products/{id}/release", h.release)

	go h.restockLoop(ctx, platform.EnvDuration("RESTOCK_INTERVAL", 2*time.Minute))
	return app.Run(ctx)
}

func cacheKey(id int64) string { return "product:" + strconv.FormatInt(id, 10) }

func (h *handler) get(w http.ResponseWriter, r *http.Request) {
	id, err := platform.PathInt64(r, "id")
	if err != nil {
		platform.WriteError(w, http.StatusBadRequest, err.Error())
		return
	}
	ctx := r.Context()

	if cached, err := h.rdb.Get(ctx, cacheKey(id)).Bytes(); err == nil {
		var p Product
		if json.Unmarshal(cached, &p) == nil {
			platform.WriteJSON(w, http.StatusOK, p)
			return
		}
	} else if !errors.Is(err, redis.Nil) {
		h.app.Log.WarnContext(ctx, "redis get failed", "err", err)
	}

	var p Product
	err = h.db.QueryRow(ctx, `SELECT id, name, price_cents, stock FROM products WHERE id = $1`, id).
		Scan(&p.ID, &p.Name, &p.PriceCents, &p.Stock)
	if errors.Is(err, pgx.ErrNoRows) {
		platform.WriteError(w, http.StatusNotFound, "product not found")
		return
	}
	if err != nil {
		h.app.Log.ErrorContext(ctx, "query product failed", "product_id", id, "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	if b, err := json.Marshal(p); err == nil {
		if err := h.rdb.Set(ctx, cacheKey(id), b, cacheTTL).Err(); err != nil {
			h.app.Log.WarnContext(ctx, "redis set failed", "err", err)
		}
	}
	platform.WriteJSON(w, http.StatusOK, p)
}

func (h *handler) list(w http.ResponseWriter, r *http.Request) {
	rows, err := h.db.Query(r.Context(), `SELECT id, name, price_cents, stock FROM products ORDER BY id`)
	if err != nil {
		h.app.Log.ErrorContext(r.Context(), "list products failed", "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	products, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (Product, error) {
		var p Product
		err := row.Scan(&p.ID, &p.Name, &p.PriceCents, &p.Stock)
		return p, err
	})
	if err != nil {
		h.app.Log.ErrorContext(r.Context(), "scan products failed", "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	platform.WriteJSON(w, http.StatusOK, products)
}

type quantityRequest struct {
	Quantity int `json:"quantity"`
}

func (h *handler) reserve(w http.ResponseWriter, r *http.Request) {
	id, in, ok := h.parseQuantity(w, r)
	if !ok {
		return
	}
	ctx := r.Context()

	// Conditional UPDATE = atomic reservation, no race between replicas.
	var remaining int
	err := h.db.QueryRow(ctx,
		`UPDATE products SET stock = stock - $1 WHERE id = $2 AND stock >= $1 RETURNING stock`,
		in.Quantity, id).Scan(&remaining)
	if errors.Is(err, pgx.ErrNoRows) {
		stockReservations.WithLabelValues("out_of_stock").Inc()
		h.app.Log.WarnContext(ctx, "stock reservation rejected", "product_id", id, "quantity", in.Quantity)
		platform.WriteError(w, http.StatusConflict, "insufficient stock")
		return
	}
	if err != nil {
		h.app.Log.ErrorContext(ctx, "reserve stock failed", "product_id", id, "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	h.invalidate(ctx, id)
	stockReservations.WithLabelValues("reserved").Inc()
	h.app.Log.InfoContext(ctx, "stock reserved", "product_id", id, "quantity", in.Quantity, "remaining", remaining)
	platform.WriteJSON(w, http.StatusOK, map[string]int{"remaining": remaining})
}

func (h *handler) release(w http.ResponseWriter, r *http.Request) {
	id, in, ok := h.parseQuantity(w, r)
	if !ok {
		return
	}
	ctx := r.Context()
	if _, err := h.db.Exec(ctx, `UPDATE products SET stock = stock + $1 WHERE id = $2`, in.Quantity, id); err != nil {
		h.app.Log.ErrorContext(ctx, "release stock failed", "product_id", id, "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	h.invalidate(ctx, id)
	stockReservations.WithLabelValues("released").Inc()
	h.app.Log.InfoContext(ctx, "stock released (compensation)", "product_id", id, "quantity", in.Quantity)
	w.WriteHeader(http.StatusNoContent)
}

func (h *handler) parseQuantity(w http.ResponseWriter, r *http.Request) (int64, quantityRequest, bool) {
	var in quantityRequest
	id, err := platform.PathInt64(r, "id")
	if err != nil {
		platform.WriteError(w, http.StatusBadRequest, err.Error())
		return 0, in, false
	}
	if err := platform.ReadJSON(r, &in); err != nil || in.Quantity <= 0 || in.Quantity > 100 {
		platform.WriteError(w, http.StatusBadRequest, "quantity must be 1..100")
		return 0, in, false
	}
	return id, in, true
}

func (h *handler) invalidate(ctx context.Context, id int64) {
	if err := h.rdb.Del(ctx, cacheKey(id)).Err(); err != nil {
		h.app.Log.WarnContext(ctx, "redis invalidate failed", "product_id", id, "err", err)
	}
}

// restockLoop periodically refills low stock so demo traffic keeps flowing.
// Each iteration is its own root span (background job tracing).
func (h *handler) restockLoop(ctx context.Context, every time.Duration) {
	tracer := otel.Tracer("product-service")
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
		}
		spanCtx, span := tracer.Start(ctx, "restock")
		tag, err := h.db.Exec(spanCtx, `UPDATE products SET stock = 1000 WHERE stock < 50`)
		if err != nil {
			span.RecordError(err)
			h.app.Log.ErrorContext(spanCtx, "restock failed", "err", err)
		} else if tag.RowsAffected() > 0 {
			h.app.Log.InfoContext(spanCtx, "products restocked", "count", tag.RowsAffected())
			// Cached entries expire on their own (short TTL).
		}
		span.End()
	}
}
