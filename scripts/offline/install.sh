#!/usr/bin/env bash
# Install a QASight offline bundle (made by scripts/offline/bundle.sh) on a
# machine with no internet. Run it from the unpacked bundle folder:
#
#   ./install.sh                                      # CLI into ./venv, images into local Docker
#   ./install.sh --registry registry.example.local/qa # and push the images to your registry
#   ./install.sh --no-venv                            # pip install into the current Python
#
# Guide: docs/Processes & Guides/Closed Network Install.md
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

usage() {
  cat <<'EOF'
Usage: ./install.sh [options]

  --venv DIR       install the CLI into this virtualenv (default: ./venv)
  --no-venv        install into the current Python instead (pip install --user if not root)
  --no-cli         skip the CLI
  --no-images      skip loading the Docker images
  --registry R     after loading, tag and push each image to R (e.g. registry.example.local/qa)
  -h, --help       this help

Set PYTHON to pick the interpreter (default: python3).
EOF
}

VENV="venv"
USE_VENV=1
CLI=1
IMAGES=1
REGISTRY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --venv) VENV="${2:?--venv needs a value}"; shift 2 ;;
    --no-venv) USE_VENV=0; shift ;;
    --no-cli) CLI=0; shift ;;
    --no-images) IMAGES=0; shift ;;
    --registry) REGISTRY="${2:?--registry needs a value}"; REGISTRY="${REGISTRY%/}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# VERSION, EXTRAS, ARCH, PYTHONS and IMAGE_REFS, written by bundle.sh.
# shellcheck disable=SC1091
. ./bundle.env

echo "== checking files"
if command -v sha256sum >/dev/null; then
  sha256sum --quiet -c SHA256SUMS
else
  shasum -a 256 -c SHA256SUMS >/dev/null
fi

if [ "$CLI" = "1" ]; then
  PY="${PYTHON:-python3}"
  echo "== qasight[$EXTRAS]==$VERSION from ./wheels"
  if [ "$USE_VENV" = "1" ]; then
    "$PY" -m venv "$VENV"
    PY="$VENV/bin/python"
    "$PY" -m pip install --quiet --no-index --find-links wheels --upgrade pip
  fi
  USER_FLAG=()
  if [ "$USE_VENV" = "0" ] && [ "$(id -u)" != "0" ]; then
    USER_FLAG=(--user)
  fi
  "$PY" -m pip install --quiet --no-index --find-links wheels ${USER_FLAG[@]+"${USER_FLAG[@]}"} \
    "qasight[${EXTRAS}]==${VERSION}"
  if [ "$USE_VENV" = "1" ]; then
    "$VENV/bin/qasight" --version
  fi
fi

SERVER_IMAGE=""
POSTGRES_IMAGE=""
RUNNER_IMAGE=""
if [ "$IMAGES" = "1" ] && [ -n "$IMAGE_REFS" ]; then
  command -v docker >/dev/null || { echo "docker isn't installed: install it or pass --no-images" >&2; exit 1; }
  for tar in images/*.tar; do
    echo "== loading $tar"
    docker load --quiet -i "$tar" >/dev/null
  done
  for image in $IMAGE_REFS; do
    target="$image"
    if [ -n "$REGISTRY" ]; then
      name="${image##*/}"
      target="$REGISTRY/$name"
      echo "== pushing $target"
      docker tag "$image" "$target"
      docker push --quiet "$target" >/dev/null
    fi
    case "$image" in
      */qasight-server:*) SERVER_IMAGE="$target" ;;
      postgres:*) POSTGRES_IMAGE="$target" ;;
      */qasight-runner:*) RUNNER_IMAGE="$target" ;;
    esac
  done
fi

echo
echo "== done"
if [ "$CLI" = "1" ] && [ "$USE_VENV" = "1" ]; then
  echo "CLI: $VENV/bin/qasight (add $(pwd)/$VENV/bin to PATH)"
fi
if [ -n "$SERVER_IMAGE" ]; then
  echo
  echo "Team server: run '$VENV/bin/qasight server init --dir <folder>' (or copy deploy/server/docker-compose.yml),"
  echo "then add these lines to that folder's .env before 'docker compose up -d':"
  echo
  echo "  TESTO_REGISTRY=${SERVER_IMAGE%/qasight-server:*}"
  echo "  TESTO_SERVER_TAG=$VERSION"
  [ -n "$POSTGRES_IMAGE" ] && echo "  TESTO_POSTGRES_IMAGE=$POSTGRES_IMAGE"
  [ -n "$RUNNER_IMAGE" ] && echo "  TESTO_RUNNER_IMAGE=$RUNNER_IMAGE  # with docker-compose.runner.yml"
  echo "  TESTO_NO_UPDATE_CHECK=1"
fi
echo
echo "Same day: fill site.env (from site.env.example) and run ./quickstart.sh instead."
echo
echo "GitLab CI: gitlab/qasight.gitlab-ci.yml (set TESTO_IMAGE to an image that has qasight,"
echo "or PIP_INDEX_URL to your PyPI mirror). See the guide for the AI settings."
