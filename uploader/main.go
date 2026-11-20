// qasight-upload sends test result files from a CI job to a QASight server, as
// `qasight ingest --server` does, without Python: one static binary.
//
//	qasight-upload --server https://qa.example.com --cycle nightly 'reports/**/*.xml'
//
// Exit codes follow the qasight CLI: 0 every test passed, 1 a test failed,
// 2 bad input (flags, files, or a refused upload), 3 the server could not be
// reached or refused the token, 4 an unexpected error.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"time"
)

// version is set at build time: -ldflags "-X main.version=1.7.0".
var version = "dev"

const (
	exitOK         = 0
	exitInvalid    = 2
	exitInfra      = 3
	exitInternal   = 4
	exitInterrupt  = 130
	defaultRetries = 3
)

const usage = `qasight-upload: send test result files to a QASight server, with nothing to install.

Usage:
  qasight-upload [flags] FILE|FOLDER|GLOB...

Files: JUnit XML, Playwright / Jest / Vitest JSON, .NET .trx, go test -json output,
Robot Framework output.xml, or an allure-results folder (the files directly in it).
Quote globs to let qasight-upload expand them: 'reports/**/*.xml'.

Flags:
  --server URL        QASight server (default: $TESTO_SERVER_URL)
  --token TOKEN       API token (default: $TESTO_API_TOKEN)
  --cycle NAME        Cycle to record the run under (default: the current folder's name)
  --stage NAME        Stage the tests are grouped under (default: junit)
  --tier TIER         unit, integration or e2e (default: unit)
  --env KEY=VALUE     What the run tested on, repeatable (default: $TESTO_ENV, else os=<CI runner OS>)
  --release NAME      The release it tested, e.g. 2.3 (default: $TESTO_RELEASE, else the CI tag)
  --repository NAME   owner/name (default: from the CI provider's variables)
  --json              Print one JSON object on stdout instead of text (also --ci)
  --retries N         Retries when the server is unreachable or busy (default: 3)
  --timeout DURATION  Time allowed for each attempt (default: 2m)
  --dry-run           Show the files and fields that would be sent, and send nothing
  --version           Print the version

Exit codes: 0 every test passed, 1 a test failed, 2 bad input, 3 server unreachable or
token refused, 4 unexpected error.
Docs: https://github.com/arieleven/qasight/blob/main/docs/Processes%20%26%20Guides/Upload%20Without%20Python.md
`

type options struct {
	server, token, cycle, stage, tier, release, repository string
	envs                                                   []string
	json, dryRun, showVersion                              bool
	retries                                                int
	timeout                                                time.Duration
	files                                                  []string
}

type multiFlag []string

func (m *multiFlag) String() string     { return strings.Join(*m, ",") }
func (m *multiFlag) Set(v string) error { *m = append(*m, v); return nil }

// parseArgs reads flags anywhere among the file arguments ("--" ends the flags).
func parseArgs(args []string, e env) (*options, error) {
	o := &options{}
	fs := flag.NewFlagSet("qasight-upload", flag.ContinueOnError)
	fs.SetOutput(io.Discard)
	fs.StringVar(&o.server, "server", e("TESTO_SERVER_URL"), "")
	fs.StringVar(&o.token, "token", e("TESTO_API_TOKEN"), "")
	fs.StringVar(&o.cycle, "cycle", "", "")
	fs.StringVar(&o.stage, "stage", "junit", "")
	fs.StringVar(&o.tier, "tier", "unit", "")
	fs.StringVar(&o.release, "release", "", "")
	fs.StringVar(&o.repository, "repository", "", "")
	envs := multiFlag{}
	fs.Var(&envs, "env", "")
	fs.BoolVar(&o.json, "json", false, "")
	fs.BoolVar(&o.json, "ci", false, "")
	fs.BoolVar(&o.dryRun, "dry-run", false, "")
	fs.BoolVar(&o.showVersion, "version", false, "")
	fs.IntVar(&o.retries, "retries", defaultRetries, "")
	fs.DurationVar(&o.timeout, "timeout", 2*time.Minute, "")
	rest := args
	for len(rest) > 0 {
		before := rest
		if err := fs.Parse(rest); err != nil {
			return nil, err
		}
		rest = fs.Args()
		if consumed := before[:len(before)-len(rest)]; len(consumed) > 0 && consumed[len(consumed)-1] == "--" {
			o.files = append(o.files, rest...) // after "--" everything is a file
			break
		}
		if len(rest) > 0 {
			o.files = append(o.files, rest[0])
			rest = rest[1:]
		}
	}
	o.envs = envs
	if o.retries < 0 {
		return nil, fmt.Errorf("--retries must be 0 or more")
	}
	if o.timeout <= 0 {
		return nil, fmt.Errorf("--timeout must be more than 0, like 90s or 5m")
	}
	return o, nil
}

