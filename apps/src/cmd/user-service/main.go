// user-service: users in PostgreSQL with a read-through Redis cache.
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
	"github.com/redis/go-redis/v9"

	"shop/internal/platform"
)

const cacheTTL = 60 * time.Second

var migrations = []string{
	`CREATE TABLE IF NOT EXISTS users (
		id         BIGSERIAL PRIMARY KEY,
		name       TEXT NOT NULL,
		email      TEXT NOT NULL UNIQUE,
		created_at TIMESTAMPTZ NOT NULL DEFAULT now()
	)`,
	// Seed 50 demo users once (empty table only).
	`INSERT INTO users (name, email)
	 SELECT 'User ' || g, 'user' || g || '@shop.local' FROM generate_series(1, 50) g
	 WHERE NOT EXISTS (SELECT 1 FROM users)`,
}

type User struct {
	ID        int64     `json:"id"`
	Name      string    `json:"name"`
	Email     string    `json:"email"`
	CreatedAt time.Time `json:"created_at"`
}

type handler struct {
	app *platform.App
	db  *pgxpool.Pool
	rdb *redis.Client
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	app := platform.MustNew(ctx, "user-service")
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
	app.Server.HandleFunc("GET /users", h.list)
	app.Server.HandleFunc("GET /users/{id}", h.get)
	app.Server.HandleFunc("POST /users", h.create)
	return app.Run(ctx)
}

func (h *handler) get(w http.ResponseWriter, r *http.Request) {
	id, err := platform.PathInt64(r, "id")
	if err != nil {
		platform.WriteError(w, http.StatusBadRequest, err.Error())
		return
	}
	ctx := r.Context()
	key := "user:" + strconv.FormatInt(id, 10)

	if cached, err := h.rdb.Get(ctx, key).Bytes(); err == nil {
		var u User
		if json.Unmarshal(cached, &u) == nil {
			platform.WriteJSON(w, http.StatusOK, u)
			return
		}
	} else if !errors.Is(err, redis.Nil) {
		// A cache outage is not fatal — fall back to the database.
		h.app.Log.WarnContext(ctx, "redis get failed", "err", err)
	}

	var u User
	err = h.db.QueryRow(ctx, `SELECT id, name, email, created_at FROM users WHERE id = $1`, id).
		Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		h.app.Log.InfoContext(ctx, "user not found", "user_id", id)
		platform.WriteError(w, http.StatusNotFound, "user not found")
		return
	}
	if err != nil {
		h.app.Log.ErrorContext(ctx, "query user failed", "user_id", id, "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}

	if b, err := json.Marshal(u); err == nil {
		if err := h.rdb.Set(ctx, key, b, cacheTTL).Err(); err != nil {
			h.app.Log.WarnContext(ctx, "redis set failed", "err", err)
		}
	}
	platform.WriteJSON(w, http.StatusOK, u)
}

func (h *handler) list(w http.ResponseWriter, r *http.Request) {
	limit, err := strconv.Atoi(r.URL.Query().Get("limit"))
	if err != nil || limit <= 0 || limit > 100 {
		limit = 20
	}
	rows, err := h.db.Query(r.Context(), `SELECT id, name, email, created_at FROM users ORDER BY id LIMIT $1`, limit)
	if err != nil {
		h.app.Log.ErrorContext(r.Context(), "list users failed", "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	users, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (User, error) {
		var u User
		err := row.Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
		return u, err
	})
	if err != nil {
		h.app.Log.ErrorContext(r.Context(), "scan users failed", "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	platform.WriteJSON(w, http.StatusOK, users)
}

func (h *handler) create(w http.ResponseWriter, r *http.Request) {
	var in struct {
		Name  string `json:"name"`
		Email string `json:"email"`
	}
	if err := platform.ReadJSON(r, &in); err != nil || in.Name == "" || in.Email == "" {
		platform.WriteError(w, http.StatusBadRequest, "name and email are required")
		return
	}
	var u User
	err := h.db.QueryRow(r.Context(),
		`INSERT INTO users (name, email) VALUES ($1, $2) ON CONFLICT (email) DO NOTHING
		 RETURNING id, name, email, created_at`, in.Name, in.Email).
		Scan(&u.ID, &u.Name, &u.Email, &u.CreatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		platform.WriteError(w, http.StatusConflict, "email already registered")
		return
	}
	if err != nil {
		h.app.Log.ErrorContext(r.Context(), "create user failed", "err", err)
		platform.WriteError(w, http.StatusInternalServerError, "internal error")
		return
	}
	h.app.Log.InfoContext(r.Context(), "user created", "user_id", u.ID)
	platform.WriteJSON(w, http.StatusCreated, u)
}
