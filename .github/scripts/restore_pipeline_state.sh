#!/usr/bin/env bash
# Przywraca najlepszy stan pipeline z artefaktów GHA.
# Kryterium: najwyższy etap (validated > discovery), potem liczba rekordów, potem data.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:?}"
DEST="${1:-.}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCORE_SCRIPT="${ROOT}/.github/scripts/score_staging.py"
mkdir -p "$DEST" /tmp/pipeline-pick

mapfile -t CANDIDATES < <(
  gh api "repos/${REPO}/actions/artifacts?per_page=100" \
    --jq '
      [.artifacts[]
        | select(.expired==false)
        | select(.name=="pipeline-state"
              or .name=="pipeline-staging-contact"
              or .name=="pipeline-staging-maps"
              or .name=="pipeline-staging-validate"
              or .name=="pipeline-staging-discovery")
        | {id, name, created_at, size_in_bytes}
      ]
      | sort_by(.created_at)
      | reverse
      | .[]
      | "\(.id)\t\(.name)\t\(.created_at)\t\(.size_in_bytes)"
    '
)

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  echo "Brak artefaktów pipeline-state / pipeline-staging-* — start od pustego stanu."
  exit 0
fi

BEST_ID=""
BEST_NAME=""
BEST_RANK=-1
BEST_TOTAL=-1
BEST_DIR=""

for line in "${CANDIDATES[@]}"; do
  id="${line%%$'\t'*}"
  rest="${line#*$'\t'}"
  name="${rest%%$'\t'*}"
  created="$(echo "$rest" | cut -f2)"
  size="${line##*$'\t'}"
  echo "Kandydat: ${name} id=${id} size=${size} created=${created}"

  tmp="/tmp/pipeline-pick/${id}"
  rm -rf "$tmp"
  mkdir -p "$tmp"
  if ! gh api "repos/${REPO}/actions/artifacts/${id}/zip" > /tmp/pipeline-state.zip 2>/dev/null; then
    echo "  pomijam: pobranie ZIP nieudane"
    continue
  fi
  if ! unzip -o /tmp/pipeline-state.zip -d "$tmp" >/dev/null 2>&1; then
    echo "  pomijam: unzip nieudany"
    continue
  fi

  staging="$(find "$tmp" -type f -name 'neueroeffnung_staging.json' | head -n 1 || true)"
  if [[ -z "$staging" || ! -s "$staging" ]]; then
    echo "  pomijam: brak neueroeffnung_staging.json"
    continue
  fi

  scored="$(python3 "$SCORE_SCRIPT" "$staging")"
  rank="$(echo "$scored" | cut -f1)"
  total="$(echo "$scored" | cut -f2)"
  stage="$(echo "$scored" | cut -f3)"
  echo "  stage=${stage} rank=${rank} records=${total}"

  if [[ "$rank" -gt "$BEST_RANK" ]] || { [[ "$rank" -eq "$BEST_RANK" ]] && [[ "$total" -gt "$BEST_TOTAL" ]]; }; then
    BEST_RANK="$rank"
    BEST_TOTAL="$total"
    BEST_ID="$id"
    BEST_NAME="$name"
    BEST_DIR="$tmp"
  fi
done

if [[ -z "$BEST_ID" ]]; then
  echo "Żaden artefakt nie zawierał neueroeffnung_staging.json — start od pustego stanu."
  exit 0
fi

echo "Wybieram najlepszy: name=${BEST_NAME} id=${BEST_ID} rank=${BEST_RANK} records=${BEST_TOTAL}"
copied=0
while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  case "$base" in
    *.json)
      cp -f "$f" "${DEST}/${base}"
      echo "  + ${base} ($(wc -c < "$f" | tr -d ' ') bytes)"
      copied=$((copied + 1))
      ;;
  esac
done < <(find "$BEST_DIR" -type f -print0)

echo "OK: przywrócono ${copied} plik(ów) JSON (w tym neueroeffnung_staging.json)"
