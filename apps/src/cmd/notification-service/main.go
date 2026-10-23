// notification-service: queues notifications on a Redis Stream and delivers them asynchronously
// via a consumer group. The trace context travels inside each message, so delivery spans stay
// attached to the originating order trace.
//
// Delivery is at least once: an entry is acknowledged only after it was delivered. Entries left pending by a
// consumer that stopped (pod deleted, OOM kill, a read whose reply was lost) are reclaimed by the other
// consumers after a minute and delivered again; after maxDeliveries attempts an entry is dropped and logged.
package main

import (
	"context"
	"errors"
	"math/rand/v2"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/redis/go-redis/v9"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/trace"

	"shop/internal/platform"
)

const (
	stream = "notifications"
	group  = "notifier"
)

// Variables so tests can shorten them.
var (
	reclaimEvery   = 30 * time.Second // how often each consumer looks for abandoned entries
	reclaimMinIdle = time.Minute      // pending longer than this: its consumer is gone (a delivery takes < 100ms)
	maxDeliveries  = int64(5)         // attempts before an entry is dropped
	staleConsumer  = time.Hour        // consumers idle this long with nothing pending are removed from the group
)

var notificationsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
	Name: "notifications_total",
	Help: "Notifications by channel and stage (queued, delivered, reclaimed, dropped).",
}, []string{"channel", "stage"})

type notificationRequest struct {
	UserID  int64  `json:"user_id"`
	Channel string `json:"channel"`
	Message string `json:"message"`
}

type handler struct {
	app *platform.App
	rdb *redis.Client
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	app := platform.MustNew(ctx, "notification-service")
	if err := run(ctx, app); err != nil {
		app.Log.Error("service failed", "err", err)
		os.Exit(1)
	}
}

func run(ctx context.Context, app *platform.App) error {
	rdb, err := app.Redis(ctx)
	if err != nil {
		return err
	}
	// BUSYGROUP: another replica already created the group.
	if err := rdb.XGroupCreateMkStream(ctx, stream, group, "$").Err(); err != nil &&
		!strings.HasPrefix(err.Error(), "BUSYGROUP") {
		return err
	}

	h := &handler{app: app, rdb: rdb}
	app.Server.HandleFunc("POST /notifications", h.enqueue)

	// Separate, uninstrumented client for the blocking XREADGROUP loop, so polling
	// does not flood VictoriaTraces with useless root spans.
	consumer := redis.NewClient(rdb.Options())
	platform.RegisterRedisPool("stream-consumer", consumer)
	app.OnShutdown(func(context.Context) error { return consumer.Close() })

	name, _ := os.Hostname()
	var loops sync.WaitGroup
	loops.Add(2)
	go func() { defer loops.Done(); h.deliverLoop(ctx, consumer, name) }()
	go func() { defer loops.Done(); h.reclaimLoop(ctx, consumer, name) }()
	// Shutdown hooks run in reverse order: this one runs before consumer.Close, so a delivery in progress
	// finishes and is acknowledged instead of staying pending.
	app.OnShutdown(func(c context.Context) error {
		done := make(chan struct{})
		go func() { loops.Wait(); close(done) }()
		select {
		case <-done:
			return nil
		case <-c.Done():
			return errors.New("notification loops did not stop in time")
		}
	})

	return app.Run(ctx)
}

func (h *handler) enqueue(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	var in notificationRequest
	if err := platform.ReadJSON(r, &in); err != nil || in.UserID <= 0 || in.Message == "" {
		platform.WriteError(w, http.StatusBadRequest, "user_id and message are required")
		return
	}
	if in.Channel == "" {
		in.Channel = "email"
	}

	fields := map[string]any{
		"user_id": in.UserID,
		"channel": in.Channel,
		"message": in.Message,
	}
	// Propagate traceparent across the async boundary.
	carrier := propagation.MapCarrier{}
	otel.GetTextMapPropagator().Inject(ctx, carrier)
	for k, v := range carrier {
		fields[k] = v
	}

	id, err := h.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: stream,
		MaxLen: 10000,
		Approx: true,
		Values: fields,
	}).Result()
	if err != nil {
		h.app.Log.ErrorContext(ctx, "enqueue notification failed", "err", err)
		platform.WriteError(w, http.StatusServiceUnavailable, "queue unavailable")
		return
	}
	notificationsTotal.WithLabelValues(in.Channel, "queued").Inc()
	h.app.Log.InfoContext(ctx, "notification queued", "user_id", in.UserID, "channel", in.Channel, "stream_id", id)
	platform.WriteJSON(w, http.StatusAccepted, map[string]string{"id": id})
}

func (h *handler) deliverLoop(ctx context.Context, consumer *redis.Client, name string) {
	tracer := otel.Tracer("notification-service")

	for ctx.Err() == nil {
		res, err := consumer.XReadGroup(ctx, &redis.XReadGroupArgs{
			Group:    group,
			Consumer: name,
			Streams:  []string{stream, ">"},
			Count:    10,
			Block:    5 * time.Second,
		}).Result()
		if errors.Is(err, redis.Nil) || ctx.Err() != nil {
			continue
		}
		if err != nil {
			h.app.Log.Warn("read stream failed", "err", err)
			time.Sleep(2 * time.Second)
			continue
		}
		for _, s := range res {
			for _, msg := range s.Messages {
				h.deliver(ctx, tracer, consumer, msg)
			}
		}
	}
}

