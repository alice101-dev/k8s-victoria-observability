package main

import (
	"context"
	"log/slog"
	"os"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"

	"shop/internal/platform"
)

// Integration test against a real Redis: REDIS_TEST_ADDR=localhost:6379 go test ./cmd/notification-service/
func newTestHandler(t *testing.T) (*handler, *redis.Client) {
	addr := os.Getenv("REDIS_TEST_ADDR")
	if addr == "" {
		t.Skip("REDIS_TEST_ADDR not set")
	}
	rdb := redis.NewClient(&redis.Options{Addr: addr})
	t.Cleanup(func() { rdb.Close() })
	ctx := context.Background()
	rdb.Del(ctx, stream)
	if err := rdb.XGroupCreateMkStream(ctx, stream, group, "$").Err(); err != nil {
		t.Fatal(err)
	}
	return &handler{app: &platform.App{Name: "test", Log: slog.Default()}, rdb: rdb}, rdb
}

// An entry read by a consumer that then disappeared is delivered by another consumer and acknowledged.
func TestReclaimDeliversAbandonedEntries(t *testing.T) {
	h, rdb := newTestHandler(t)
	ctx := context.Background()
	reclaimMinIdle = 50 * time.Millisecond

	id := rdb.XAdd(ctx, &redis.XAddArgs{Stream: stream, Values: map[string]any{"channel": "email", "user_id": "1", "message": "hi"}}).Val()
	if n := len(rdb.XReadGroup(ctx, &redis.XReadGroupArgs{Group: group, Consumer: "dead", Streams: []string{stream, ">"}, Count: 1}).Val()); n != 1 {
		t.Fatalf("dead consumer read %d streams", n)
	}
	time.Sleep(100 * time.Millisecond)

	h.reclaim(ctx, otel.Tracer("test"), rdb, "live")

	if p := rdb.XPending(ctx, stream, group).Val(); p.Count != 0 {
		t.Fatalf("entry %s still pending: %+v", id, p)
	}
}

// An entry that keeps failing is dropped after maxDeliveries attempts instead of being retried forever.
func TestReclaimDropsAfterMaxDeliveries(t *testing.T) {
	h, rdb := newTestHandler(t)
	ctx := context.Background()
	reclaimMinIdle, maxDeliveries = 10*time.Millisecond, 2

	rdb.XAdd(ctx, &redis.XAddArgs{Stream: stream, Values: map[string]any{"channel": "sms"}})
	rdb.XReadGroup(ctx, &redis.XReadGroupArgs{Group: group, Consumer: "dead", Streams: []string{stream, ">"}, Count: 1})
	// Simulate a second failed delivery: claimed by another consumer that also died.
	time.Sleep(20 * time.Millisecond)
	ids := rdb.XPendingExt(ctx, &redis.XPendingExtArgs{Stream: stream, Group: group, Start: "-", End: "+", Count: 1}).Val()
	rdb.XClaim(ctx, &redis.XClaimArgs{Stream: stream, Group: group, Consumer: "dead-too", MinIdle: 0, Messages: []string{ids[0].ID}})
	time.Sleep(20 * time.Millisecond)

	h.reclaim(ctx, otel.Tracer("test"), rdb, "live")

	if p := rdb.XPending(ctx, stream, group).Val(); p.Count != 0 {
		t.Fatalf("entry still pending after %d deliveries: %+v", maxDeliveries, p)
	}
}

// Consumers that are gone and hold nothing are removed; the live one and those with pending entries stay.
func TestRemoveStaleConsumers(t *testing.T) {
	h, rdb := newTestHandler(t)
	ctx := context.Background()
	staleConsumer = 10 * time.Millisecond

	rdb.XGroupCreateConsumer(ctx, stream, group, "gone")
	rdb.XGroupCreateConsumer(ctx, stream, group, "live")
	rdb.XAdd(ctx, &redis.XAddArgs{Stream: stream, Values: map[string]any{"channel": "email"}})
	rdb.XReadGroup(ctx, &redis.XReadGroupArgs{Group: group, Consumer: "holding", Streams: []string{stream, ">"}, Count: 1})
	time.Sleep(30 * time.Millisecond)

	h.removeStaleConsumers(ctx, rdb, "live")

	left := map[string]bool{}
	for _, c := range rdb.XInfoConsumers(ctx, stream, group).Val() {
		left[c.Name] = true
	}
	if left["gone"] || !left["live"] || !left["holding"] {
		t.Fatalf("consumers left: %v, want live and holding", left)
	}
}
