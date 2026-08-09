#!/bin/bash
# Black-box test for the team-supervisor SubagentStop hook: 4 cases, exit 0 or 1.
#
# 検査の対象は .claude/skills/team-supervisor/scripts/subagent-stop.sh。あれは Claude Code の
# SubagentStop フックで、subagent が終わろうとしたときに標準入力へ JSON を受け取り、
# 終了コードだけで結果を返す（0 = 終了を認める / 2 = 終了を拒む / 1 = 打ち切る）。
# ここではフックの中身を読まず、外から叩いて終了コードが仕様どおりかだけを確かめる。
# 仕様の出どころは .claude/skills/team-supervisor/hooks.md の「振る舞い」表。
#
# 確かめる 4 ケース:
#   1. ブランチ登録 (branch-<agentId>) が無い          -> 0（サブリーダー以外を邪魔しない）
#   2. 登録があり、そのブランチが origin に無い        -> 2（未 push なので終了を拒む）
#   3. blocked-<agentId> の目印がある                  -> 0（報告済みなので止めない）
#   4. 押し戻しが 3 回を超えた（4 回目）               -> 1（無限に止め続けない）
#
# 使い方: リポジトリのルートから引数なしで実行する。全部通れば 0、1 つでも外れれば 1 を返す。
#
#   .claude/scripts/test-subagent-stop.sh; echo "exit=$?"
#
# なぜ使い捨ての git リポジトリを作るか:
#   フックは状態ファイルの置き場所を「今いるディレクトリの
#   git rev-parse --path-format=absolute --git-common-dir」から決める。このリポジトリの中で
#   そのまま叩くと、本物の .git/team-supervisor/ に登録ファイルとカウンタを書いてしまい、
#   走行中の team-supervisor の判定を狂わせる。そこで mktemp で作った使い捨てのリポジトリへ
#   移動してから叩き、終わったら丸ごと消す。このリポジトリの作業ツリーには一切触れない。
#
# 環境変数 TEST_HOOK_PATH:
#   叩くフックのパスを差し替える。この試験自体が壊れた実装を検出できるかを確かめるための口で、
#   CI からの呼び出しでは設定しない（設定しなければ自分の位置から本物のフックを探す）。

# --- 検査の対象を決める ---

# .claude/scripts/ と .claude/skills/ は隣り合っているので、自分の位置からの相対で辿る。
# install.sh が .claude/scripts を ~/.claude/scripts へ symlink するが、~/.claude/skills も
# 同じリポジトリへの symlink なので、どちらから起動しても同じフックに届く。
script_dir=$(cd "$(dirname "$0")" && pwd -P)
hook=${TEST_HOOK_PATH:-$script_dir/../skills/team-supervisor/scripts/subagent-stop.sh}

die() {
  echo "test-subagent-stop: $1" >&2
  exit 1
}

[ -f "$hook" ] || die "フックが見つかりません: $hook"
# hooks.md が chmod +x を要求している。Claude Code は shebang 経由で直接起動するので、
# bash "$hook" では実行権の欠落を見逃してしまう。ここで前提条件として確かめる。
[ -x "$hook" ] || die "フックに実行権がありません: $hook"

# --- 使い捨てのリポジトリを用意する ---

sandbox=$(mktemp -d "${TMPDIR:-/tmp}/subagent-stop-test.XXXXXX") || die "一時ディレクトリを作れません"
sandbox=$(cd "$sandbox" && pwd -P) || die "一時ディレクトリへ移動できません"

cleanup() {
  # 名前が想定どおりのときだけ消す。sandbox が空文字になった場合に / を消さないため。
  case "$sandbox" in
    */subagent-stop-test.??????) rm -rf "$sandbox" ;;
  esac
}
trap 'code=$?; cleanup; exit $code' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 呼び出し元の git 設定と git 用の環境変数を遮断する。~/.gitconfig の init.defaultBranch や
# url.<base>.insteadOf、親プロセスが立てた GIT_DIR などが残っていると、使い捨てのつもりの
# git 呼び出しが別のリポジトリや別の URL を向く。
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY GIT_NAMESPACE
export HOME="$sandbox/home"
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_SYSTEM=/dev/null
export GIT_TERMINAL_PROMPT=0
export GIT_CEILING_DIRECTORIES="$sandbox"
export GIT_AUTHOR_NAME=test-subagent-stop
export GIT_AUTHOR_EMAIL=test-subagent-stop@example.invalid
export GIT_COMMITTER_NAME=$GIT_AUTHOR_NAME
export GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
mkdir -p "$HOME" || die "HOME の差し替え先を作れません: $HOME"

# git ls-remote がうっかりネットワークへ出た場合に固まらないよう、あれば timeout を挟む。
timeout_cmd=()
command -v timeout >/dev/null 2>&1 && timeout_cmd=(timeout 30)

# 作業リポジトリと、その origin になるローカルの bare リポジトリを作る。
# origin を実在させるのは、ケース 2 で確かめたいのが「origin へは届くが、そのブランチだけが
# 無い」状態だから。origin 自体を作らないと git ls-remote は「remote が解決できない」で落ち、
# ブランチの有無を見ずに同じ終了コードになってしまい、試験としてざるになる。
new_repo() { # $1 = 名前。作業リポジトリのパスを標準出力に返す
  local name=$1
  local origin="$sandbox/$name-origin.git"
  local work="$sandbox/$name"
  git init -q --bare -b main "$origin" >/dev/null 2>&1 || return 1
  git init -q -b main "$work" >/dev/null 2>&1 || return 1
  git -C "$work" remote add origin "$origin" || return 1
  git -C "$work" commit -q --allow-empty -m "init" || return 1
  git -C "$work" push -q origin main >/dev/null 2>&1 || return 1
  printf '%s' "$work"
}