func serverURLProblem(raw string) string {
	if strings.TrimSpace(raw) == "" {
		return "no server: pass --server https://qasight.example.com or set $TESTO_SERVER_URL"
	}
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || (u.Scheme != "http" && u.Scheme != "https") || u.Host == "" {
		return fmt.Sprintf("--server must be a URL like https://qasight.example.com (or $TESTO_SERVER_URL); got %q", raw)
	}
	return ""
}

var cycleChars = regexp.MustCompile(`[^A-Za-z0-9_.-]+`)

// defaultCycle is the current folder's name, as `qasight ingest` and `pytest --qasight` name it.
func defaultCycle() string {
	dir, err := os.Getwd()
	if err != nil {
		return "tests"
	}
	name := strings.ToLower(strings.Trim(cycleChars.ReplaceAllString(filepath.Base(dir), "-"), "-."))
	if name == "" {
		return "tests"
	}
	return name
}

// formFields are the upload's fields besides the files.
func formFields(o *options, e env) (map[string]string, error) {
	fields := map[string]string{
		"cycle": strings.TrimSpace(o.cycle),
		"stage": strings.TrimSpace(o.stage),
		"tier":  strings.ToLower(strings.TrimSpace(o.tier)),
	}
	if fields["cycle"] == "" {
		fields["cycle"] = defaultCycle()
	}
	for k, v := range ciProvenance(e) {
		fields[k] = v
	}
	repo := strings.TrimSpace(o.repository)
	if repo == "" {
		repo = ciRepository(e)
	}
	if repo != "" {
		fields["repository"] = repo
	}
	environment, err := resolveEnvironment(o.envs, e)
	if err != nil {
		return nil, fmt.Errorf("--env: %w", err)
	}
	if environment != "" {
		fields["environment"] = environment
	}
	release, err := resolveRelease(o.release, e)
	if err != nil {
		return nil, fmt.Errorf("--release: %w", err)
	}
	if release != "" {
		fields["release"] = release
	}
	return fields, nil
}

type app struct {
	stdout, stderr io.Writer
	env            env
	client         *http.Client
	sleep          func(time.Duration)
	baseWait       time.Duration
}

func (a *app) emit(v map[string]any) {
	b, _ := json.Marshal(v)
	fmt.Fprintln(a.stdout, string(b))
}

func (a *app) fail(o *options, code int, msg string) int {
	if o != nil && o.json {
		kind := map[int]string{exitInvalid: "invalid_input", exitInfra: "infra_failure"}[code]
		if kind == "" {
			kind = "internal_error"
		}
		a.emit(map[string]any{"event": "error", "code": kind, "message": msg})
	} else {
		fmt.Fprintf(a.stderr, "error: %s\n", msg)
	}
	return code
}

