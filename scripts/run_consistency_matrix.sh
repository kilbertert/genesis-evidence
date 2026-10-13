#!/usr/bin/env bash
# Run the consistency-prompt comparison as a matrix: topics x arms x repeats.
#
# Why a script and not one invocation: the quantity being measured is a *distribution*.
# A single before/after pair cannot separate the prompt effect from run-to-run variance,
# and an earlier round recorded exactly that mistake -- one sample of three runs (2/5/0)
# was reported as a result. This runs every arm repeatedly and aggregates.
#
# Usage: run_consistency_matrix.sh <out-dir> [repeats] [limit]
set -uo pipefail

OUT="${1:?usage: run_consistency_matrix.sh <out-dir> [repeats] [limit]}"
REPEATS="${2:-3}"
LIMIT="${3:-6}"
CORPUS="${CORPUS:-/tmp/ge-p2corpus/genesis-evidence.sqlite3}"

# Topics chosen for the most material issues in the stored corpus, so the comparison has
# something to compare. A topic with none would report 0 for every arm and prove nothing.
TOPICS=(sodium-reduction-blood-pressure vitamin-d-supplementation dietary-fatty-acids-lipids nutrition-ckd-risk)
ARMS=(free quoted)

export PAPER_AI_BASE_URL="${PAPER_AI_BASE_URL:-https://api.commandcode.ai/provider/v1}"
export PAPER_AI_MODEL="${PAPER_AI_MODEL:-deepseek/deepseek-v4.1-flash}"
export PAPER_AI_MAX_TOKENS="${PAPER_AI_MAX_TOKENS:-32768}"

mkdir -p "$OUT"
echo "matrix: ${#TOPICS[@]} topics x ${#ARMS[@]} arms x ${REPEATS} repeats x ${LIMIT} papers/run"

for topic in "${TOPICS[@]}"; do
  for arm in "${ARMS[@]}"; do
    for rep in $(seq 1 "$REPEATS"); do
      report="$OUT/${topic}__${arm}__r${rep}.json"
      if [ -f "$report" ]; then
        echo "skip (exists): $report"
        continue
      fi
      echo "=== $topic / $arm / r$rep ==="
      # A report that already exists is refused by the script, so a resumed run never
      # silently replaces a measurement it was supposed to be compared against.
      timeout 3600 uv run --extra dev python -u scripts/measure_consistency_contract.py \
        --corpus "$CORPUS" --topic "$topic" --limit "$LIMIT" --apply \
        --prompt-arm "$arm" --label "${topic}__${arm}__r${rep}" \
        --report "$report" 2>&1 | tail -6 || {
          echo "!! $topic / $arm / r$rep failed (kept going; the run is the experiment)"
        }
    done
  done
done
echo "matrix done"
