package platform

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp"
)

// StatusError is a non-2xx response from another service.
type StatusError struct {
	Peer string
	Code int
	Body string
}

func (e *StatusError) Error() string {
	return fmt.Sprintf("%s responded %d: %s", e.Peer, e.Code, e.Body)
}

// Client is a service-to-service HTTP client with trace propagation (W3C traceparent),
// per-request timeouts and per-peer metrics.
type Client struct {
	peer    string
	baseURL string
	hc      *http.Client
}

// NewClient creates a client for peer (service name) at baseURL, e.g. http://user-service:8080.
func NewClient(peer, baseURL string) *Client {
	return &Client{
		peer:    peer,
		baseURL: baseURL,
		hc: &http.Client{
			Transport: otelhttp.NewTransport(http.DefaultTransport),
			Timeout:   EnvDuration("HTTP_CLIENT_TIMEOUT", 3*time.Second),
		},
	}
}

// DoJSON sends a JSON request (in may be nil) and decodes a 2xx response into out (may be nil).
// Non-2xx responses are returned as *StatusError.
func (c *Client) DoJSON(ctx context.Context, method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return fmt.Errorf("marshal request: %w", err)
		}
		body = bytes.NewReader(b)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return err
	}
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	start := time.Now()
	resp, err := c.hc.Do(req)
	code := "error"
	if err == nil {
		code = strconv.Itoa(resp.StatusCode)
	}
	httpClientRequests.WithLabelValues(c.peer, method, code).Inc()
	httpClientDuration.WithLabelValues(c.peer, method, code).Observe(time.Since(start).Seconds())
	if err != nil {
		return fmt.Errorf("call %s: %w", c.peer, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		msg, _ := io.ReadAll(io.LimitReader(resp.Body, 512))
		return &StatusError{Peer: c.peer, Code: resp.StatusCode, Body: string(bytes.TrimSpace(msg))}
	}
	if out == nil {
		_, _ = io.Copy(io.Discard, resp.Body)
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("decode %s response: %w", c.peer, err)
	}
	return nil
}