func (h *handler) deliver(ctx context.Context, tracer trace.Tracer, consumer *redis.Client, msg redis.XMessage) {
	carrier := propagation.MapCarrier{}
	for k, v := range msg.Values {
		if s, ok := v.(string); ok {
			carrier[k] = s
		}
	}
	parent := otel.GetTextMapPropagator().Extract(ctx, carrier)
	spanCtx, span := tracer.Start(parent, "notification.deliver", trace.WithSpanKind(trace.SpanKindConsumer))
	defer span.End()

	channel, _ := msg.Values["channel"].(string)
	span.SetAttributes(
		attribute.String("messaging.system", "redis"),
		attribute.String("messaging.destination.name", stream),
		attribute.String("messaging.message.id", msg.ID),
		attribute.String("notification.channel", channel),
	)

	// Simulate the email/SMS provider call.
	time.Sleep(time.Duration(10+rand.IntN(70)) * time.Millisecond)

	// Not cancelled by shutdown: the delivery happened, so it must be acknowledged.
	ackCtx, cancel := context.WithTimeout(context.WithoutCancel(spanCtx), 2*time.Second)
	defer cancel()
	if err := consumer.XAck(ackCtx, stream, group, msg.ID).Err(); err != nil {
		span.RecordError(err)
		h.app.Log.ErrorContext(spanCtx, "ack notification failed", "stream_id", msg.ID, "err", err)
		return
	}
	notificationsTotal.WithLabelValues(channel, "delivered").Inc()
	h.app.Log.InfoContext(spanCtx, "notification delivered", "stream_id", msg.ID, "channel", channel, "user_id", msg.Values["user_id"])
}

// reclaimLoop takes over entries other consumers left pending and removes consumers that are gone.
func (h *handler) reclaimLoop(ctx context.Context, consumer *redis.Client, name string) {
	tracer := otel.Tracer("notification-service")
	tick := time.NewTicker(reclaimEvery)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
		h.reclaim(ctx, tracer, consumer, name)
		h.removeStaleConsumers(ctx, consumer, name)
	}
}

func (h *handler) reclaim(ctx context.Context, tracer trace.Tracer, consumer *redis.Client, name string) {
	pending, err := consumer.XPendingExt(ctx, &redis.XPendingExtArgs{
		Stream: stream, Group: group, Idle: reclaimMinIdle, Start: "-", End: "+", Count: 100,
	}).Result()
	if err != nil {
		if ctx.Err() == nil {
			h.app.Log.Warn("list pending notifications failed", "err", err)
		}
		return
	}
	for _, p := range pending {
		if ctx.Err() != nil {
			return
		}
		// XCLAIM re-checks the idle time, so two consumers never take the same entry.
		msgs, err := consumer.XClaim(ctx, &redis.XClaimArgs{
			Stream: stream, Group: group, Consumer: name, MinIdle: reclaimMinIdle, Messages: []string{p.ID},
		}).Result()
		if err != nil {
			h.app.Log.Warn("reclaim notification failed", "stream_id", p.ID, "err", err)
			continue
		}
		if len(msgs) == 0 {
			continue // claimed by another consumer meanwhile
		}
		msg := msgs[0]
		channel, _ := msg.Values["channel"].(string)
		if len(msg.Values) == 0 || p.RetryCount >= maxDeliveries {
			// Trimmed from the stream (MAXLEN) or failing every time: give up instead of retrying forever.
			if err := consumer.XAck(ctx, stream, group, p.ID).Err(); err != nil {
				h.app.Log.Warn("drop notification failed", "stream_id", p.ID, "err", err)
				continue
			}
			notificationsTotal.WithLabelValues(channel, "dropped").Inc()
			h.app.Log.Error("notification dropped", "stream_id", p.ID, "deliveries", p.RetryCount,
				"previous_consumer", p.Consumer, "trimmed", len(msg.Values) == 0)
			continue
		}
		notificationsTotal.WithLabelValues(channel, "reclaimed").Inc()
		h.app.Log.Warn("notification reclaimed", "stream_id", p.ID, "previous_consumer", p.Consumer,
			"idle", p.Idle.String(), "deliveries", p.RetryCount)
		h.deliver(ctx, tracer, consumer, msg)
	}
}

func (h *handler) removeStaleConsumers(ctx context.Context, consumer *redis.Client, name string) {
	consumers, err := consumer.XInfoConsumers(ctx, stream, group).Result()
	if err != nil {
		return
	}
	for _, c := range consumers {
		if c.Name == name || c.Pending > 0 || c.Idle < staleConsumer {
			continue
		}
		if err := consumer.XGroupDelConsumer(ctx, stream, group, c.Name).Err(); err == nil {
			h.app.Log.Info("removed stale stream consumer", "consumer", c.Name, "idle", c.Idle.String())
		}
	}
}
