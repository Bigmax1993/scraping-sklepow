#!/usr/bin/env bash
# Przywraca najlepszy stan pipeline (staging + cache JSON) z artefaktów GHA.
# Kandydaci: pipeline-state / pipeline-staging-* ; sort: rozmiar ↓, data ↓.
# Pomija artefakty bez neueroeffnung_staging.json.
set -euo pipefail

REPO="${GITHUB_REPOSITORY:?}"
DEST="${1:-.}"
mkdir -p "$DEST"

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
      | sort_by(.size_in_bytes, .created_at)
      | reverse
      | .[]
      | "\(.id)\t\(.name)\t\(.created_at)\t\(.size_in_bytes)"
    '
)

if [[ ${#CANDIDATES[@]} -eq 0 ]]; then
  echo "Brak artefaktów pipeline-state / pipeline-staging-* — start od pustego stanu."
  exit 0
fi

try_restore() {
  local artifact_id="$1"
  local artifact_name="$2"
  local tmp="/tmp/pipeline-state-restore-${artifact_id}"

  rm -rf "$tmp"
  mkdir -p "$tmp"
  if ! gh api "repos/${REPO}/actions/artifacts/${artifact_id}/zip" > /tmp/pipeline-state.zip; then
    echo "  pomijam ${artifact_name} (${artifact_id}): pobranie ZIP nieudane"
    return 1
  fi
  if ! unzip -o /tmp/pipeline-state.zip -d "$tmp" >/dev/null; then
    echo "  pomijam ${artifact_name} (${artifact_id}): unzip nieudany"
    return 1
  fi

  local staging
  staging="$(find "$tmp" -type f -name 'neueroeffnung_staging.json' | head -n 1 || true)"
  if [[ -z "$staging" || ! -s "$staging" ]]; then
    echo "  pomijam ${artifact_name} (${artifact_id}): brak neueroeffnung_staging.json"
    return 1
  fi

  echo "Przywracam artefakt name=${artifact_name} id=${artifact_id}"
  local copied=0
  while IFS= read -r -d '' f; do
    local base
    base="$(basename "$f")"
    case "$base" in
      *.json)
        cp -f "$f" "${DEST}/${base}"
        echo "  + ${base} ($(wc -c < "$f" | tr -d ' ') bytes)"
        copied=$((copied + 1))
        ;;
    esac
  done < <(find "$tmp" -type f -print0)

  echo "OK: przywrócono ${copied} plik(ów) JSON (w tym neueroeffnung_staging.json)"
  return 0
}

for line in "${CANDIDATES[@]}"; do
  id="${line%%$'\t'*}"
  rest="${line#*$'\t'}"
  name="${rest%%$'\t'*}"
  size="${line##*$'\t'}"
  echo "Kandydat: ${name} id=${id} size=${size}"
  if try_restore "$id" "$name"; then
    exit 0
  fi
done

echo "Żaden artefakt nie zawierał neueroeffnung_staging.json — start od pustego stanu."
exit 0
