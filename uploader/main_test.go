package main

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func mapEnv(m map[string]string) env { return func(k string) string { return m[k] } }

type received struct {
	fields map[string]string
	files  map[string]string
	auth   string
	agent  string
}

// fakeServer answers like POST /api/v1/ingest; answer picks the status per attempt (from 1).
func fakeServer(t *testing.T, answer func(attempt int, w http.ResponseWriter, got *received)) (*httptest.Server, *int32) {
	t.Helper()
	var attempts int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		n := int(atomic.AddInt32(&attempts, 1))
		if r.URL.Path != "/api/v1/ingest" || r.Method != http.MethodPost {
			http.NotFound(w, r)
			return
		}
		if err := r.ParseMultipartForm(32 << 20); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		got := &received{fields: map[string]string{}, files: map[string]string{}, auth: r.Header.Get("Authorization"), agent: r.UserAgent()}
		for k, v := range r.MultipartForm.Value {
			got.fields[k] = v[0]
		}
		for _, fh := range r.MultipartForm.File["files"] {
			f, _ := fh.Open()
			b, _ := io.ReadAll(f)
			f.Close()
			got.files[fh.Filename] = string(b)
		}
		answer(n, w, got)
	}))
	t.Cleanup(srv.Close)
	return srv, &attempts
}

func created(w http.ResponseWriter, body map[string]any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(body)
}

func writeFile(t *testing.T, path, content string) string {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	return path
}

func newApp(vars map[string]string) (*app, *bytes.Buffer, *bytes.Buffer, *[]time.Duration) {
	var out, errb bytes.Buffer
	var waits []time.Duration
	return &app{
		stdout: &out, stderr: &errb, env: mapEnv(vars), client: &http.Client{},
		sleep: func(d time.Duration) { waits = append(waits, d) }, baseWait: 10 * time.Millisecond,
	}, &out, &errb, &waits
}

const junit = `<testsuite tests="1"><testcase name="t"/></testsuite>`

func TestUploadSendsFilesFieldsAndToken(t *testing.T) {
	dir := t.TempDir()
	report := writeFile(t, filepath.Join(dir, "reports", "unit.xml"), junit)
	var got *received
	srv, _ := fakeServer(t, func(_ int, w http.ResponseWriter, r *received) {
		got = r
		created(w, map[string]any{"run_id": "r1", "cycle": "web", "exit_code": 0, "tests": 3, "passed": 3, "failed": 0, "skipped": 0, "run_url": "http://x/runs/r1"})
	})
	a, out, _, _ := newApp(map[string]string{
		"TESTO_SERVER_URL": srv.URL, "TESTO_API_TOKEN": "s3cret",
		"GITHUB_ACTIONS": "true", "GITHUB_SHA": "abc", "GITHUB_REF_NAME": "main", "GITHUB_RUN_ID": "9",
		"GITHUB_REPOSITORY": "o/r", "RUNNER_OS": "Linux",
	})
	code := a.run(context.Background(), []string{report, "--cycle", "web", "--env", "browser=Firefox"})
	if code != 0 {
		t.Fatalf("exit %d, out %q", code, out)
	}
	if got.auth != "Bearer s3cret" || !strings.HasPrefix(got.agent, "qasight-upload/") {
		t.Fatalf("headers: %q %q", got.auth, got.agent)
	}
	want := map[string]string{
		"cycle": "web", "stage": "junit", "tier": "unit", "ci_provider": "github", "ci_commit_sha": "abc",
		"ci_ref_name": "main", "ci_pipeline_id": "9", "repository": "o/r", "environment": "browser=Firefox",
	}
	if !reflect.DeepEqual(got.fields, want) {
		t.Fatalf("fields\n got %v\nwant %v", got.fields, want)
	}
	if got.files["unit.xml"] != junit {
		t.Fatalf("files: %v", got.files)
	}
	if !strings.Contains(out.String(), "3 test(s), 0 failed") || !strings.Contains(out.String(), "http://x/runs/r1") {
		t.Fatalf("output: %q", out)
	}
	if strings.Contains(out.String(), "s3cret") {
		t.Fatal("token printed")
	}
}

