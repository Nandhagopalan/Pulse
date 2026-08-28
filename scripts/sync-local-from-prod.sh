#!/usr/bin/env bash
#
# Refresh the local Supabase database with production's market and strategy data.
#
# The local stack is built from supabase/migrations, so it has the right shape
# from the moment it starts, but it is empty until something fills it. The
# pipeline can do that (`uv run python -m pipeline eod`); this script takes the
# shortcut of copying what production already computed.
#
# Per-user tables are deliberately left alone. Sessions carry cookies issued
# against production's domain and users/watchlists/prefs are yours to keep, so
# copying them down would break local login rather than help it.
#
# Usage:
#   scripts/sync-local-from-prod.sh            # prompts before overwriting
#   scripts/sync-local-from-prod.sh --yes      # no prompt
#   PROD_DB_URL="postgresql://..." scripts/sync-local-from-prod.sh
#
set -euo pipefail

# Tables that stay local. Everything else the dump carries gets replaced.
KEEP=(users sessions user_watchlist user_prefs)

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ASSUME_YES=0
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && ASSUME_YES=1

if [[ -t 1 ]]; then BOLD=$'\033[1m'; GREEN=$'\033[32m'; RESET=$'\033[0m'
else BOLD=""; GREEN=""; RESET=""; fi

die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
step() { printf '\n%s==> %s%s\n' "$BOLD" "$*" "$RESET"; }

is_local() { [[ "$1" == *"127.0.0.1"* || "$1" == *"localhost"* ]]; }

# --- Resolve the two endpoints -----------------------------------------------
#
# Production comes from $PROD_DB_URL, or from the first non-local SUPABASE_DB_URL
# in .env.local. That line is often commented out — .env.local flips between the
# hosted and local database by moving the '#' — so a leading '#' is allowed here.

PROD_URL="${PROD_DB_URL:-}"
if [[ -z "$PROD_URL" && -f .env.local ]]; then
  while IFS= read -r candidate; do
    if ! is_local "$candidate"; then PROD_URL="$candidate"; break; fi
  done < <(grep -E '^[[:space:]]*#?[[:space:]]*(SUPABASE_DB_URL|DATABASE_URL)=' .env.local \
             | sed -E 's/^[^=]*=//; s/^"//; s/"$//')
fi
[[ -n "$PROD_URL" ]] || die "no production URL found. Set PROD_DB_URL, or put the hosted
       SUPABASE_DB_URL in .env.local (a commented-out line is fine)."
is_local "$PROD_URL" && die "PROD_DB_URL points at localhost — that is the local database, not production."

# The local stack reports its own URI, so a remapped port in supabase/config.toml
# is picked up without editing anything here.
LOCAL_URL="$(supabase status -o env 2>/dev/null | sed -nE 's/^DB_URL="(.*)"$/\1/p')"
[[ -n "$LOCAL_URL" ]] || die "the local Supabase stack is not running. Start it with:  supabase start"
is_local "$LOCAL_URL" || die "refusing to run: the local stack reported a non-local URL ($LOCAL_URL)."

# psql and pg_dump are not assumed to be on the host; the stack's own container
# has both, at a version that matches the server.
PROJECT_ID="$(sed -nE 's/^[[:space:]]*project_id[[:space:]]*=[[:space:]]*"(.*)"/\1/p' supabase/config.toml)"
CONTAINER="supabase_db_${PROJECT_ID}"
docker inspect "$CONTAINER" >/dev/null 2>&1 || die "container $CONTAINER not found. Is 'supabase start' running for this project?"

psql_local() { docker exec -i "$CONTAINER" psql -U postgres "$@"; }

OUT="$(mktemp -d)"
trap 'rm -rf "$OUT"' EXIT

# --- Pull production ----------------------------------------------------------

step "Dumping production data (public schema, excluding ${KEEP[*]})"
EXCLUDE=()
for t in "${KEEP[@]}"; do EXCLUDE+=(-x "public.$t"); done
supabase db dump --db-url "$PROD_URL" --data-only --use-copy -s public \
  "${EXCLUDE[@]}" -f "$OUT/prod.sql"

# The tables to replace are exactly the ones the dump turned out to carry, so a
# table added by a later migration is picked up without editing this script.
# (a while-read loop rather than mapfile: macOS ships bash 3.2)
TABLES=()
while IFS= read -r t; do TABLES+=("$t"); done < <(sed -nE 's/^COPY "public"\."([^"]+)".*/\1/p' "$OUT/prod.sql")
[[ ${#TABLES[@]} -gt 0 ]] || die "the dump contains no public tables — nothing to sync."
printf '    %s table(s): %s\n' "${#TABLES[@]}" "${TABLES[*]}"

if (( ! ASSUME_YES )); then
  printf '\nThis will DELETE and replace those tables in the local database.\n'
  read -r -p "Continue? [y/N] " reply
  [[ "$reply" == [yY] ]] || die "aborted."
fi

# --- Back up, then replace ----------------------------------------------------
#
# The backup includes the per-user tables the sync itself skips, so restoring it
# puts local back exactly as it was.

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$ROOT/.local-backups/local-$STAMP.sql"
mkdir -p "$(dirname "$BACKUP")"
step "Backing up the current local data to ${BACKUP#$ROOT/}"
supabase db dump --local --data-only --use-copy -s public -f "$BACKUP"

step "Replacing local data"
# TRUNCATE and reload run as one transaction: a failure anywhere leaves the
# local database exactly as it was, rather than half-emptied.
{
  printf 'TRUNCATE TABLE %s;\n' "$(printf 'public.%s,' "${TABLES[@]}" | sed 's/,$//')"
  cat "$OUT/prod.sql"
} | psql_local -v ON_ERROR_STOP=1 -q --single-transaction >/dev/null

# --- Verify -------------------------------------------------------------------
#
# Counted on both sides rather than assumed: a silently short COPY is the one
# failure this script could otherwise report as success.

step "Verifying"
counts_query() {
  local first=1
  for t in "${TABLES[@]}"; do
    (( first )) || printf ' union all '
    printf "select '%s' t, count(*) n from public.%s" "$t" "$t"
    first=0
  done
  printf ' order by 1'
}
Q="$(counts_query)"
psql_local -tAF, -c "$Q" > "$OUT/local.csv"
docker exec -i "$CONTAINER" psql -tAF, "$PROD_URL" -c "$Q" > "$OUT/prod.csv"

if diff -q "$OUT/local.csv" "$OUT/prod.csv" >/dev/null; then
  printf '\n%-24s %10s\n' "TABLE" "ROWS"
  sed 's/,/ /' "$OUT/local.csv" | awk '{printf "%-24s %10s\n", $1, $2}'
  printf '\n%sLocal matches production across %d table(s).%s\n' "$GREEN" "${#TABLES[@]}" "$RESET"
  printf 'Per-user tables (%s) were left untouched.\n' "${KEEP[*]}"
  printf 'Backup of the previous local data: %s\n' "${BACKUP#$ROOT/}"
else
  printf '\nrow counts differ (local | production):\n' >&2
  diff --side-by-side "$OUT/local.csv" "$OUT/prod.csv" >&2 || true
  die "sync finished but the counts do not match. Restore with:
       docker exec -i $CONTAINER psql -U postgres < ${BACKUP#$ROOT/}"
fi
