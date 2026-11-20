package main

// The upload's form fields besides the files: this CI job's provenance, the
// repository, the environment and the release. The same rules as the Python
// client (qasight/services/ci_provenance.py, run_environment.py, run_release.py),
// so a run uploaded with qasight-upload looks exactly like one from
// `qasight ingest --server`.

import (
	"fmt"
	"regexp"
	"runtime"
	"sort"
	"strings"
)

// env is a lookup over environment variables (os.Getenv in main, a map in tests).
type env func(string) string

func isTrue(v string) bool { return strings.EqualFold(strings.TrimSpace(v), "true") }

func branchName(ref string) string {
	for _, prefix := range []string{"refs/heads/", "origin/"} {
		if strings.HasPrefix(ref, prefix) {
			return strings.TrimPrefix(ref, prefix)
		}
	}
	return ref
}

func firstSet(e env, names ...string) string {
	for _, n := range names {
		if v := e(n); v != "" {
			return v
		}
	}
	return ""
}

// ciProvenance is the CI provider and the job's ids, commit and branches; nil outside CI.
func ciProvenance(e env) map[string]string {
	var m map[string]string
	switch {
	case isTrue(e("GITHUB_ACTIONS")):
		m = map[string]string{
			"ci_provider":    "github",
			"ci_pipeline_id": e("GITHUB_RUN_ID"),
			"ci_job_id":      e("GITHUB_JOB"),
			"ci_commit_sha":  e("GITHUB_SHA"),
			// On pull_request events GITHUB_REF_NAME is "<number>/merge"; HEAD_REF is the branch.
			"ci_ref_name": firstSet(e, "GITHUB_HEAD_REF", "GITHUB_REF_NAME"),
			"ci_base_ref": e("GITHUB_BASE_REF"),
		}
	case isTrue(e("GITLAB_CI")):
		m = map[string]string{
			"ci_provider":    "gitlab",
			"ci_pipeline_id": e("CI_PIPELINE_ID"),
			"ci_job_id":      e("CI_JOB_ID"),
			"ci_commit_sha":  e("CI_COMMIT_SHA"),
			"ci_ref_name":    e("CI_COMMIT_REF_NAME"),
			"ci_base_ref":    e("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
		}
	case isTrue(e("BUILDKITE")):
		m = map[string]string{
			"ci_provider":    "buildkite",
			"ci_pipeline_id": e("BUILDKITE_BUILD_ID"),
			"ci_job_id":      e("BUILDKITE_JOB_ID"),
			"ci_commit_sha":  e("BUILDKITE_COMMIT"),
			"ci_ref_name":    e("BUILDKITE_BRANCH"),
			"ci_base_ref":    e("BUILDKITE_PULL_REQUEST_BASE_BRANCH"),
		}
	case isTrue(e("CIRCLECI")):
		m = map[string]string{
			"ci_provider":    "circleci",
			"ci_pipeline_id": e("CIRCLE_WORKFLOW_ID"),
			"ci_job_id":      e("CIRCLE_BUILD_NUM"),
			"ci_commit_sha":  e("CIRCLE_SHA1"),
			"ci_ref_name":    e("CIRCLE_BRANCH"),
		}
	case e("JENKINS_URL") != "":
		m = map[string]string{
			"ci_provider":    "jenkins",
			"ci_pipeline_id": firstSet(e, "BUILD_TAG", "BUILD_ID"),
			"ci_job_id":      e("BUILD_NUMBER"),
			"ci_commit_sha":  e("GIT_COMMIT"),
			"ci_ref_name":    branchName(firstSet(e, "GIT_BRANCH", "BRANCH_NAME")),
			"ci_base_ref":    e("CHANGE_TARGET"),
		}
	case e("TF_BUILD") == "True" || e("TF_BUILD") == "true":
		m = map[string]string{
			"ci_provider":    "azure_pipelines",
			"ci_pipeline_id": e("BUILD_BUILDID"),
			"ci_job_id":      e("SYSTEM_JOBID"),
			"ci_commit_sha":  e("BUILD_SOURCEVERSION"),
			"ci_ref_name":    e("BUILD_SOURCEBRANCHNAME"),
			"ci_base_ref":    branchName(e("SYSTEM_PULLREQUEST_TARGETBRANCH")),
		}
	case isTrue(e("CI")):
		m = map[string]string{"ci_provider": "generic"}
	default:
		return nil
	}
	for k, v := range m {
		if v == "" {
			delete(m, k)
		}
	}
	return m
}

func ciRepository(e env) string {
	return firstSet(e, "GITHUB_REPOSITORY", "CI_PROJECT_PATH", "BUILDKITE_REPO", "CIRCLE_PROJECT_REPONAME")
}

// autoMetadata is false when TESTO_AUTO_METADATA turns off the CI tag and OS defaults.
func autoMetadata(e env) bool {
	switch strings.ToLower(strings.TrimSpace(e("TESTO_AUTO_METADATA"))) {
	case "0", "false", "off", "no":
		return false
	}
	return true
}

// ciTag is the git tag this CI job builds ("v2.3.0"), or "" for a branch or pull request build.
func ciTag(e env) string {
	if !autoMetadata(e) {
		return ""
	}
	if isTrue(e("GITHUB_ACTIONS")) {
		if e("GITHUB_REF_TYPE") == "tag" {
			return e("GITHUB_REF_NAME")
		}
		return ""
	}
	for _, name := range []string{"CI_COMMIT_TAG", "CIRCLE_TAG", "BUILDKITE_TAG", "TAG_NAME"} {
		if v := strings.TrimSpace(e(name)); v != "" {
			return v
		}
	}
	if src := e("BUILD_SOURCEBRANCH"); strings.HasPrefix(src, "refs/tags/") {
		return strings.TrimPrefix(src, "refs/tags/")
	}
	return ""
}

var osNames = map[string]string{
	"linux": "linux", "windows": "windows", "windows_nt": "windows", "macos": "macos", "darwin": "macos",
}

// ciOS is the CI runner's OS ("linux", "windows", "macos"); "" outside CI.
func ciOS(e env) string {
	if !autoMetadata(e) || ciProvenance(e) == nil {
		return ""
	}
	name := strings.ToLower(strings.TrimSpace(firstSet(e, "RUNNER_OS", "AGENT_OS")))
	if name == "" {
		name = runtime.GOOS
	}
	if mapped, ok := osNames[name]; ok {
		return mapped
	}
	return name
}

// --- environment: "browser=firefox, os=linux" ----------------------------------------

const (
	envMaxKeys    = 8
	envKeyLimit   = 40
	envValueLimit = 60
)

var envKeyChars = regexp.MustCompile(`[^a-z0-9_.-]+`)

func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

func cleanEnvKey(k string) string {
	return truncate(strings.Trim(envKeyChars.ReplaceAllString(strings.ToLower(strings.TrimSpace(k)), "-"), "-"), envKeyLimit)
}

func cleanEnvValue(v string) string {
	v = strings.NewReplacer(",", " ", "=", " ").Replace(v)
	return truncate(strings.Join(strings.Fields(v), " "), envValueLimit)
}

// envPairs reads KEY=VALUE pairs; with strict, a bad pair is an error instead of skipped.
func envPairs(pairs []string, strict bool) (map[string]string, error) {
	out := map[string]string{}
	for _, pair := range pairs {
		key, value, found := strings.Cut(pair, "=")
		name, cleaned := cleanEnvKey(key), cleanEnvValue(value)
		if !found || name == "" || cleaned == "" {
			if strict {
				return nil, fmt.Errorf("expected KEY=VALUE, got %q", pair)
			}
			continue
		}
		out[name] = cleaned
	}
	return out, nil
}

// canonicalEnv is the stored form: keys sorted, at most eight, "" when empty.
func canonicalEnv(m map[string]string) string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if len(keys) > envMaxKeys {
		keys = keys[:envMaxKeys]
	}
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, k+"="+m[k])
	}
	return strings.Join(parts, ", ")
}