func TestFailedTestExitsOneAndJSONOutput(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	srv, _ := fakeServer(t, func(_ int, w http.ResponseWriter, _ *received) {
		created(w, map[string]any{"run_id": "r2", "cycle": "c", "exit_code": 1, "tests": 2, "passed": 1, "failed": 1, "skipped": 0})
	})
	a, out, _, _ := newApp(nil)
	code := a.run(context.Background(), []string{"--server", srv.URL, "--json", "--cycle", "c", report})
	if code != 1 {
		t.Fatalf("exit %d", code)
	}
	var ev map[string]any
	if err := json.Unmarshal(out.Bytes(), &ev); err != nil {
		t.Fatalf("not one JSON object: %q", out)
	}
	if ev["event"] != "ingest_uploaded" || ev["failed"] != float64(1) || ev["run_id"] != "r2" || ev["run_url"] != nil {
		t.Fatalf("event: %v", ev)
	}
}

func TestRetriesBusyServerThenSucceeds(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	srv, attempts := fakeServer(t, func(n int, w http.ResponseWriter, _ *received) {
		if n < 3 {
			if n == 2 {
				w.Header().Set("Retry-After", "5")
			}
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		created(w, map[string]any{"cycle": "c", "exit_code": 0, "tests": 1, "passed": 1})
	})
	a, _, errb, waits := newApp(nil)
	if code := a.run(context.Background(), []string{"--server", srv.URL, report}); code != 0 {
		t.Fatalf("exit %d: %s", code, errb)
	}
	if *attempts != 3 || len(*waits) != 2 || (*waits)[1] != 5*time.Second {
		t.Fatalf("attempts %d waits %v", *attempts, *waits)
	}
}

func TestGivesUpAfterRetries(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	srv, attempts := fakeServer(t, func(_ int, w http.ResponseWriter, _ *received) {
		w.WriteHeader(http.StatusBadGateway)
	})
	a, out, _, _ := newApp(nil)
	code := a.run(context.Background(), []string{"--server", srv.URL, "--retries", "2", "--json", report})
	if code != exitInfra || *attempts != 3 {
		t.Fatalf("exit %d attempts %d", code, *attempts)
	}
	if !strings.Contains(out.String(), `"code":"infra_failure"`) {
		t.Fatalf("output: %s", out)
	}
}

func TestRefusedTokenIsNotRetried(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	srv, attempts := fakeServer(t, func(_ int, w http.ResponseWriter, _ *received) {
		w.WriteHeader(http.StatusUnauthorized)
		_, _ = w.Write([]byte(`{"error": {"message": "missing or invalid token"}}`))
	})
	a, _, errb, _ := newApp(nil)
	code := a.run(context.Background(), []string{"--server", srv.URL, report})
	if code != exitInfra || *attempts != 1 {
		t.Fatalf("exit %d attempts %d", code, *attempts)
	}
	if !strings.Contains(errb.String(), "missing or invalid token") || !strings.Contains(errb.String(), "check the token") {
		t.Fatalf("stderr: %s", errb)
	}
}

func TestRefusedUploadIsBadInput(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	srv, _ := fakeServer(t, func(_ int, w http.ResponseWriter, _ *received) {
		w.WriteHeader(http.StatusBadRequest)
		_, _ = w.Write([]byte(`{"detail": "unsafe cycle name"}`))
	})
	a, _, errb, _ := newApp(nil)
	if code := a.run(context.Background(), []string{"--server", srv.URL, report}); code != exitInvalid {
		t.Fatalf("exit %d", code)
	}
	if !strings.Contains(errb.String(), "unsafe cycle name") {
		t.Fatalf("stderr: %s", errb)
	}
}

func TestUnreachableServerIsRetriedThenInfra(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := ln.Addr().String()
	ln.Close() // nothing listens there now
	a, _, errb, waits := newApp(nil)
	code := a.run(context.Background(), []string{"--server", "http://" + addr, "--retries", "1", report})
	if code != exitInfra || len(*waits) != 1 || !strings.Contains(errb.String(), "could not reach") {
		t.Fatalf("exit %d waits %v stderr %s", code, *waits, errb)
	}
}

func TestUsageErrors(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	cases := map[string][]string{
		"no server":      {report},
		"bad server":     {"--server", "qa.example.com", report},
		"no files":       {"--server", "http://x"},
		"missing file":   {"--server", "http://x", "nope.xml"},
		"empty glob":     {"--server", "http://x", "nothing/**/*.xml"},
		"bad env":        {"--server", "http://x", "--env", "firefox", report},
		"bad release":    {"--server", "http://x", "--release", "a b", report},
		"unknown flag":   {"--server", "http://x", "--nope", report},
		"negative retry": {"--server", "http://x", "--retries", "-1", report},
	}
	for name, args := range cases {
		a, _, _, _ := newApp(nil)
		if code := a.run(context.Background(), args); code != exitInvalid {
			t.Errorf("%s: exit %d", name, code)
		}
	}
}

func TestDryRunSendsNothing(t *testing.T) {
	report := writeFile(t, filepath.Join(t.TempDir(), "r.xml"), junit)
	srv, attempts := fakeServer(t, func(_ int, w http.ResponseWriter, _ *received) { created(w, nil) })
	a, out, _, _ := newApp(map[string]string{"TESTO_API_TOKEN": "s3cret"})
	if code := a.run(context.Background(), []string{"--server", srv.URL, "--dry-run", "--json", "--cycle", "c", report}); code != 0 {
		t.Fatalf("exit %d", code)
	}
	if *attempts != 0 || strings.Contains(out.String(), "s3cret") || !strings.Contains(out.String(), `"token_set":true`) {
		t.Fatalf("attempts %d out %s", *attempts, out)
	}
}

func TestHelpAndVersion(t *testing.T) {
	a, out, _, _ := newApp(nil)
	if code := a.run(context.Background(), []string{"--help"}); code != 0 || !strings.Contains(out.String(), "Exit codes") {
		t.Fatalf("help: %d %q", code, out)
	}
	a, out, _, _ = newApp(nil)
	if code := a.run(context.Background(), []string{"--version"}); code != 0 || !strings.HasPrefix(out.String(), "qasight-upload ") {
		t.Fatalf("version: %d %q", code, out)
	}
}

func TestExpandArgs(t *testing.T) {
	dir := t.TempDir()
	a := writeFile(t, filepath.Join(dir, "reports", "a.xml"), junit)
	b := writeFile(t, filepath.Join(dir, "reports", "deep", "er", "b.xml"), junit)
	writeFile(t, filepath.Join(dir, "reports", "deep", "notes.txt"), "x")
	r1 := writeFile(t, filepath.Join(dir, "allure-results", "1-result.json"), "{}")
	r2 := writeFile(t, filepath.Join(dir, "allure-results", "shot.png"), "png")
	writeFile(t, filepath.Join(dir, "allure-results", "sub", "skip.json"), "{}")

	got, err := expandArgs([]string{filepath.Join(dir, "reports", "**", "*.xml"), a, filepath.Join(dir, "allure-results")})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{a, b, r1, r2}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("\n got %v\nwant %v", got, want)
	}
}

