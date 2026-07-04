#!/bin/bash
# Claude Code statusline: model/dir/git/worktree, context usage, cost, rate limits, PR.
input=$(cat)

MODEL=$(echo "$input" | jq -r '.model.display_name')
DIR=$(echo "$input" | jq -r '.workspace.current_dir')
PCT=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)
COST=$(echo "$input" | jq -r '.cost.total_cost_usd // 0')
DURATION_MS=$(echo "$input" | jq -r '.cost.total_duration_ms // 0')
LINES_ADDED=$(echo "$input" | jq -r '.cost.total_lines_added // 0')
LINES_REMOVED=$(echo "$input" | jq -r '.cost.total_lines_removed // 0')
FIVE_H=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // empty')
FIVE_H_RESET=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // empty')
WEEK=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // empty')
WEEK_RESET=$(echo "$input" | jq -r '.rate_limits.seven_day.resets_at // empty')
EFFORT=$(echo "$input" | jq -r '.effort.level // empty')
WORKTREE=$(echo "$input" | jq -r '.worktree.name // empty')
PR_NUMBER=$(echo "$input" | jq -r '.pr.number // empty')
PR_URL=$(echo "$input" | jq -r '.pr.url // empty')
PR_STATE=$(echo "$input" | jq -r '.pr.review_state // empty')

GREEN='\033[32m'; YELLOW='\033[33m'; RED='\033[31m'; CYAN='\033[36m'; MAGENTA='\033[35m'; BLUE='\033[34m'; RESET='\033[0m'

color_for_pct() {
  local p=${1%.*}
  if [ "$p" -ge 90 ]; then echo "$RED"
  elif [ "$p" -ge 70 ]; then echo "$YELLOW"
  else echo "$GREEN"; fi
}

# --- line 1: model(effort) / dir / git / worktree ---
GIT_INFO=""
if git rev-parse --git-dir > /dev/null 2>&1; then
  BRANCH=$(git branch --show-current 2>/dev/null)
  STAGED=$(git diff --cached --numstat 2>/dev/null | wc -l | tr -d ' ')
  MODIFIED=$(git diff --numstat 2>/dev/null | wc -l | tr -d ' ')
  UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ')
  GIT_STATUS=""
  [ "$STAGED" -gt 0 ] && GIT_STATUS="${GREEN}+${STAGED}${RESET}"
  [ "$MODIFIED" -gt 0 ] && GIT_STATUS="${GIT_STATUS}${GIT_STATUS:+ }${YELLOW}~${MODIFIED}${RESET}"
  [ "$UNTRACKED" -gt 0 ] && GIT_STATUS="${GIT_STATUS}${GIT_STATUS:+ }${BLUE}?${UNTRACKED}${RESET}"
  GIT_INFO=" | 🌿 ${BRANCH}${GIT_STATUS:+ }${GIT_STATUS}"
fi

MODEL_LABEL="$MODEL"
[ -n "$EFFORT" ] && MODEL_LABEL="${MODEL_LABEL} ${MAGENTA}·${EFFORT}${RESET}${CYAN}"

WORKTREE_INFO=""
[ -n "$WORKTREE" ] && WORKTREE_INFO=" | 🌲 ${WORKTREE}"

echo -e "${CYAN}[$MODEL_LABEL]${RESET} 📁 ${DIR##*/}${GIT_INFO}${WORKTREE_INFO}"

# --- line 2: context bar / cost / duration / lines changed ---
CTX_COLOR=$(color_for_pct "$PCT")
BAR_WIDTH=10
FILLED=$((PCT * BAR_WIDTH / 100))
EMPTY=$((BAR_WIDTH - FILLED))
BAR=""
[ "$FILLED" -gt 0 ] && printf -v FILL "%${FILLED}s" && BAR="${FILL// /█}"
[ "$EMPTY" -gt 0 ] && printf -v PAD "%${EMPTY}s" && BAR="${BAR}${PAD// /░}"

COST_FMT=$(printf '$%.2f' "$COST")
DURATION_SEC=$((DURATION_MS / 1000))
MINS=$((DURATION_SEC / 60))
SECS=$((DURATION_SEC % 60))

LINES_INFO=""
if [ "$LINES_ADDED" -gt 0 ] || [ "$LINES_REMOVED" -gt 0 ]; then
  LINES_INFO=" | 📝 ${GREEN}+${LINES_ADDED}${RESET} ${RED}-${LINES_REMOVED}${RESET}"
fi

echo -e "${CTX_COLOR}${BAR}${RESET} ctx:${PCT}% | 💰 ${COST_FMT} | ⏱️ ${MINS}m ${SECS}s${LINES_INFO}"

# --- line 3: rate limits (Pro/Max subscriptions only) ---
if [ -n "$FIVE_H" ] || [ -n "$WEEK" ]; then
  RATE_LINE=""
  if [ -n "$FIVE_H" ]; then
    FIVE_H_INT=$(printf '%.0f' "$FIVE_H")
    FIVE_H_COLOR=$(color_for_pct "$FIVE_H_INT")
    FIVE_H_RESET_FMT=""
    [ -n "$FIVE_H_RESET" ] && FIVE_H_RESET_FMT=" (resets $(date -d @"$FIVE_H_RESET" +%H:%M 2>/dev/null))"
    RATE_LINE="⏳ 5h: ${FIVE_H_COLOR}${FIVE_H_INT}%${RESET}${FIVE_H_RESET_FMT}"
  fi
  if [ -n "$WEEK" ]; then
    WEEK_INT=$(printf '%.0f' "$WEEK")
    WEEK_COLOR=$(color_for_pct "$WEEK_INT")
    WEEK_RESET_FMT=""
    [ -n "$WEEK_RESET" ] && WEEK_RESET_FMT=" (resets $(date -d @"$WEEK_RESET" '+%m/%d %H:%M' 2>/dev/null))"
    RATE_LINE="${RATE_LINE}${RATE_LINE:+ | }📅 7d: ${WEEK_COLOR}${WEEK_INT}%${RESET}${WEEK_RESET_FMT}"
  fi
  echo -e "$RATE_LINE"
fi

# --- line 4: open PR badge ---
if [ -n "$PR_NUMBER" ]; then
  case "$PR_STATE" in
    approved) PR_COLOR=$GREEN ;;
    changes_requested) PR_COLOR=$RED ;;
    pending) PR_COLOR=$YELLOW ;;
    draft) PR_COLOR=$BLUE ;;
    *) PR_COLOR=$RESET ;;
  esac
  PR_LABEL="#${PR_NUMBER}"
  [ -n "$PR_URL" ] && PR_LABEL=$(printf '%b' "\e]8;;${PR_URL}\a${PR_LABEL}\e]8;;\a")
  PR_STATE_FMT=""
  [ -n "$PR_STATE" ] && PR_STATE_FMT=" ${PR_STATE}"
  printf '%b\n' "🔀 PR ${PR_COLOR}${PR_LABEL}${PR_STATE_FMT}${RESET}"
fi
