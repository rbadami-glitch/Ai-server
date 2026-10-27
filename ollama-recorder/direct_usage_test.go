package server

import (
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/ollama/ollama/api"
)

func TestDirectUsageRecordsFinalStreamOnly(t *testing.T) {
	previous := activeDirectUsageRecorder
	recorder := &directUsageRecorder{queue: make(chan directUsageEvent, 2)}
	recorder.listener.Store("unknown")
	activeDirectUsageRecorder = recorder
	t.Cleanup(func() { activeDirectUsageRecorder = previous })

	s := &Server{addr: &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 11434}}
	router := gin.New()
	router.Use(s.directUsageMiddleware())
	router.POST("/api/chat", func(c *gin.Context) {
		c.Set(directUsageModelKey, "gemma4:31b")
		ch := make(chan any, 2)
		ch <- api.ChatResponse{Model: "gemma4:31b", Done: false}
		ch <- api.ChatResponse{Model: "gemma4:31b", Done: true,
			Metrics: api.Metrics{PromptEvalCount: 12, EvalCount: 8}}
		close(ch)
		streamResponse(c, ch)
	})

	request := httptest.NewRequest(http.MethodPost, "/api/chat", nil)
	request.RemoteAddr = "10.0.0.8:4321"
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status = %d", response.Code)
	}
	select {
	case event := <-recorder.queue:
		if event.Model != "gemma4:31b" || event.SourceIP != "10.0.0.8" ||
			event.Listener != "11434" || event.Status != 200 ||
			event.PromptTokens == nil || *event.PromptTokens != 12 ||
			event.CompletionTokens == nil || *event.CompletionTokens != 8 ||
			event.TotalTokens == nil || *event.TotalTokens != 20 {
			t.Fatalf("incorrect event: %+v", event)
		}
		encoded, err := json.Marshal(event)
		if err != nil {
			t.Fatal(err)
		}
		if json.Valid(encoded) == false {
			t.Fatal("event did not encode")
		}
	default:
		t.Fatal("missing usage event")
	}
	if len(recorder.queue) != 0 {
		t.Fatal("more than one event was recorded")
	}
}

func TestDirectUsageDropsWithoutBlocking(t *testing.T) {
	recorder := &directUsageRecorder{queue: make(chan directUsageEvent, 1)}
	recorder.enqueue(directUsageEvent{Schema: 1})
	recorder.enqueue(directUsageEvent{Schema: 1})
	if recorder.dropped.Load() != 1 {
		t.Fatalf("dropped = %d", recorder.dropped.Load())
	}
}

func TestDirectUsagePrivateFailureKeepsMissingTokensUnknown(t *testing.T) {
	previous := activeDirectUsageRecorder
	recorder := &directUsageRecorder{queue: make(chan directUsageEvent, 1)}
	recorder.listener.Store("unknown")
	activeDirectUsageRecorder = recorder
	t.Cleanup(func() { activeDirectUsageRecorder = previous })

	s := &Server{addr: &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 11435}}
	router := gin.New()
	router.Use(s.directUsageMiddleware())
	router.POST("/v1/chat/completions", func(c *gin.Context) {
		c.Set(directUsageModelKey, "gemma4:31b")
		c.AbortWithStatus(http.StatusInternalServerError)
	})
	request := httptest.NewRequest(http.MethodPost, "/v1/chat/completions", nil)
	request.RemoteAddr = "127.0.0.1:54321"
	router.ServeHTTP(httptest.NewRecorder(), request)
	select {
	case event := <-recorder.queue:
		if event.Listener != "11435" || event.Status != 500 ||
			event.Model != "gemma4:31b" || event.SourceIP != "127.0.0.1" ||
			event.PromptTokens != nil || event.CompletionTokens != nil {
			t.Fatalf("incorrect private failure event: %+v", event)
		}
	default:
		t.Fatal("missing private failure event")
	}
}