func TestFlagsAfterFilesAndDoubleDash(t *testing.T) {
	o, err := parseArgs([]string{"a.xml", "--cycle", "c", "b.xml", "--json", "--", "--odd.xml"}, mapEnv(nil))
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(o.files, []string{"a.xml", "b.xml", "--odd.xml"}) || o.cycle != "c" || !o.json {
		t.Fatalf("%+v", o)
	}
}

func TestLimits(t *testing.T) {
	dir := t.TempDir()
	big := filepath.Join(dir, "big.xml")
	f, _ := os.Create(big)
	_ = f.Truncate(maxFileBytes + 1)
	f.Close()
	if err := checkLimits([]string{big}); err == nil {
		t.Fatal("a file over 20 MB passed")
	}
	many := make([]string, maxFiles+1)
	if err := checkLimits(many); err == nil || !strings.Contains(err.Error(), "too many") {
		t.Fatalf("201 files: %v", err)
	}
}

func TestProvenanceForEachCI(t *testing.T) {
	cases := []struct {
		vars map[string]string
		want map[string]string
	}{
		{map[string]string{"GITHUB_ACTIONS": "true", "GITHUB_HEAD_REF": "feat", "GITHUB_REF_NAME": "7/merge", "GITHUB_BASE_REF": "main"},
			map[string]string{"ci_provider": "github", "ci_ref_name": "feat", "ci_base_ref": "main"}},
		{map[string]string{"GITLAB_CI": "true", "CI_PIPELINE_ID": "1", "CI_JOB_ID": "2", "CI_COMMIT_REF_NAME": "dev"},
			map[string]string{"ci_provider": "gitlab", "ci_pipeline_id": "1", "ci_job_id": "2", "ci_ref_name": "dev"}},
		{map[string]string{"JENKINS_URL": "http://j", "GIT_BRANCH": "origin/main", "BUILD_NUMBER": "5"},
			map[string]string{"ci_provider": "jenkins", "ci_ref_name": "main", "ci_job_id": "5"}},
		{map[string]string{"TF_BUILD": "True", "SYSTEM_PULLREQUEST_TARGETBRANCH": "refs/heads/main"},
			map[string]string{"ci_provider": "azure_pipelines", "ci_base_ref": "main"}},
		{map[string]string{"CI": "true"}, map[string]string{"ci_provider": "generic"}},
		{map[string]string{}, nil},
	}
	for _, c := range cases {
		if got := ciProvenance(mapEnv(c.vars)); !reflect.DeepEqual(got, c.want) {
			t.Errorf("%v:\n got %v\nwant %v", c.vars, got, c.want)
		}
	}
}

