# Jednorazowa bramka startu pipeline'u (scheduled runs).
# workflow_dispatch zawsze przechodzi (ręczne testy).
# Po 2026-09-03 scheduled crony działają normalnie — plik można zostawić (harmless).

PIPELINE_START_DATE="${PIPELINE_START_DATE:-2026-09-03}"

if [[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]]; then
  echo "skip=false" >> "${GITHUB_OUTPUT}"
  echo "Manual dispatch — bramka pominięta."
  exit 0
fi

TODAY=$(date -u +%Y-%m-%d)
if [[ "$TODAY" < "$PIPELINE_START_DATE" ]]; then
  echo "skip=true" >> "${GITHUB_OUTPUT}"
  echo "::notice title=Start pipeline ${PIPELINE_START_DATE}::Cron pominięty (${TODAY}). Pierwszy automatyczny run: Discovery ${PIPELINE_START_DATE} o 03:30 czasu polskiego."
else
  echo "skip=false" >> "${GITHUB_OUTPUT}"
  echo "Pipeline aktywny (data ${TODAY} >= ${PIPELINE_START_DATE})."
fi
