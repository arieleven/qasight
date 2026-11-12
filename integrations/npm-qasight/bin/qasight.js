#!/usr/bin/env node
"use strict";
// `npx qasight ...`: the QASight CLI (a Python package) for projects without Python.
//
// It runs `uv tool run --from qasight qasight ...`. uv (https://docs.astral.sh/uv/)
// is one binary that fetches a Python and the package into its cache, so after the
// first run it starts in well under a second. uv is taken from $QASIGHT_UV, then
// PATH, then this package's cache folder; when there is none it is installed there
// with uv's official installer (nothing is added to your shell profile).
//
// $QASIGHT_VERSION pins the qasight version (default: the newest uv has cached;
// `QASIGHT_VERSION=latest` checks PyPI). Install messages go to stderr, so
// `--ci` output on stdout stays machine-readable.

const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const WINDOWS = process.platform === "win32";
// Commands that need the web server's dependencies (`pip install 'qasight[api]'`).
const API_COMMANDS = new Set(["ui", "serve", "demo"]);

function exe(name) {
  return WINDOWS ? `${name}.exe` : name;
}

function isFile(file) {
  try {
    return fs.statSync(file).isFile();
  } catch {
    return false;
  }
}

function onPath(name, env = process.env) {
  for (const dir of (env.PATH || "").split(path.delimiter)) {
    if (dir && isFile(path.join(dir, exe(name)))) return path.join(dir, exe(name));
  }
  return null;
}

function cacheDir(env = process.env) {
  if (env.QASIGHT_NPM_HOME) return env.QASIGHT_NPM_HOME;
  const base = WINDOWS
    ? env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local")
    : env.XDG_CACHE_HOME || path.join(os.homedir(), ".cache");
  return path.join(base, "qasight-npm");
}

function findUv(env = process.env) {
  if (env.QASIGHT_UV) return env.QASIGHT_UV;
  const cached = path.join(cacheDir(env), "bin", exe("uv"));
  return onPath("uv", env) || (isFile(cached) ? cached : null);
}

function installUv(env = process.env) {
  const dir = path.join(cacheDir(env), "bin");
  fs.mkdirSync(dir, { recursive: true });
  process.stderr.write(`qasight: installing uv into ${dir} (once)\n`);
  const installEnv = { ...env, UV_INSTALL_DIR: dir, UV_NO_MODIFY_PATH: "1" };
  const [cmd, args] = WINDOWS
    ? ["powershell", ["-ExecutionPolicy", "ByPass", "-c", "irm https://astral.sh/uv/install.ps1 | iex"]]
    : ["sh", ["-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]];
  const result = spawnSync(cmd, args, { env: installEnv, stdio: ["ignore", 2, 2] });
  const uv = path.join(dir, exe("uv"));
  if (result.status !== 0 || !isFile(uv)) {
    process.stderr.write(
      "qasight: couldn't install uv. Install it (https://docs.astral.sh/uv/getting-started/installation/)" +
        " or Python, then run `uvx qasight` or `pip install qasight`.\n",
    );
    return null;
  }
  return uv;
}

function packageSpec(args, env = process.env) {
  const extras = API_COMMANDS.has(args[0]) ? "[api]" : "";
  const version = (env.QASIGHT_VERSION || "").trim();
  if (version === "latest") return `qasight${extras}@latest`;
  return `qasight${extras}${version ? `==${version}` : ""}`;
}

function uvArgs(args, env = process.env) {
  return ["tool", "run", "--from", packageSpec(args, env), "qasight", ...args];
}

function main(args) {
  const uv = findUv() || installUv();
  if (!uv) return 1;
  const result = spawnSync(uv, uvArgs(args), { stdio: "inherit" });
  if (result.error) {
    process.stderr.write(`qasight: couldn't start ${uv}: ${result.error.message}\n`);
    return 1;
  }
  if (result.signal) {
    process.kill(process.pid, result.signal);
    return 1;
  }
  return result.status ?? 1;
}

module.exports = { cacheDir, findUv, packageSpec, uvArgs };

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2));
}