func TestEnvironmentAndRelease(t *testing.T) {
	e := mapEnv(map[string]string{"TESTO_ENV": "Browser=chrome, os=linux", "CI": "true"})
	if got, _ := resolveEnvironment([]string{"browser=Fire,fox"}, e); got != "browser=Fire fox, os=linux" {
		t.Errorf("flags over TESTO_ENV: %q", got)
	}
	if got, _ := resolveEnvironment(nil, mapEnv(map[string]string{"GITHUB_ACTIONS": "true", "RUNNER_OS": "macOS"})); got != "os=macos" {
		t.Errorf("CI OS default: %q", got)
	}
	if got, _ := resolveEnvironment(nil, mapEnv(map[string]string{"CI": "true", "RUNNER_OS": "Linux", "TESTO_AUTO_METADATA": "0"})); got != "" {
		t.Errorf("auto metadata off: %q", got)
	}
	if got, _ := resolveEnvironment(nil, mapEnv(nil)); got != "" {
		t.Errorf("outside CI: %q", got)
	}
	tag := mapEnv(map[string]string{"GITHUB_ACTIONS": "true", "GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v2.3.0"})
	if got, _ := resolveRelease("", tag); got != "v2.3.0" {
		t.Errorf("CI tag: %q", got)
	}
	if got, _ := resolveRelease("", mapEnv(map[string]string{"TESTO_RELEASE": "2.3", "CI_COMMIT_TAG": "v9"})); got != "2.3" {
		t.Errorf("TESTO_RELEASE wins: %q", got)
	}
	if got, _ := resolveRelease("", mapEnv(map[string]string{"BUILD_SOURCEBRANCH": "refs/tags/v1"})); got != "v1" {
		t.Errorf("Azure tag: %q", got)
	}
}
