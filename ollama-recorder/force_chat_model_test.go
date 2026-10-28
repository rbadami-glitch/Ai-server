package server

import (
	"encoding/json"
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

func TestForceChatModelOptIn(t *testing.T) {
	t.Setenv("OLLAMA_FORCE_CHAT_MODEL", "")
	if forceChatModelEnabled() {
		t.Fatal("empty setting enabled routing")
	}
	t.Setenv("OLLAMA_FORCE_CHAT_MODEL", "gemma4:31b")
	if !forceChatModelEnabled() {
		t.Fatal("Gemma setting did not enable routing")
	}
}
