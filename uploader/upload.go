package main

// The HTTP side: one multipart POST to /api/v1/ingest, the same request
// `qasight ingest --server` sends (qasight/services/ingest_client.py), retried
// only while the server can't have recorded the run yet, so a retry never
// records it twice.

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"mime/multipart"
	"net/http"
	"net/http/httptrace"
	"net/textproto"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

// result is the server's answer to a recorded upload (IngestResponse in qasight_api/models.py).
type result struct {
	RunID    *string `json:"run_id"`
	Cycle    string  `json:"cycle"`
	ExitCode int     `json:"exit_code"`
	Tests    int     `json:"tests"`
	Passed   int     `json:"passed"`
	Failed   int     `json:"failed"`
	Skipped  int     `json:"skipped"`
	RunURL   *string `json:"run_url"`
}

// uploadError says why an upload failed; badInput marks the server refusing what was
// sent (exit 2) rather than being unreachable or refusing the token (exit 3).
type uploadError struct {
	msg      string
	badInput bool
}

func (e *uploadError) Error() string { return e.msg }

type uploader struct {
	client   *http.Client
	server   string
	token    string
	retries  int
	baseWait time.Duration
	agent    string
	sleep    func(time.Duration)
	log      func(string)
}

// Answers that mean the request was not handled, so sending it again is safe.
var retryStatus = map[int]bool{
	http.StatusTooManyRequests:    true,
	http.StatusBadGateway:         true,
	http.StatusServiceUnavailable: true,
}

// buildBody writes the multipart form: the fields (sorted, for a stable request) and the files.
func buildBody(fields map[string]string, files []string) (*bytes.Buffer, string, error) {
	var buf bytes.Buffer
	w := multipart.NewWriter(&buf)
	keys := make([]string, 0, len(fields))
	for k := range fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if err := w.WriteField(k, fields[k]); err != nil {
			return nil, "", err
		}
	}
	for _, f := range files {
		h := make(textproto.MIMEHeader)
		h.Set("Content-Disposition", fmt.Sprintf(`form-data; name="files"; filename=%q`, filepath.Base(f)))
		h.Set("Content-Type", contentType(f))
		part, err := w.CreatePart(h)
		if err != nil {
			return nil, "", err
		}
		in, err := os.Open(f)
		if err != nil {
			return nil, "", err
		}
		_, err = io.Copy(part, in)
		in.Close()
		if err != nil {
			return nil, "", fmt.Errorf("reading %s: %w", f, err)
		}
	}
	if err := w.Close(); err != nil {
		return nil, "", err
	}
	return &buf, w.FormDataContentType(), nil
}

func contentType(name string) string {
	switch strings.ToLower(filepath.Ext(name)) {
	case ".json":
		return "application/json"
	case ".xml", ".trx":
		return "application/xml"
	default:
		return "application/octet-stream"
	}
}

func (u *uploader) url() string { return strings.TrimRight(u.server, "/") + "/api/v1/ingest" }

// send posts the form, retrying connection failures before the request went out and
// 429 / 502 / 503 answers, waiting longer each time (or as long as Retry-After asks).
func (u *uploader) send(ctx context.Context, body []byte, contentType string) (*result, error) {
	url := u.url()
	for attempt := 0; ; attempt++ {
		res, retryAfter, err := u.once(ctx, url, body, contentType)
		if err == nil {
			return res, nil
		}
		var notSent *retryError
		retryable := errors.As(err, &notSent) || retryAfter >= 0
		if !retryable || attempt >= u.retries || ctx.Err() != nil {
			return nil, err
		}
		wait := u.baseWait << attempt
		wait += time.Duration(rand.Int63n(int64(u.baseWait)/2 + 1)) // jitter, so parallel jobs spread out
		if retryAfter > 0 {
			wait = retryAfter
		}
		u.log(fmt.Sprintf("%v; trying again in %s (%d of %d)", err, wait.Round(100*time.Millisecond), attempt+1, u.retries))
		u.sleep(wait)
	}
}

// retryError is a failure where the request never reached the server's handler.
type retryError struct{ msg string }

func (e *retryError) Error() string { return e.msg }

const maxRetryAfter = 60 * time.Second

// once makes one attempt. retryAfter is >= 0 when the answer says to try again
// (0: no wait given). A transport error comes back as *retryError when the request
// was not fully sent, and as *uploadError when it was, since the server may then
// have recorded the run.
func (u *uploader) once(ctx context.Context, url string, body []byte, contentType string) (*result, time.Duration, error) {
	wrote := false
	trace := &httptrace.ClientTrace{WroteRequest: func(httptrace.WroteRequestInfo) { wrote = true }}
	req, err := http.NewRequestWithContext(httptrace.WithClientTrace(ctx, trace), http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return nil, -1, &uploadError{msg: fmt.Sprintf("bad server URL %s: %v", url, err), badInput: true}
	}
	req.Header.Set("Content-Type", contentType)
	req.Header.Set("Accept", "application/json")
	req.Header.Set("User-Agent", u.agent)
	if u.token != "" {
		req.Header.Set("Authorization", "Bearer "+u.token)
	}
	resp, err := u.client.Do(req)
	if err != nil {
		if wrote {
			return nil, -1, &uploadError{msg: fmt.Sprintf(
				"lost the connection to %s after sending the files (%v); not sent again, since the server may have recorded the run", url, err)}
		}
		return nil, -1, &retryError{msg: fmt.Sprintf("could not reach %s: %v", url, err)}
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))

	if resp.StatusCode != http.StatusCreated {
		msg := fmt.Sprintf("%s answered %d: %s", url, resp.StatusCode, errorMessage(raw))
		retryAfter := time.Duration(-1)
		if retryStatus[resp.StatusCode] {
			retryAfter = 0
			if secs, err := strconv.Atoi(strings.TrimSpace(resp.Header.Get("Retry-After"))); err == nil && secs > 0 {
				retryAfter = min(time.Duration(secs)*time.Second, maxRetryAfter)
			}
		}
		switch resp.StatusCode {
		case http.StatusUnauthorized, http.StatusForbidden:
			msg += " (check the token: --token or $TESTO_API_TOKEN)"
		}
		bad := resp.StatusCode == http.StatusBadRequest || resp.StatusCode == http.StatusRequestEntityTooLarge ||
			resp.StatusCode == http.StatusUnprocessableEntity
		return nil, retryAfter, &uploadError{msg: msg, badInput: bad}
	}
	var res result
	if err := json.Unmarshal(raw, &res); err != nil {
		return nil, -1, &uploadError{msg: fmt.Sprintf("%s answered with something that is not the expected JSON", url)}
	}
	return &res, -1, nil
}

// errorMessage pulls the reason out of an error answer: QASight's {"error": {"message"}},
// FastAPI's {"detail"}, or the start of the text.
func errorMessage(raw []byte) string {
	var body map[string]any
	if json.Unmarshal(raw, &body) == nil {
		if e, ok := body["error"].(map[string]any); ok {
			if m, ok := e["message"].(string); ok && m != "" {
				return m
			}
		}
		if d, ok := body["detail"].(string); ok && d != "" {
			return d
		}
		if d, ok := body["detail"]; ok {
			if b, err := json.Marshal(d); err == nil {
				return truncate(string(b), 300)
			}
		}
	}
	text := strings.TrimSpace(string(raw))
	if text == "" {
		return "no details"
	}
	return truncate(text, 300)
}