// resolveEnvironment: TESTO_ENV with --env flags merged over it (flags win), else the CI runner's OS.
func resolveEnvironment(flags []string, e env) (string, error) {
	fromFlags, err := envPairs(flags, true)
	if err != nil {
		return "", err
	}
	merged, _ := envPairs(strings.Split(e("TESTO_ENV"), ","), false)
	for k, v := range fromFlags {
		merged[k] = v
	}
	if s := canonicalEnv(merged); s != "" {
		return s, nil
	}
	if osName := ciOS(e); osName != "" {
		return canonicalEnv(map[string]string{"os": osName}), nil
	}
	return "", nil
}

// --- release -------------------------------------------------------------------------

const releaseMaxLength = 60

var releaseChars = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+\-]*$`)

func validRelease(v string) bool {
	return len(v) <= releaseMaxLength && releaseChars.MatchString(v)
}

// resolveRelease: --release (an error when unusable), else TESTO_RELEASE, else the CI tag.
func resolveRelease(flag string, e env) (string, error) {
	if v := strings.TrimSpace(flag); v != "" {
		if !validRelease(v) {
			return "", fmt.Errorf("%q: use up to %d letters, digits, '.', '-', '+' or '_'", truncate(v, releaseMaxLength), releaseMaxLength)
		}
		return v, nil
	}
	for _, v := range []string{strings.TrimSpace(e("TESTO_RELEASE")), strings.TrimSpace(ciTag(e))} {
		if v != "" && validRelease(v) {
			return v, nil
		}
	}
	return "", nil
}