# フックが状態ファイルを探すのと同じ式で状態ディレクトリを求める。式を揃えておくと、
# 試験が用意した場所とフックが読む場所がずれない。
state_dir_of() { # $1 = 作業リポジトリ
  local common
  common=$(cd "$1" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  printf '%s/team-supervisor' "$common"
}

# --- フックを叩く ---

# フックを呼ぶ直前に、行き先が本当に使い捨てのリポジトリかを毎回確かめる。ここが最後の砦で、
# TMPDIR が別のリポジトリの中にある・git init に失敗した、といった場合に本物の .git を
# つかむのを止める。外れていたら叩かずに終える。
assert_sandbox() { # $1 = 作業リポジトリ
  local common
  common=$(cd "$1" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  case "$common" in
    "$sandbox"/*) return 0 ;;
  esac
  die "使い捨てリポジトリの外を指しています（$1 -> ${common:-解決できず}）。フックを叩かずに終えます"
}

# フックの標準入力に渡す JSON は必要な 2 つのキーだけにする。フックは agent_id を
# sed の貪欲な .* で拾うので、自由文（last_assistant_message など）に "agent_id" に似た
# 文字列が混ざると別の値を拾いかねない。
invoke_hook() { # $1 = 作業リポジトリ, $2 = agentId。終了コードを標準出力に返す
  local work=$1 agent_id=$2 code
  assert_sandbox "$work"
  printf '{"hook_event_name":"SubagentStop","agent_id":"%s"}' "$agent_id" |
    (cd "$work" && exec "${timeout_cmd[@]}" "$hook") >/dev/null 2>&1
  code=$?
  printf '%s' "$code"
}

# --- 合否を数える ---

total=0
passed=0
failed_labels=""

report() { # $1 = ラベル, $2 = 説明, $3 = 期待, $4 = 実測
  total=$((total + 1))
  if [ "$3" = "$4" ]; then
    passed=$((passed + 1))
    echo "PASS $1: $2 (期待 $3 / 実際 $4)"
  else
    failed_labels="$failed_labels $1"
    echo "FAIL $1: $2 (期待 $3 / 実際 $4)"
  fi
}

# --- ケース 1: ブランチ登録が無い -> 0 ---

# 状態ディレクトリごと作らない。実装 subagent や Explore など、リードが登録していない
# subagent が終わるときの状態を再現する。
run_case_1() {
  local work agent_id=a0000000000000001
  work=$(new_repo case1) || die "ケース1 の使い捨てリポジトリを作れません"
  report "ケース1" "branch-<agentId> の登録が無い -> 素通し" 0 "$(invoke_hook "$work" "$agent_id")"
}

# --- ケース 2: 登録があり origin に無い -> 2 ---

# origin は生きていて main も push 済み。登録するブランチ名だけを push しないでおく。
run_case_2() {
  local work state agent_id=a0000000000000002
  work=$(new_repo case2) || die "ケース2 の使い捨てリポジトリを作れません"
  state=$(state_dir_of "$work") || die "ケース2 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース2 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース2 のブランチ登録を書けません"
  report "ケース2" "登録あり・origin にそのブランチが無い -> 押し戻し" 2 "$(invoke_hook "$work" "$agent_id")"
}

# --- ケース 3: blocked の目印がある -> 0 ---

# ケース 2 との違いを blocked-<agentId> の 1 ファイルだけにする。ブランチを push すると
# 「目印が効いた」のか「ブランチが有ったから素通しした」のか区別できなくなる。
run_case_3() {
  local work state agent_id=a0000000000000003
  work=$(new_repo case3) || die "ケース3 の使い捨てリポジトリを作れません"
  state=$(state_dir_of "$work") || die "ケース3 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース3 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース3 のブランチ登録を書けません"
  touch "$state/blocked-$agent_id" || die "ケース3 の blocked の目印を作れません"
  report "ケース3" "blocked-<agentId> の目印がある -> 素通し" 0 "$(invoke_hook "$work" "$agent_id")"
}

# --- ケース 4: 押し戻しが 3 回を超えた -> 1 ---

# 押し戻し回数のカウンタ（idle-count-<agentId>）はフックが自分で書く内部の状態なので、
# 試験からは書かない。代わりに同じ agentId で 4 回続けて叩き、2,2,2,1 と遷移することを見る。
# カウンタを 3 で置いて 1 回だけ叩く手もあるが、それはファイル名と中身の形に頼るうえ、
# 「押し戻すたびに数える」という遷移そのものを飛ばしてしまう。
run_case_4() {
  local work state actual agent_id=a0000000000000004
  work=$(new_repo case4) || die "ケース4 の使い捨てリポジトリを作れません"
  state=$(state_dir_of "$work") || die "ケース4 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース4 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース4 のブランチ登録を書けません"
  actual="$(invoke_hook "$work" "$agent_id"),$(invoke_hook "$work" "$agent_id"),$(invoke_hook "$work" "$agent_id"),$(invoke_hook "$work" "$agent_id")"
  report "ケース4" "未 push のまま 4 回終了しようとする -> 4 回目で打ち切り" "2,2,2,1" "$actual"
}

# --- 実行して結果を出す ---

echo "対象のフック: $hook"
echo "使い捨てリポジトリ: $sandbox"
echo

run_case_1
run_case_2
run_case_3
run_case_4

echo
if [ -n "$failed_labels" ]; then
  echo "$total 件中 $passed 件成功。外れたケース:$failed_labels"
  exit 1
fi
echo "$total 件中 $passed 件成功"
exit 0
