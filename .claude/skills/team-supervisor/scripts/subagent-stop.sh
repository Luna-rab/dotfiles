#!/usr/bin/env bash
# team-supervisor スキルの SubagentStop フック。
#
# サブリーダーの subagent が作業を終えようとしたとき、タスクブランチが origin に
# push されていなければ終了を拒否して作業を続けさせる。公式ドキュメントが
# 「エージェントはエラーに当たると回復せずに止まることがある」と明記しているため、
# リードの外に機械的な歯止めを置く。
#
# 標準入力: {"hook_event_name":"SubagentStop","agent_id":"a...","agent_type":"...", ...}
# 終了コード: 0 = 素通し / 2 = 終了を拒否して stderr を subagent に見せる
#             1 = 打ち切り（stderr はユーザーにだけ見せる）

set -uo pipefail

payload=$(cat)
agent_id=$(printf '%s' "$payload" |
  sed -n 's/.*"agent_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# agent_id が無ければ判定材料が無い
[ -n "$agent_id" ] || exit 0

# git リポジトリでなければ何もしない
git rev-parse --git-common-dir >/dev/null 2>&1 || exit 0
state_dir="$(git rev-parse --path-format=absolute --git-common-dir)/team-supervisor"

# 対象はリードが登録したサブリーダーだけ。実装・レビュー・Explore など、登録の無い
# subagent は素通しする（このフックは全 subagent の終了で発火する）。
branch_file="$state_dir/branch-$agent_id"
[ -f "$branch_file" ] || exit 0

# リードへ blocked を報告済み、または再開を打ち切ったものは止めない
if [ -f "$state_dir/blocked-$agent_id" ]; then
  exit 0
fi

# タスクブランチが push されていれば成果は残っているので素通しする
branch=$(cat "$branch_file")
if [ -n "$branch" ] &&
  git ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null 2>&1; then
  rm -f "$state_dir/idle-count-$agent_id"
  exit 0
fi

# 同じ subagent を無限に止め続けないよう 3 回で打ち切る
count_file="$state_dir/idle-count-$agent_id"
count=$(cat "$count_file" 2>/dev/null || echo 0)
case "$count" in '' | *[!0-9]*) count=0 ;; esac
count=$((count + 1))
printf '%s' "$count" >"$count_file"

if [ "$count" -gt 3 ]; then
  echo "team-supervisor: $agent_id のブランチ ($branch) が push されないまま ${count} 回終了しようとしました。リードの介入が要ります。" >&2
  exit 1
fi

cat >&2 <<EOF
あなたのタスクブランチ ($branch) が origin に push されていません（${count}/3 回目の確認）。
次のどちらかを行ってください。

1. 未完の作業を続ける。実装 subagent に commit と push をさせる
   （push は git push origin HEAD:refs/heads/$branch）。

2. これ以上進められないなら、リードへ SendMessage で blocked を報告し、
   報告したあとに次を実行してこの確認を止める。
   touch "$state_dir/blocked-$agent_id"
EOF
exit 2
