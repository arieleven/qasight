package main

// Turning the command's arguments into result files: a file, a folder (the
// files directly in it, like an allure-results folder) or a glob, with `**`
// for any depth, as `qasight ingest` reads them. Quoted globs work the same on
// Windows, where the shell doesn't expand them.

import (
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strings"
)

// The server's limits (qasight/services/junit_ingest.py), checked before sending
// so an oversized upload fails at once with a clear message.
const (
	maxFiles      = 200
	maxFileBytes  = 20 * 1024 * 1024
	maxTotalBytes = 100 * 1024 * 1024
)

func hasMeta(s string) bool { return strings.ContainsAny(s, "*?[") }

// expandArgs returns the files the arguments name, in order, without duplicates.
func expandArgs(args []string) ([]string, error) {
	var out []string
	seen := map[string]bool{}
	add := func(p string) {
		clean := filepath.Clean(p)
		if !seen[clean] {
			seen[clean] = true
			out = append(out, clean)
		}
	}
	for _, raw := range args {
		arg := strings.TrimSpace(raw)
		if arg == "" {
			continue
		}
		// The literal path first, so a file name with "[" or "*" in it still works.
		if info, err := os.Stat(arg); err == nil {
			if info.IsDir() {
				entries, err := os.ReadDir(arg)
				if err != nil {
					return nil, err
				}
				n := 0
				for _, entry := range entries {
					if entry.Type().IsRegular() {
						add(filepath.Join(arg, entry.Name()))
						n++
					}
				}
				if n == 0 {
					return nil, fmt.Errorf("folder %s has no files in it", arg)
				}
			} else {
				add(arg)
			}
			continue
		}
		if !hasMeta(arg) {
			return nil, fmt.Errorf("%s: no such file or folder", arg)
		}
		matches, err := glob(arg)
		if err != nil {
			return nil, err
		}
		if len(matches) == 0 {
			return nil, fmt.Errorf("no file matches %s", arg)
		}
		for _, m := range matches {
			add(m)
		}
	}
	return out, nil
}

// glob matches pattern against files; "**" stands for any number of folders, none included.
func glob(pattern string) ([]string, error) {
	slashed := filepath.ToSlash(pattern)
	if !strings.Contains(slashed, "**") {
		matches, err := filepath.Glob(pattern)
		if err != nil {
			return nil, fmt.Errorf("bad pattern %s: %w", pattern, err)
		}
		return regularFiles(matches), nil
	}
	parts := strings.Split(slashed, "/")
	// The folder to walk: the leading parts without wildcards.
	base := 0
	for base < len(parts) && !hasMeta(parts[base]) {
		base++
	}
	root := strings.Join(parts[:base], "/")
	if root == "" {
		if strings.HasPrefix(slashed, "/") {
			root = "/"
		} else {
			root = "."
		}
	}
	rest := parts[base:]
	for _, p := range rest {
		if _, err := path.Match(p, ""); err != nil && p != "**" {
			return nil, fmt.Errorf("bad pattern %s: %w", pattern, err)
		}
	}
	var out []string
	err := filepath.WalkDir(filepath.FromSlash(root), func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			if p == filepath.FromSlash(root) {
				return err
			}
			return nil // an unreadable folder somewhere below: skip it
		}
		if !d.Type().IsRegular() {
			return nil
		}
		rel, err := filepath.Rel(filepath.FromSlash(root), p)
		if err != nil {
			return nil
		}
		if matchParts(rest, strings.Split(filepath.ToSlash(rel), "/")) {
			out = append(out, p)
		}
		return nil
	})
	if err != nil && !os.IsNotExist(err) {
		return nil, err
	}
	sort.Strings(out)
	return out, nil
}

func matchParts(pattern, name []string) bool {
	if len(pattern) == 0 {
		return len(name) == 0
	}
	if pattern[0] == "**" {
		for i := 0; i <= len(name); i++ {
			if matchParts(pattern[1:], name[i:]) {
				return true
			}
		}
		return false
	}
	if len(name) == 0 {
		return false
	}
	ok, _ := path.Match(pattern[0], name[0])
	return ok && matchParts(pattern[1:], name[1:])
}

func regularFiles(paths []string) []string {
	var out []string
	for _, p := range paths {
		if info, err := os.Stat(p); err == nil && info.Mode().IsRegular() {
			out = append(out, p)
		}
	}
	sort.Strings(out)
	return out
}

// checkLimits refuses an upload the server would refuse, before reading it into memory.
func checkLimits(files []string) error {
	if len(files) > maxFiles {
		return fmt.Errorf("too many files (%d; the server takes at most %d)", len(files), maxFiles)
	}
	var total int64
	for _, f := range files {
		info, err := os.Stat(f)
		if err != nil {
			return err
		}
		if info.Size() > maxFileBytes {
			return fmt.Errorf("%s is larger than %d MB, the server's limit for one file", f, maxFileBytes/(1024*1024))
		}
		total += info.Size()
	}
	if total > maxTotalBytes {
		return fmt.Errorf("the files add up to more than %d MB, the server's limit for one upload", maxTotalBytes/(1024*1024))
	}
	return nil
}
