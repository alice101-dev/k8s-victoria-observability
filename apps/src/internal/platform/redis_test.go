package platform

import (
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/redis/go-redis/v9"
)

// Two clients in one process (notification-service) must not panic and must both be exported.
func TestRegisterRedisPoolTwoClients(t *testing.T) {
	a := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", PoolSize: 7})
	b := redis.NewClient(&redis.Options{Addr: "127.0.0.1:1", PoolSize: 3})
	defer a.Close()
	defer b.Close()
	RegisterRedisPool("default", a)
	RegisterRedisPool("stream-consumer", b)

	families, err := prometheus.DefaultGatherer.Gather()
	if err != nil {
		t.Fatal(err)
	}
	sizes := map[string]float64{}
	for _, f := range families {
		if f.GetName() != "goredis_pool_max_connections" {
			continue
		}
		for _, m := range f.GetMetric() {
			sizes[m.GetLabel()[0].GetValue()] = m.GetGauge().GetValue()
		}
	}
	if sizes["default"] != 7 || sizes["stream-consumer"] != 3 {
		t.Fatalf("goredis_pool_max_connections = %v, want default=7 stream-consumer=3", sizes)
	}
}
