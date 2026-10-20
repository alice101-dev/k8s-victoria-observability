package platform

import (
	"context"
	"errors"
	"fmt"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
)

// InitTracing installs a global TracerProvider exporting spans over OTLP/HTTP to VictoriaTraces.
//
// Tuning uses the standard OpenTelemetry environment variables (read by the SDK):
//   - OTEL_EXPORTER_OTLP_TRACES_ENDPOINT : full URL, e.g. http://vtinsert:10481/insert/opentelemetry/v1/traces
//   - OTEL_TRACES_SAMPLER / OTEL_TRACES_SAMPLER_ARG : e.g. parentbased_traceidratio + 0.1
//   - OTEL_RESOURCE_ATTRIBUTES : Kubernetes attributes (namespace, pod, node) from the Downward API
//   - OTEL_BSP_* : batch size and export interval
func InitTracing(ctx context.Context, service string) (func(context.Context) error, error) {
	exp, err := otlptracehttp.New(ctx)
	if err != nil {
		return nil, fmt.Errorf("otlp trace exporter: %w", err)
	}

	// Order matters: later detectors win, so the environment (OTEL_SERVICE_NAME,
	// OTEL_RESOURCE_ATTRIBUTES) overrides the default service.name.
	res, err := resource.New(ctx,
		resource.WithAttributes(attribute.String("service.name", service)),
		resource.WithTelemetrySDK(),
		resource.WithFromEnv(),
	)
	if err != nil && !errors.Is(err, resource.ErrPartialResource) {
		return nil, fmt.Errorf("otel resource: %w", err)
	}

	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exp),
		sdktrace.WithResource(res),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))
	return tp.Shutdown, nil
}
