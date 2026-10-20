package platform

import (
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strconv"
)

const maxBodyBytes = 1 << 20 // 1 MiB

// WriteJSON writes v as JSON with the given status code.
func WriteJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if v != nil {
		if err := json.NewEncoder(w).Encode(v); err != nil {
			slog.Default().Warn("encode response failed", "err", err)
		}
	}
}

// WriteError writes the standard error body {"error": "..."}.
func WriteError(w http.ResponseWriter, code int, msg string) {
	WriteJSON(w, code, map[string]string{"error": msg})
}

// ReadJSON decodes the request body into v (max 1 MiB, unknown fields rejected).
func ReadJSON(r *http.Request, v any) error {
	dec := json.NewDecoder(io.LimitReader(r.Body, maxBodyBytes))
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return fmt.Errorf("invalid json body: %w", err)
	}
	return nil
}

// PathInt64 parses a ServeMux path value (e.g. {id}) as a positive int64.
func PathInt64(r *http.Request, name string) (int64, error) {
	v, err := strconv.ParseInt(r.PathValue(name), 10, 64)
	if err != nil || v <= 0 {
		return 0, fmt.Errorf("invalid %s", name)
	}
	return v, nil
}
