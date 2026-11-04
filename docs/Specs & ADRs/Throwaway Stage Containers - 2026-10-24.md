---
type: spec
status: current
created: 2026-10-24
updated: 2026-11-04
related: "Team Server, Switchable Team Features, Project Review"
---

# Throwaway Stage Containers - 2026-10-24

[Index](../Index.md) · [Team Server § Runs in throwaway containers](../Processes%20&%20Guides/Team%20Server.md#runs-in-throwaway-containers) · [Switchable Team Features - 2026-10-24](Switchable%20Team%20Features%20-%202026-10-24.md)

## Context

After accounts, throttled sign-in and a separate report origin, the fifth [Project Review](../project-review.md) pass named one gap left between the team server and a 10: a `runner` can start any command as the API's own user, inside the API's container, next to its token, its database URL and every other run's files. The API is a command runner by design, so the fix is not to stop it running commands but to run them somewhere that holds nothing worth taking. As with the other team features, one developer on a laptop and a small team on a trusted network must not pay for it.

## Decision

A switch, `TESTO_RUNNER=host|container`, default `host`.

- **`host`**: unchanged. The executor starts the stage with `subprocess.Popen`.
- **`container`**: the executor hands the stage to a **runner service** over HTTP (`TESTO_RUNNER_URL`, bearer `TESTO_RUNNER_TOKEN`). The service starts it with `docker run --rm` in a fresh container and streams the output back.

### Where the code lives

- `qasight/engine/backends/__init__.py`: `launch()` picks the backend. Both return something the executor treats like a `Popen`: a `stdout` to drain, `wait(timeout)`, `send_signal`/`kill`. So the log buffer, the timeout contract (rc `124`), the post-stage JUnit import and quarantine are the same code for both.
- `backends/container.py`: `RemoteStageProcess`, the client. It rewrites the tool to its name (`/usr/local/bin/python` becomes `python`) so the image's own `PATH` finds it.
- `backends/runner_service.py`: the service. Standard library only, so its image is a slim Python with the Docker client and this one file (`Dockerfile.qasight-runner-service`). Also installed as `qasight-runner-service`.

### What the container gets

The service decides, not the request:

- **Image**: `TESTO_RUNNER_IMAGE` (default the `qasight-runner` image). One image per service; teams that need Playwright or other tools build theirs `FROM` it.
- **Mounts**: `TESTO_RUNNER_MOUNTS`, at the same paths the API uses, so results land where the API reads them. The stage's directory must be inside one of them.
- **Commands**: `TESTO_RUNNER_ALLOWED_COMMANDS`, by name (default `python`, `pytest`, `behave`, `behavex`, `node`, `npm`, `npx`; `*` allows any, still inside the container).
- **Limits**: `--cap-drop ALL`, `no-new-privileges`, `--pids-limit` (512), `--init`, a fixed user (`10001:10001`, the images' `qasight`), and optional `TESTO_RUNNER_MEMORY`, `TESTO_RUNNER_CPUS`, `TESTO_RUNNER_NETWORK` (`none` cuts the stage off the network). At most `TESTO_RUNNER_MAX_RUNS` (4) at once.
- **Environment**: only the stage's own variables: its `env:` block and what QASight and the adapter set (`TESTO_SHARED_ALLURE_RESULTS_DIR` and so on). The executor tracks which keys those are, so the server's own environment never leaves it. Values go to `docker run` as `-e NAME` through the client's environment, not on its command line, and variables that would steer the Docker client itself (`DOCKER_*`, `PATH`, `HOME`) are dropped.

### Wire format and lifetime

`POST /runs` answers with a stream of frames (one byte of kind, four of length, the payload): output (`o`, empty ones are heartbeats every 15 s of silence) and the exit code (`x`, last). `DELETE /runs/<name>` sends `TERM` or `KILL`, which is what the executor's timeout path calls. If the API closes the connection or dies, the service sees the socket close and kills the container. On startup the service removes containers a crashed earlier service left behind (label `testo.runner=1`). A lost connection without an exit code fails the stage with `125`, like `docker run` does when it can't start a container; an unreachable or refusing service fails it with `125` and the reason as the stage error.

## Options considered

- **Give the API the Docker socket.** Simplest, but whoever gets code execution in the API (the internet-facing part) gets root on the host. Rejected: the point is that the API holds nothing dangerous.
- **gVisor, Firecracker or Kubernetes Jobs.** Stronger isolation or better scaling, but each is a platform choice the project shouldn't make for its users. The runner service is a small HTTP contract, so a Kubernetes-backed runner can implement the same three endpoints later.
- **Run the whole cycle in one container.** Fewer containers, but the API would no longer see stage boundaries, logs and timeouts the same way in both modes. Rejected: per stage keeps one executor.
- **Forward the API's environment minus a deny-list.** Rejected: an allow-list of what the stage itself added can't leak a secret someone adds to the server later.
- **On by default for the team server.** Rejected: it needs the Docker socket mounted into a second container, which a server that only takes uploads doesn't need.

## Consequences

- Default `host`: nothing changes for `qasight run`, the local API or a server that only takes uploads (`qasight ingest` never starts a process).
- The runner service holds the Docker socket, which is root on the host. It has no published port, takes requests only with its own token, and can only start the configured image with the configured mounts. Rootless Docker or Podman (`DOCKER_HOST`) narrows this further.
- Stages see the repos and artifacts volumes and nothing else of the server: not its token, database, history or other volumes. They can still write to the shared artifacts volume, so one run's stage could disturb another run's artifacts; separate volumes per run would need Docker's volume subpaths and are left for later.
- The stage image must carry the tools the stages use. The default one has Python, pytest, Behave and BehaveX.
- A new image, `ghcr.io/arieleven/qasight-runner-service`, is built by `docker-publish.yml` with the others; like each new package it starts private on GitHub until the owner makes it public.
- Tests: `tests/unit/qasight/engine/test_container_runner.py` runs the real service with a stand-in `docker` (stream, exit code, env boundary, timeout, a vanished API, refusals), and `tests/contract/api/test_container_runner_contract.py` checks the API refuses to start half configured. It was also checked by hand against a real Docker daemon: no capabilities in the container, no server token, the stage's variables present, results written to the shared volume, timeout and disconnect both removing the container.
