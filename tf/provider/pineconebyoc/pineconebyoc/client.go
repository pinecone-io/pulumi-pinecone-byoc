package pineconebyoc

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"
)

type Client struct {
	APIURL         string
	PineconeAPIKey string
	HTTP           *http.Client
}

type auth0Config struct {
	Domain       string
	ClientID     string
	ClientSecret string
}

type apiHTTPError struct {
	Method     string
	URL        string
	StatusCode int
	Body       string
}

func (e *apiHTTPError) Error() string {
	return fmt.Sprintf("%s %s failed: %d: %s", e.Method, e.URL, e.StatusCode, e.Body)
}

func newClient(apiURL, pineconeAPIKey string) *Client {
	return &Client{
		APIURL:         trimSlash(apiURL),
		PineconeAPIKey: pineconeAPIKey,
		HTTP:           &http.Client{Timeout: 60 * time.Second},
	}
}

func trimSlash(s string) string {
	for len(s) > 0 && s[len(s)-1] == '/' {
		s = s[:len(s)-1]
	}
	return s
}

func (c *Client) cpgwBootstrapURL() string {
	return c.APIURL + "/internal/cpgw/infra/bootstrap"
}

func (c *Client) cpgwInfraURL() string {
	return c.APIURL + "/internal/cpgw/infra"
}

func (c *Client) managementURL() string {
	return c.APIURL + "/management"
}

func (c *Client) do(ctx context.Context, method, url string, headers map[string]string, body any, out any) error {
	var payload []byte
	var err error
	if body != nil {
		payload, err = json.Marshal(body)
		if err != nil {
			return err
		}
	}

	var lastErr error
	for attempt := 0; attempt <= 3; attempt++ {
		var bodyReader io.Reader
		if payload != nil {
			bodyReader = bytes.NewReader(payload)
		}
		req, err := http.NewRequestWithContext(ctx, method, url, bodyReader)
		if err != nil {
			return err
		}
		req.Header.Set("Content-Type", "application/json")
		for k, v := range headers {
			req.Header.Set(k, v)
		}

		resp, err := c.HTTP.Do(req)
		if err != nil {
			lastErr = err
		} else {
			respBody, readErr := io.ReadAll(resp.Body)
			_ = resp.Body.Close()
			if readErr != nil {
				return readErr
			}
			if resp.StatusCode >= 200 && resp.StatusCode < 300 {
				if out == nil || len(respBody) == 0 {
					return nil
				}
				return json.Unmarshal(respBody, out)
			}
			lastErr = &apiHTTPError{
				Method:     method,
				URL:        url,
				StatusCode: resp.StatusCode,
				Body:       string(respBody),
			}
			if resp.StatusCode < 500 {
				return lastErr
			}
		}

		if attempt < 3 {
			time.Sleep(time.Duration(1<<attempt) * 2 * time.Second)
		}
	}
	return lastErr
}

func isHTTPStatus(err error, status int) bool {
	var httpErr *apiHTTPError
	return errors.As(err, &httpErr) && httpErr.StatusCode == status
}

func apiKeyHeaders(key string) map[string]string {
	return map[string]string{"Api-Key": key}
}

func publicAPIHeaders(key string) map[string]string {
	return map[string]string{
		"Api-Key":                key,
		"X-Pinecone-API-Version": "2025-04",
	}
}

func bearerHeaders(jwt string) map[string]string {
	return map[string]string{
		"Authorization":          "Bearer " + jwt,
		"X-Pinecone-Api-Version": "unstable",
	}
}

func (c *Client) accessToken(ctx context.Context, auth auth0Config) (string, error) {
	var out struct {
		AccessToken string `json:"access_token"`
	}
	err := c.do(ctx, http.MethodPost, trimSlash(auth.Domain)+"/oauth/token", nil, map[string]any{
		"client_id":     auth.ClientID,
		"client_secret": auth.ClientSecret,
		"audience":      c.APIURL + "/",
		"grant_type":    "client_credentials",
	}, &out)
	return out.AccessToken, err
}
