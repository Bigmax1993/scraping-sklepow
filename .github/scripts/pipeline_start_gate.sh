# Bramka scheduled runs: start od PIPELINE_START_DATE, potem tylko co N dni (cykl).
# workflow_dispatch zawsze przechodzi (ręczne testy).
# Kotwica 2026-10-05 = poniedziałek → aktywny tydzień cyklu to dni 0–6 (pon–niedz).

PIPELINE_START_DATE="${PIPELINE_START_DATE:-2026-10-05}"
PIPELINE_CYCLE_DAYS="${PIPELINE_CYCLE_DAYS:-28}"

if [[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]]; then
  echo "skip=false" >> "${GITHUB_OUTPUT}"
  echo "Manual dispatch — bramka pominięta."
  exit 0
fi

TODAY=$(date -u +%Y-%m-%d)

if [[ "$TODAY" < "$PIPELINE_START_DATE" ]]; then
  echo "skip=true" >> "${GITHUB_OUTPUT}"
  echo "::notice title=Start pipeline ${PIPELINE_START_DATE}::Cron pominięty (${TODAY}). Następny cykl od ${PIPELINE_START_DATE}."
  exit 0
fi

start_epoch=$(date -u -d "$PIPELINE_START_DATE" +%s)
today_epoch=$(date -u -d "$TODAY" +%s)
days=$(( (today_epoch - start_epoch) / 86400 ))
cycle_day=$(( days % PIPELINE_CYCLE_DAYS ))

if (( cycle_day > 6 )); then
  next_offset=$(( PIPELINE_CYCLE_DAYS - cycle_day ))
  next_epoch=$(( today_epoch + next_offset * 86400 ))
  next_date=$(date -u -d "@${next_epoch}" +%Y-%m-%d)
  echo "skip=true" >> "${GITHUB_OUTPUT}"
  echo "::notice title=Poza cyklem ${PIPELINE_CYCLE_DAYS} dni::Cron pominięty (${TODAY}, dzień cyklu ${cycle_day}). Następny tydzień pipeline: ${next_date}."
  exit 0
fi

echo "skip=false" >> "${GITHUB_OUTPUT}"
echo "Pipeline aktywny (data ${TODAY}, dzień cyklu ${cycle_day}/${PIPELINE_CYCLE_DAYS}, start ${PIPELINE_START_DATE})."
