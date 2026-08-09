#!/usr/bin/env bash
# team-supervisor スキルの TeammateIdle フック。
#
# サブリーダー teammate が作業を終えようとしたとき、タスクブランチが origin に
# push されていなければ終了を拒否して作業を続けさせる。公式ドキュメントが
# 「lead も全タスク完了前に終わったと判断することがある」と明記しているため、
# lead の外に機械的な歯止めを置く。
#
# 標準入力: {"teammate_name": "...", "team_name": "..."}
# 終了コード: 0 = 素通し / 2 = 終了を拒否して stderr を teammate に見せる
#             1 = 打ち切り（stderr はユーザーにだけ見せる）

set -uo pipefail

payload=$(cat)
name=$(printf '%s' "$payload" |
  sed -n 's/.*"teammate_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# このスキルのサブリーダーだけを対象にする（名前は task で始まる）
case "$name" in
  task*) ;;
  *) exit 0 ;;
esac

# git リポジトリでなければ何もしない
git rev-parse --git-common-dir >/dev/null 2>&1 || exit 0
state_dir="$(git rev-parse --git-common-dir)/team-supervisor"
mkdir -p "$state_dir" 2>/dev/null || exit 0

# サブリーダーが blocked をリードへ報告済みなら止めない
if [ -f "$state_dir/blocked-$name" ]; then
  exit 0
fi

# タスクブランチが push されていれば成果は残っているので素通しする。
# ブランチ名は、リードがタスク登録時に branch-<teammate 名> へ書いたもの（SKILL.md §6）を
# 読む。登録が無いときだけ、規約 topic/<作業名>--task-<番号>（teammate 名は task<番号>）の
# 末尾一致に頼る。末尾一致は過去の実行が残した同名ブランチにも一致し得るので、登録を正とする。
pushed=0
branch_file="$state_dir/branch-$name"
if [ -f "$branch_file" ]; then
  branch=$(cat "$branch_file")
  if [ -n "$branch" ] &&
    git ls-remote --exit-code --heads origin "refs/heads/$branch" >/dev/null 2>&1; then
    pushed=1
  fi
elif git ls-remote --heads origin 2>/dev/null | grep -- "--task-${name#task}\$" >/dev/null; then
  # grep に -q を付けない（-q は途中で読むのをやめ、pipefail 下で ls-remote が
  # SIGPIPE で落ちて「push なし」と誤判定することがある）
  pushed=1
fi

if [ "$pushed" = 1 ]; then
  rm -f "$state_dir/idle-count-$name"
  exit 0
fi

# 同じ teammate を無限に止め続けないよう 3 回で打ち切る
count_file="$state_dir/idle-count-$name"
count=$(cat "$count_file" 2>/dev/null || echo 0)
case "$count" in ''|*[!0-9]*) count=0 ;; esac
count=$((count + 1))
printf '%s' "$count" >"$count_file"

if [ "$count" -gt 3 ]; then
  echo "team-supervisor: $name のブランチが push されないまま ${count} 回アイドルに入りました。リードの介入が要ります。" >&2
  exit 1
fi

cat >&2 <<EOF
あなたのタスクブランチが origin に push されていません（${count}/3 回目の確認）。
次のどちらかを行ってください。

1. 未完の作業を続ける。実装 subagent に commit と push をさせる
   （push は git push origin HEAD:refs/heads/<タスクブランチ>）。

2. これ以上進められないなら、リードへ SendMessage で blocked を報告し、
   報告したあとに次を実行してこの確認を止める。
   touch "$state_dir/blocked-$name"
EOF
exit 2
