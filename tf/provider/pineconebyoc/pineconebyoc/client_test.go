package pineconebyoc

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestTrimSlash(t *testing.T) {
	got := trimSlash("https://api.pinecone.io///")
	if got != "https://api.pinecone.io" {
		t.Fatalf("trimSlash() = %q", got)
	}
}

func TestServerHost(t *testing.T) {
	kubeconfig := "clusters:\n- cluster:\n    server: https://example.azmk8s.io:443\n"
	got := serverHost(kubeconfig)
	if got != "example.azmk8s.io" {
		t.Fatalf("serverHost() = %q", got)
	}
}

func TestDoRetriesWithRequestBody(t *testing.T) {
	attempts := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts++
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Fatalf("failed reading request body: %v", err)
		}
		if string(body) != `{"hello":"world"}` {
			t.Fatalf("attempt %d body = %q", attempts, string(body))
		}
		if attempts == 1 {
			http.Error(w, "retry me", http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	client := newClient(server.URL, "")
	err := client.do(context.Background(), http.MethodPost, server.URL, nil, map[string]string{"hello": "world"}, nil)
	if err != nil {
		t.Fatalf("do() returned error: %v", err)
	}
	if attempts != 2 {
		t.Fatalf("attempts = %d", attempts)
	}
}

func TestDeleteProjectIndexesDeletesAndWaits(t *testing.T) {
	indexExists := true
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodGet && r.URL.Path == "/indexes":
			w.Header().Set("Content-Type", "application/json")
			if indexExists {
				_, _ = w.Write([]byte(`{"indexes":[{"name":"idx","status":{"state":"Ready"}}]}`))
				return
			}
			_, _ = w.Write([]byte(`{"indexes":[]}`))
		case r.Method == http.MethodDelete && r.URL.Path == "/indexes/idx":
			indexExists = false
			w.WriteHeader(http.StatusAccepted)
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()

	client := newClient(server.URL, "")
	if err := client.deleteProjectIndexes(context.Background(), "project-key", time.Second, time.Millisecond); err != nil {
		t.Fatalf("deleteProjectIndexes() returned error: %v", err)
	}
}
