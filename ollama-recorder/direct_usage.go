package server

// Optional metadata-only usage recording. The HTTP handler queues one small
// event after it finishes; the background goroutine owns all file I/O.
// Set OLLAMA_DIRECT_USAGE_DIR to enable this in an instrumented Ollama build.

import (
	"encoding/json"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/ollama/ollama/api"
)

const directUsageModelKey = "direct_usage_model"
const directUsageMetricsKey = "direct_usage_metrics"
const directUsageEmbedCountKey = "direct_usage_embed_count"

type directUsageEvent struct {
	Schema           int    `json:"schema"`
	Kind             string `json:"kind,omitempty"`
	At               int64  `json:"at"`
	Listener         string `json:"listener"`
	Route            string `json:"route,omitempty"`
	Model            string `json:"model,omitempty"`
	SourceIP         string `json:"source_ip,omitempty"`
	Status           int    `json:"status,omitempty"`
	PromptTokens     *int   `json:"prompt_tokens,omitempty"`
	CompletionTokens *int   `json:"completion_tokens,omitempty"`
	TotalTokens      *int   `json:"total_tokens,omitempty"`
	Dropped          uint64 `json:"dropped,omitempty"`
}

type directUsageRecorder struct {
	directory string
	queue     chan directUsageEvent
	stop      chan chan struct{}
	dropped   atomic.Uint64
	listener  atomic.Value
}

var activeDirectUsageRecorder = startDirectUsageRecorder()

func startDirectUsageRecorder() *directUsageRecorder {
	directory := strings.TrimSpace(os.Getenv("OLLAMA_DIRECT_USAGE_DIR"))
	if directory == "" {
		return nil
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		slog.Error("direct usage recorder disabled: cannot create directory", "error", err)
		return nil
	}
	recorder := &directUsageRecorder{directory: directory,
		queue: make(chan directUsageEvent, 8192), stop: make(chan chan struct{})}
	recorder.listener.Store("unknown")
	go recorder.run()
	slog.Info("direct usage recorder enabled", "directory", directory)
	return recorder
}

func directUsageRoute(path string) bool {
	switch path {
	case "/api/chat", "/api/generate", "/api/embed", "/api/embeddings",
		"/v1/chat/completions", "/v1/completions", "/v1/embeddings",
		"/v1/responses", "/v1/messages", "/v1/images/generations",
		"/v1/images/edits", "/v1/audio/transcriptions":
		return true
	default:
		return false
	}
}

func (s *Server) directUsageMiddleware() gin.HandlerFunc {
	listener := directUsagePort(s.addr)
	if activeDirectUsageRecorder != nil {
		activeDirectUsageRecorder.listener.Store(listener)
	}
	return func(c *gin.Context) {
		if !directUsageRoute(c.Request.URL.Path) {
			c.Next()
			return
		}
		c.Next()
		if listener == "unknown" {
			return
		}
		event := directUsageEvent{
			Schema: 1, At: time.Now().Unix(), Listener: listener,
			Route: c.Request.URL.Path, Status: c.Writer.Status(),
			Model: "Unknown model", SourceIP: "Unknown IP",
		}
		if value, ok := c.Get(directUsageModelKey); ok {
			if model, ok := value.(string); ok && model != "" && len(model) <= 256 {
				event.Model = model
			}
		}
		if host, _, err := net.SplitHostPort(c.Request.RemoteAddr); err == nil {
			event.SourceIP = host
		}
		if value, ok := c.Get(directUsageMetricsKey); ok {
			if metrics, ok := value.(api.Metrics); ok && (metrics.PromptEvalCount > 0 || metrics.EvalCount > 0) {
				prompt, completion := metrics.PromptEvalCount, metrics.EvalCount
				total := prompt + completion
				event.PromptTokens, event.CompletionTokens, event.TotalTokens = &prompt, &completion, &total
			}
		}
		if value, ok := c.Get(directUsageEmbedCountKey); ok {
			if prompt, ok := value.(int); ok && prompt > 0 {
				total := prompt
				event.PromptTokens, event.TotalTokens = &prompt, &total
			}
		}
		activeDirectUsageRecorder.enqueue(event)
	}
}

func (r *directUsageRecorder) enqueue(event directUsageEvent) {
	select {
	case r.queue <- event:
	default:
		r.dropped.Add(1)
	}
}

func (r *directUsageRecorder) shutdown() {
	done := make(chan struct{})
	select {
	case r.stop <- done:
	case <-time.After(3 * time.Second):
		slog.Warn("direct usage recorder did not begin shutdown in time")
		return
	}
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		slog.Warn("direct usage recorder did not flush in time")
	}
}

func (r *directUsageRecorder) run() {
	var currentKey string
	var file *os.File
	defer func() {
		if file != nil {
			file.Close()
		}
	}()
	write := func(event directUsageEvent) {
		day := time.Unix(event.At, 0).UTC().Format("2006-01-02")
		key := event.Listener + ":" + day
		if key != currentKey || file == nil {
			if file != nil {
				file.Close()
				file = nil
			}
			currentKey = key
			path := filepath.Join(r.directory, "ollama-"+event.Listener+"-"+day+".jsonl")
			var err error
			file, err = os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
			if err != nil {
				slog.Error("direct usage recorder cannot open file", "error", err)
				return
			}
		}
		if file == nil {
			return
		}
		line, err := json.Marshal(event)
		if err != nil {
			slog.Error("direct usage recorder cannot encode event", "error", err)
			return
		}
		line = append(line, '\n')
		if _, err := file.Write(line); err != nil {
			slog.Error("direct usage recorder cannot write file", "error", err)
			file.Close()
			file = nil
		}
	}
	ticker := time.NewTicker(5 * time.Second)
	defer ticker.Stop()
	writeDrops := func() {
		listener := r.listener.Load().(string)
		if dropped := r.dropped.Swap(0); dropped > 0 && listener != "unknown" {
			write(directUsageEvent{Schema: 1, Kind: "dropped", At: time.Now().Unix(),
				Listener: listener, Dropped: dropped})
		}
	}
	for {
		select {
		case event := <-r.queue:
			write(event)
		case <-ticker.C:
			writeDrops()
		case done := <-r.stop:
			for len(r.queue) > 0 {
				write(<-r.queue)
			}
			writeDrops()
			if file != nil {
				file.Close()
				file = nil
			}
			close(done)
			return
		}
	}
}

func directUsagePort(addr net.Addr) string {
	if addr == nil {
		return "unknown"
	}
	_, port, err := net.SplitHostPort(addr.String())
	if err != nil {
		return "unknown"
	}
	if port == "11434" || port == "11435" {
		return port
	}
	return "unknown"
}
