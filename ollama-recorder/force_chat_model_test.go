package server

import (
	"encoding/json"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
)

func TestForceChatModelRoutes(t *testing.T) {
	for _, path := range []string{
		"/api/chat", "/api/generate", "/v1/chat/completions",
		"/v1/completions", "/v1/responses", "/v1/messages",
	} {
		t.Run(path, func(t *testing.T) {
			router := gin.New()
			router.Use(forceChatModelMiddleware())
			router.POST(path, func(c *gin.Context) {
				var body map[string]any
				if err := c.ShouldBindJSON(&body); err != nil {
					t.Fatal(err)
				}
				c.JSON(http.StatusOK, body)
			})
			request := httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{"model":"devstral:latest","stream":true}`))
			request.Header.Set("Content-Type", "application/json")
			response := httptest.NewRecorder()
			router.ServeHTTP(response, request)
			var body map[string]any
			if response.Code != http.StatusOK || json.Unmarshal(response.Body.Bytes(), &body) != nil ||
				body["model"] != forcedChatModel || body["stream"] != true {
				t.Fatalf("response = %d %s", response.Code, response.Body.String())
			}
		})
	}
}

func TestForceChatModelLeavesEmbeddingsAndInvalidJSON(t *testing.T) {
	router := gin.New()
	router.Use(forceChatModelMiddleware())
	for _, path := range []string{"/api/embed", "/v1/embeddings", "/api/chat"} {
		router.POST(path, func(c *gin.Context) {
			var body map[string]any
			if err := c.ShouldBindJSON(&body); err != nil {
				c.Status(http.StatusBadRequest)
				return
			}
			c.JSON(http.StatusOK, body)
		})
	}
	for _, path := range []string{"/api/embed", "/v1/embeddings"} {
		request := httptest.NewRequest(http.MethodPost, path, strings.NewReader(`{"model":"embeddinggemma"}`))
		response := httptest.NewRecorder()
		router.ServeHTTP(response, request)
		if response.Code != http.StatusOK || !strings.Contains(response.Body.String(), "embeddinggemma") {
			t.Fatalf("embedding response = %d %s", response.Code, response.Body.String())
		}
	}
	request := httptest.NewRequest(http.MethodPost, "/api/chat", strings.NewReader(`{"model":`))
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("invalid JSON status = %d", response.Code)
	}
}

func TestForceChatModelWiredIntoOllamaRoutes(t *testing.T) {
	previous := activeDirectUsageRecorder
	recorder := &directUsageRecorder{queue: make(chan directUsageEvent, 1)}
	recorder.listener.Store("unknown")
	activeDirectUsageRecorder = recorder
	t.Cleanup(func() { activeDirectUsageRecorder = previous })

	s := &Server{addr: &net.TCPAddr{IP: net.ParseIP("127.0.0.1"), Port: 11435}}
	routes, err := s.GenerateRoutes(nil)
	if err != nil {
		t.Fatal(err)
	}
	request := httptest.NewRequest(http.MethodPost, "/api/generate",
		strings.NewReader(`{"model":"devstral:latest","prompt":"","top_logprobs":-1}`))
	request.Header.Set("Content-Type", "application/json")
	response := httptest.NewRecorder()
	routes.ServeHTTP(response, request)
	if response.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, body = %s", response.Code, response.Body.String())
	}
	select {
	case event := <-recorder.queue:
		if event.Model != forcedChatModel || event.Route != "/api/generate" {
			t.Fatalf("routing event = %+v", event)
		}
	default:
		t.Fatal("routing event was not recorded")
	}
}