func (a *app) run(ctx context.Context, args []string) int {
	o, err := parseArgs(args, a.env)
	if err == flag.ErrHelp {
		io.WriteString(a.stdout, usage)
		return exitOK
	}
	if err != nil {
		jsonWanted := false
		for _, arg := range args {
			jsonWanted = jsonWanted || arg == "--json" || arg == "-json" || arg == "--ci" || arg == "-ci"
		}
		return a.fail(&options{json: jsonWanted}, exitInvalid, err.Error()+" (qasight-upload --help lists the flags)")
	}
	if o.showVersion {
		fmt.Fprintf(a.stdout, "qasight-upload %s\n", version)
		return exitOK
	}
	if p := serverURLProblem(o.server); p != "" {
		return a.fail(o, exitInvalid, p)
	}
	if len(o.files) == 0 {
		return a.fail(o, exitInvalid, "no result files given: qasight-upload [flags] 'reports/**/*.xml'")
	}
	files, err := expandArgs(o.files)
	if err != nil {
		return a.fail(o, exitInvalid, err.Error())
	}
	if err := checkLimits(files); err != nil {
		return a.fail(o, exitInvalid, err.Error())
	}
	fields, err := formFields(o, a.env)
	if err != nil {
		return a.fail(o, exitInvalid, err.Error())
	}
	server := strings.TrimRight(strings.TrimSpace(o.server), "/")
	u := &uploader{
		client:   a.client,
		server:   server,
		token:    strings.TrimSpace(o.token),
		retries:  o.retries,
		baseWait: a.baseWait,
		agent:    "qasight-upload/" + version,
		sleep:    a.sleep,
		log: func(s string) {
			if !o.json {
				fmt.Fprintf(a.stderr, "warning: %s\n", s)
			}
		},
	}

	if o.dryRun {
		if o.json {
			a.emit(map[string]any{"event": "dry_run", "url": u.url(), "fields": fields, "files": files, "token_set": u.token != ""})
		} else {
			fmt.Fprintf(a.stdout, "Would send %d file(s) to %s (token %s):\n", len(files), u.url(), map[bool]string{true: "set", false: "not set"}[u.token != ""])
			for _, f := range files {
				fmt.Fprintf(a.stdout, "  %s\n", f)
			}
			for _, k := range sortedKeys(fields) {
				fmt.Fprintf(a.stdout, "  %s=%s\n", k, fields[k])
			}
		}
		return exitOK
	}

	body, contentType, err := buildBody(fields, files)
	if err != nil {
		return a.fail(o, exitInvalid, err.Error())
	}
	a.client.Timeout = o.timeout
	res, err := u.send(ctx, body.Bytes(), contentType)
	if ctx.Err() != nil {
		return a.fail(o, exitInterrupt, "stopped before the upload finished")
	}
	if err != nil {
		if ue, ok := err.(*uploadError); ok && ue.badInput {
			return a.fail(o, exitInvalid, err.Error())
		}
		return a.fail(o, exitInfra, err.Error())
	}

	if o.json {
		out := map[string]any{
			"event": "ingest_uploaded", "server": server, "run_id": res.RunID, "cycle": res.Cycle,
			"exit_code": res.ExitCode, "tests": res.Tests, "passed": res.Passed, "failed": res.Failed,
			"skipped": res.Skipped, "run_url": res.RunURL,
		}
		a.emit(out)
	} else {
		run := "not saved"
		if res.RunID != nil && *res.RunID != "" {
			run = *res.RunID
		}
		fmt.Fprintf(a.stdout, "%d test(s), %d failed, recorded on %s as cycle %s (run %s)\n",
			res.Tests, res.Failed, server, res.Cycle, run)
		if res.RunURL != nil && *res.RunURL != "" {
			fmt.Fprintln(a.stdout, *res.RunURL)
		}
	}
	return res.ExitCode
}

func sortedKeys(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	a := &app{
		stdout:   os.Stdout,
		stderr:   os.Stderr,
		env:      os.Getenv,
		client:   &http.Client{},
		sleep:    time.Sleep,
		baseWait: 2 * time.Second,
	}
	defer func() {
		if r := recover(); r != nil {
			fmt.Fprintf(os.Stderr, "error: unexpected failure: %v\n", r)
			os.Exit(exitInternal)
		}
	}()
	os.Exit(a.run(ctx, os.Args[1:]))
}
