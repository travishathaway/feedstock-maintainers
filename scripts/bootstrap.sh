#!/usr/bin/env bash
# Bootstrap every data file from scratch, in the same order as .github/workflows/update.yml.
# See docs/bootstrapping.md for what each step does and why it runs where it does.
set -euo pipefail

PLATFORMS="linux-64 noarch"
WITH_BACKFILL=0
WITH_SITE=0
FORCE=""

usage() {
  cat <<USAGE
Usage: scripts/bootstrap.sh [options]

Options:
  --platforms "A B"   conda-forge platforms for the package graph (default: "$PLATFORMS")
  --with-backfill     also run the one-off, slow history backfills
  --site              also run 'generate site-data' and build the web app
  --force             pass --force to the fetch commands that support it
  -h, --help          show this help

Requires GITHUB_TOKEN. Set TEAM_READ_TOKEN to a token that can read the conda-forge
org's teams if you want team handles expanded into head counts.
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --platforms) PLATFORMS="$2"; shift 2 ;;
    --with-backfill) WITH_BACKFILL=1; shift ;;
    --site) WITH_SITE=1; shift ;;
    --force) FORCE="--force"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "error: GITHUB_TOKEN is not set (see README, 'GitHub token')." >&2
  exit 1
fi

cd "$(dirname "$0")/.."

PLATFORM_ARGS=()
for p in $PLATFORMS; do PLATFORM_ARGS+=(-p "$p"); done

fsm() { pixi run fsm "$@"; }
step() { printf '\n==> %s\n' "$*"; }

step "Installing dependencies"
pixi install

# ── Maintainer pipeline ──────────────────────────────────────────────────────
step "Fetching feedstock recipes"
fsm fetch feedstocks $FORCE
step "Extracting maintainers, package names and licenses"
fsm generate maintainers
step "Fetching maintainer GitHub profiles (slow)"
fsm fetch maintainer-info $FORCE
step "Resolving maintainer countries"
fsm generate maintainer-countries
step "Building the maintainer graph"
fsm generate maintainer-graph --output maintainer-graph.json

# PRIVACY: team membership must never be cached or published, so the file is
# reduced to head counts and removed immediately.
step "Counting team members"
rm -f team-members.json
GITHUB_TOKEN="${TEAM_READ_TOKEN:-$GITHUB_TOKEN}" fsm fetch team-members
fsm generate listed-maintainer-counts
rm -f team-members.json

step "Linking packages to maintainers"
fsm generate package-maintainers

# ── Package pipeline ─────────────────────────────────────────────────────────
step "Building the package graph (downloads repodata)"
fsm generate package-graph "${PLATFORM_ARGS[@]}" --output package-graph.json
step "Computing transitive dependencies (downloads repodata)"
fsm generate transitive-dependencies "${PLATFORM_ARGS[@]}" --output transitive-dependencies.json
step "Fetching package download stats"
fsm fetch package-downloads
step "Fetching package about metadata"
fsm fetch package-about --package-maintainers-file package-maintainers.json $FORCE

# ── Feedstock health ─────────────────────────────────────────────────────────
step "Computing feedstock tiers"
fsm generate feedstock-tiers \
  --package-names-file package-names.json \
  --package-downloads-file package-downloads.json \
  --transitive-dependencies-file transitive-dependencies.json \
  --output feedstock-tiers.json
step "Fetching feedstock activity for the top tier"
fsm fetch feedstock-activity --tiers-file feedstock-tiers.json --tier top $FORCE \
  --output feedstock-activity-raw.json
fsm generate feedstock-activity \
  --raw-file feedstock-activity-raw.json \
  --maintainers-file maintainers.json \
  --output feedstock-activity.json
step "Fetching feedstock health signals for the top tier"
fsm fetch feedstock-health-signals --tiers-file feedstock-tiers.json --tier top $FORCE \
  --output feedstock-health-signals-raw.json
fsm generate feedstock-health \
  --signals-file feedstock-health-signals-raw.json \
  --maintainers-file maintainers.json \
  --activity-raw-file feedstock-activity-raw.json \
  --activity-file feedstock-activity.json \
  --package-names-file package-names.json \
  --transitive-dependencies-file transitive-dependencies.json \
  --output feedstock-health.json

# ── History ──────────────────────────────────────────────────────────────────
if [ "$WITH_BACKFILL" -eq 1 ]; then
  step "Backfilling maintainer history (one-off, slow)"
  fsm fetch maintainer-history-backfill
fi
step "Updating maintainer and feedstock count history"
fsm generate maintainer-history-append \
  --maintainers-file maintainers.json \
  --history-file maintainer-history.json
fsm fetch feedstock-count-history-backfill
fsm generate feedstock-count-append \
  --maintainers-file maintainers.json \
  --history-file feedstock-count-history.json

# ── Site ─────────────────────────────────────────────────────────────────────
if [ "$WITH_SITE" -eq 1 ]; then
  step "Generating site data"
  mkdir -p web/static/data
  cp maintainer-graph.json web/static/data/maintainer-graph.json
  fsm generate site-data
  step "Building the web app"
  pixi run build-web
fi

step "Done"
