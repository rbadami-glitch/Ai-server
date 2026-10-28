package server

// When enabled on an Ollama service, force every chat and text-generation
// request to the local Gemma model before compatibility or cloud middleware
// chooses an upstream. Embedding and management endpoints are untouched.

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"strings"

	"github.com/gin-gonic/gin"
)

const forcedChatModel = "gemma4:31b"

func forceChatModelRoute(path string) bool {
	switch path {
	case "/api/chat", "/api/generate", "/v1/chat/completions",
		"/v1/completions", "/v1/responses", "/v1/messages":
		return true
	default:
		return false
	}
}

func forceChatModelMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		if c.Request.Method != http.MethodPost || !forceChatModelRoute(c.Request.URL.Path) {
			c.Next()
			return
		}
		body, err := io.ReadAll(c.Request.Body)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "could not read request body"})
			return
		}
		// Preserve malformed requests so their normal endpoint returns its own 400.
		var payload map[string]json.RawMessage
		if json.Unmarshal(body, &payload) != nil || payload == nil {
			c.Request.Body = io.NopCloser(bytes.NewReader(body))
			c.Next()
			return
		}
		payload["model"] = json.RawMessage(`"` + forcedChatModel + `"`)
		rewritten, err := json.Marshal(payload)
		if err != nil {
			c.AbortWithStatusJSON(http.StatusBadRequest, gin.H{"error": "could not rewrite request"})
			return
		}
		c.Request.Body = io.NopCloser(bytes.NewReader(rewritten))
		c.Request.ContentLength = int64(len(rewritten))
		c.Request.Header.Del("Content-Length")
		c.Next()
	}
}

func forceChatModelEnabled() bool {
	return strings.EqualFold(strings.TrimSpace(os.Getenv("OLLAMA_FORCE_CHAT_MODEL")), forcedChatModel)
}
