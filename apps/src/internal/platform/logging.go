package platform

import (
	"context"
	"log/slog"
	"os"

	"go.opentelemetry.io/otel/trace"
)

// NewLogger returns a JSON logger on stdout; "time"/"level"/"msg" map to _time/_msg
// in VictoriaLogs (vlagent k8sCollector).
func NewLogger(service string) *slog.Logger {
	level := new(slog.LevelVar)
	if err := level.UnmarshalText([]byte(Env("LOG_LEVEL", "info"))); err != nil {
		level.Set(slog.LevelInfo)
	}
	h := slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level})
	return slog.New(traceHandler{h}).With("service", service)
}

// traceHandler adds trace_id and span_id from the context to every record, linking
// logs (VictoriaLogs) and traces (VictoriaTraces) in Grafana.
type traceHandler struct{ slog.Handler }

func (h traceHandler) Handle(ctx context.Context, r slog.Record) error {
	if sc := trace.SpanContextFromContext(ctx); sc.IsValid() {
		r.AddAttrs(
			slog.String("trace_id", sc.TraceID().String()),
			slog.String("span_id", sc.SpanID().String()),
		)
	}
	return h.Handler.Handle(ctx, r)
}

func (h traceHandler) WithAttrs(attrs []slog.Attr) slog.Handler {
	return traceHandler{h.Handler.WithAttrs(attrs)}
}

func (h traceHandler) WithGroup(name string) slog.Handler {
	return traceHandler{h.Handler.WithGroup(name)}
}
