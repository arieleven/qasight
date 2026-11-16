#!/usr/bin/env bash
# Pack QASight for a network with no internet: one .tar.gz with the Python
# wheels, the Docker images, the GitLab CI template and install.sh.
#
# Run on a machine that has internet (Linux or macOS), from a clone of the
# repository, ideally checked out at the release tag:
#
#   scripts/offline/bundle.sh                       # this checkout's version
#   scripts/offline/bundle.sh --version 1.7.0 --python 3.11,3.12 --extras api,db,bdd
#   scripts/offline/bundle.sh --no-images           # wheels only (no Docker needed)
#
# Carry the .tar.gz in, unpack it and run ./install.sh there.
# Guide: docs/Processes & Guides/Closed Network Install.md
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

usage() {
  cat <<'EOF'
Usage: scripts/offline/bundle.sh [options]

  --version V      qasight version from PyPI (default: this checkout's pyproject.toml)
  --python LIST    target Python versions, comma-separated (default: 3.11,3.12,3.13)
  --arch A         amd64 or arm64, for wheels and images (default: amd64)
  --extras LIST    qasight extras to include (default: api,db)
  --no-images      skip the Docker images (no Docker needed)
  --runner         also pack the qasight-runner-service image (runs in throwaway containers)
  --registry R     where the published images live (default: ghcr.io/arieleven)
  --out DIR        where to write the bundle (default: ./dist-offline)
  -h, --help       this help
EOF
}

VERSION=""
PYTHONS="3.11,3.12,3.13"
ARCH="amd64"
EXTRAS="api,db"
IMAGES=1
RUNNER=0
REGISTRY="ghcr.io/arieleven"
OUT="dist-offline"

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    --python) PYTHONS="${2:?--python needs a value}"; shift 2 ;;
    --arch) ARCH="${2:?--arch needs a value}"; shift 2 ;;
    --extras) EXTRAS="${2:?--extras needs a value}"; shift 2 ;;
    --no-images) IMAGES=0; shift ;;
    --runner) RUNNER=1; shift ;;
    --registry) REGISTRY="${2:?--registry needs a value}"; shift 2 ;;
    --out) OUT="${2:?--out needs a value}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$VERSION" ]; then
  VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -n 1)"
fi
case "$ARCH" in
  amd64) PY_ARCH="x86_64" ;;
  arm64) PY_ARCH="aarch64" ;;
  *) echo "--arch must be amd64 or arm64, not $ARCH" >&2; exit 2 ;;
esac

PIP="${PIP:-python3 -m pip}"
NAME="qasight-offline-${VERSION}-linux-${ARCH}"
STAGE="$OUT/$NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE/wheels" "$STAGE/images" "$STAGE/gitlab"

echo "== wheels: qasight[$EXTRAS]==$VERSION for Python $PYTHONS on linux/$ARCH"
IFS=',' read -r -a PY_LIST <<<"$PYTHONS"
for py in "${PY_LIST[@]}"; do
  # Wheels only (no source builds inside), for the target's Python and CPU, not this machine's.
  $PIP download --quiet --dest "$STAGE/wheels" --only-binary=:all: \
    --implementation cp --python-version "$py" \
    --platform "manylinux_2_28_${PY_ARCH}" --platform "manylinux_2_17_${PY_ARCH}" \
    --platform "manylinux2014_${PY_ARCH}" --platform "manylinux_2_5_${PY_ARCH}" \
    --platform "manylinux1_${PY_ARCH}" --platform "linux_${PY_ARCH}" \
    "qasight[${EXTRAS}]==${VERSION}" pip
done

IMAGE_LIST=()
if [ "$IMAGES" = "1" ]; then
  command -v docker >/dev/null || { echo "docker isn't installed: install it or pass --no-images" >&2; exit 1; }
  IMAGE_LIST=("$REGISTRY/qasight-server:$VERSION" "$REGISTRY/qasight-runner:$VERSION" "postgres:16-alpine")
  if [ "$RUNNER" = "1" ]; then
    IMAGE_LIST+=("$REGISTRY/qasight-runner-service:$VERSION")
  fi
  for image in "${IMAGE_LIST[@]}"; do
    echo "== image: $image (linux/$ARCH)"
    docker pull --quiet --platform "linux/$ARCH" "$image" >/dev/null
    file="$(basename "${image%%:*}")-${image##*:}.tar"
    docker save -o "$STAGE/images/$file" "$image"
  done
fi

cp "$REPO/ci/gitlab/testo.gitlab-ci.yml" "$STAGE/gitlab/qasight.gitlab-ci.yml"
cp "$HERE/install.sh" "$STAGE/install.sh"
chmod +x "$STAGE/install.sh"
{
  echo "VERSION=\"$VERSION\""
  echo "EXTRAS=\"$EXTRAS\""
  echo "ARCH=\"$ARCH\""
  echo "PYTHONS=\"$PYTHONS\""
  echo "IMAGE_REFS=\"${IMAGE_LIST[*]:-}\""
} >"$STAGE/bundle.env"
cat >"$STAGE/README.txt" <<EOF
QASight $VERSION for a network with no internet (linux/$ARCH).

  ./install.sh                       # check files, install the CLI into ./venv, load the images
  ./install.sh --registry registry.example.local/qa   # and push the images to your registry

Guide: https://arieleven.github.io/qasight/docs/Processes%20%26%20Guides/Closed%20Network%20Install/
(the same page is docs/Processes & Guides/Closed Network Install.md in the repository).
EOF

(
  cd "$STAGE"
  find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum 2>/dev/null >SHA256SUMS \
    || find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 shasum -a 256 >SHA256SUMS
)
tar -czf "$OUT/$NAME.tar.gz" -C "$OUT" "$NAME"
echo "== done: $OUT/$NAME.tar.gz ($(du -h "$OUT/$NAME.tar.gz" | cut -f1))"
