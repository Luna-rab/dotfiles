#!/bin/bash
# Black-box test for the team-supervisor SubagentStop hook: 6 cases, exit 0 or 1.
#
# 検査の対象は .claude/skills/team-supervisor/scripts/subagent-stop.sh。あれは Claude Code の
# SubagentStop フックで、subagent が終わろうとしたときに標準入力へ JSON を受け取り、
# 終了コードだけで結果を返す（0 = 終了を認める / 2 = 終了を拒む / 1 = 打ち切る）。
# ここではフックの中身を読まず、外から叩いて終了コードが仕様どおりかだけを確かめる。
# 仕様の出どころは .claude/skills/team-supervisor/hooks.md の「振る舞い」表。
#
# 確かめる 6 ケース:
#   1. 状態ディレクトリは在るが自分の branch-<agentId> が無い -> 0（サブリーダー以外を邪魔しない）
#   2. 登録があり、そのブランチが origin に無い               -> 2（未 push なので終了を拒む）
#   3. blocked-<agentId> の目印がある                         -> 0（報告済みなので止めない）
#   4. 押し戻しが 3 回を超えた（4 回目）                      -> 1（無限に止め続けない）
#   5. 登録したブランチが origin に push 済み                 -> 0。押し戻し回数の記録も消える
#   6. git worktree の中から叩く（未 push）                   -> 2（共有の .git を見に行く）
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

# フックが標準エラーへ書いた内容の受け皿。落ちたケースの原因を追えるようにするために取る
# （詳しくは invoke_hook と report のコメント）。
hook_log="$sandbox/hook-stderr.log"
: >"$hook_log" || die "フックの標準エラーを受ける一時ファイルを作れません: $hook_log"

# 試験自身の下ごしらえ（git init / push / worktree add）が標準エラーへ書いた内容の受け皿。
# $hook_log とは別に持つ: あちらはフックが書いたもの、こちらは試験のセットアップが書いたもので、
# 混ぜると FAIL の表示にセットアップの出力が紛れ込む。
#
# なぜ取るか: セットアップが失敗すると die が走って試験全体が exit 1 で止まるが、die の 1 行
# だけでは git が何を言って失敗したのかが残らない。CI で落ちたとき、それが「フックが仕様どおり
# 動いていない」のか「CI の環境でセットアップが失敗した」のかを分けたあと、後者なら次に何を
# 調べればよいかの手掛かりが要る。とくに git init の -b は git 2.28 以降、git worktree add は
# git の版・ファイルシステム・既存のブランチ名に左右されるので、理由が消えると調べ直しが長引く。
setup_log="$sandbox/setup-stderr.log"
: >"$setup_log" || die "セットアップの標準エラーを受ける一時ファイルを作れません: $setup_log"

# セットアップの git を、標準出力を捨てて標準エラーだけ $setup_log に取って実行する。
# 呼ぶ git にはすべて -q が付いているので、成功したときの出力は増えない。
run_setup() { # $@ = 実行するコマンド。終了コードはそのまま返す
  : >"$setup_log"
  "$@" >/dev/null 2>"$setup_log"
}

# 直前の run_setup が拾った理由を「 (理由)」の形で返す。何も書かれていなければ何も返さないので、
# die のメッセージへそのまま連結できる。改行は空白に潰して 1 行に収める。
setup_reason() {
  [ -s "$setup_log" ] || return 0
  printf ' (%s)' "$(tr '\n' ' ' <"$setup_log" | sed 's/ *$//')"
}

# git ls-remote がうっかりネットワークへ出た場合に固まらないよう、あれば timeout を挟む。
timeout_cmd=()
command -v timeout >/dev/null 2>&1 && timeout_cmd=(timeout 30)

# 作業リポジトリと、その origin になるローカルの bare リポジトリを作る。
# origin を実在させるのは、ケース 2 で確かめたいのが「origin へは届くが、そのブランチだけが
# 無い」状態だから。origin 自体を作らないと git ls-remote は「remote が解決できない」で落ち、
# ブランチの有無を見ずに同じ終了コードになってしまい、試験としてざるになる。
#
# 失敗したときは return 1 だけを返し、git が言った理由は $setup_log に残す。呼び出し元は
# die のメッセージに $(setup_reason) を足して理由を出す。new_repo は $(new_repo case1) という
# コマンド置換のサブシェルで動くので、変数に取っても呼び出し元へ届かない。ファイルなら届く。
new_repo() { # $1 = 名前。作業リポジトリのパスを標準出力に返す
  local name=$1
  local origin="$sandbox/$name-origin.git"
  local work="$sandbox/$name"
  run_setup git init -q --bare -b main "$origin" || return 1
  run_setup git init -q -b main "$work" || return 1
  run_setup git -C "$work" remote add origin "$origin" || return 1
  run_setup git -C "$work" commit -q --allow-empty -m "init" || return 1
  run_setup git -C "$work" push -q origin main || return 1
  printf '%s' "$work"
}

# フックが状態ファイルを探すのと同じ式で状態ディレクトリを求める。式を揃えておくと、
# 試験が用意した場所とフックが読む場所がずれない。
state_dir_of() { # $1 = 作業リポジトリ
  local common
  common=$(cd "$1" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 1
  printf '%s/team-supervisor' "$common"
}

# リードが他タスクのサブリーダーを登録している状態を作る。実運用ではチームに複数のタスクが
# 居るので状態ディレクトリは必ず在り、「自分の登録だけが無い」という形になる。ディレクトリごと
# 無い状態しか試さないと、フックが「登録の有無」ではなく「ディレクトリの有無」で判断していても
# 気づけない。
register_other_agent() { # $1 = 状態ディレクトリ
  printf '%s' "topic/demo--task-9" >"$1/branch-a0000000000000009"
}

# --- フックを叩く ---

# フックを呼ぶ直前に、行き先が本当に使い捨てのリポジトリかを毎回確かめる。ここが最後の砦で、
# TMPDIR が別のリポジトリの中にある・git init に失敗した、といった場合に本物の .git を
# つかむのを止める。外れていたら叩かずに試験全体を打ち切る。
assert_sandbox() { # $1 = フックを叩くディレクトリ
  local common
  common=$(cd "$1" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  case "$common" in
    "$sandbox"/*) return 0 ;;
  esac
  die "使い捨てリポジトリの外を指しています（$1 -> ${common:-解決できず}）。フックを叩かずに試験を打ち切ります"
}

# フックの標準入力に渡す JSON は必要な 2 つのキーだけにする。フックは agent_id を
# sed の貪欲な .* で拾うので、自由文（last_assistant_message など）に "agent_id" に似た
# 文字列が混ざると別の値を拾いかねない。
#
# 終了コードは標準出力に返さず、グローバル変数 hook_exit_code に入れる。呼び出し側が
# actual=$(invoke_hook ...) と書くと中身がコマンド置換のサブシェルになり、assert_sandbox の
# die が呼ぶ exit 1 がそのサブシェルしか終わらせない。行き先が使い捨てリポジトリの外だと
# 分かったのに、残りのケースがフックを叩き続けてしまうため、コマンド置換を使わない形にする。
#
# フックの標準エラーは捨てずに $hook_log へ足す。フックは押し戻しの理由をそこに書くので、
# 落ちたときに終了コードの数字しか残らないと原因を追えない（report が FAIL のときだけ出す）。
invoke_hook() { # $1 = フックを叩くディレクトリ, $2 = agentId。結果は hook_exit_code に入る
  local work=$1 agent_id=$2
  assert_sandbox "$work"
  printf '{"hook_event_name":"SubagentStop","agent_id":"%s"}' "$agent_id" |
    (cd "$work" && exec "${timeout_cmd[@]}" "$hook") >/dev/null 2>>"$hook_log"
  hook_exit_code=$?
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
    if [ -s "$hook_log" ]; then
      echo "  このケースでフックが標準エラーへ書いた内容:"
      sed 's/^/    /' "$hook_log"
    fi
  fi
  # 次のケースが前のケースの出力を巻き込まないよう、毎回空にする。
  : >"$hook_log"
}

# --- ケース 1: 自分のブランチ登録だけが無い -> 0 ---

# 状態ディレクトリは作り、別の agentId の登録だけを置く。実装・レビュー・Explore など、
# リードが登録していない subagent が終わるときの状態を再現する。
run_case_1() {
  local work state agent_id=a0000000000000001
  work=$(new_repo case1) || die "ケース1 の使い捨てリポジトリを作れません$(setup_reason)"
  state=$(state_dir_of "$work") || die "ケース1 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース1 の状態ディレクトリを作れません"
  register_other_agent "$state" || die "ケース1 の別 agentId の登録を書けません"
  invoke_hook "$work" "$agent_id"
  report "ケース1" "状態ディレクトリは在るが自分の登録が無い -> 素通し" 0 "$hook_exit_code"
}

# --- ケース 2: 登録があり origin に無い -> 2 ---

# origin は生きていて main も push 済み。登録するブランチ名だけを push しないでおく。
run_case_2() {
  local work state agent_id=a0000000000000002
  work=$(new_repo case2) || die "ケース2 の使い捨てリポジトリを作れません$(setup_reason)"
  state=$(state_dir_of "$work") || die "ケース2 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース2 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース2 のブランチ登録を書けません"
  invoke_hook "$work" "$agent_id"
  report "ケース2" "登録あり・origin にそのブランチが無い -> 押し戻し" 2 "$hook_exit_code"
}

# --- ケース 3: blocked の目印がある -> 0 ---

# ケース 2 との違いを blocked-<agentId> の 1 ファイルだけにする。ブランチを push すると
# 「目印が効いた」のか「ブランチが有ったから素通しした」のか区別できなくなる。
run_case_3() {
  local work state agent_id=a0000000000000003
  work=$(new_repo case3) || die "ケース3 の使い捨てリポジトリを作れません$(setup_reason)"
  state=$(state_dir_of "$work") || die "ケース3 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース3 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース3 のブランチ登録を書けません"
  touch "$state/blocked-$agent_id" || die "ケース3 の blocked の目印を作れません"
  invoke_hook "$work" "$agent_id"
  report "ケース3" "blocked-<agentId> の目印がある -> 素通し" 0 "$hook_exit_code"
}

# --- ケース 4: 押し戻しが 3 回を超えた -> 1 ---

# 押し戻し回数のカウンタ（idle-count-<agentId>）はフックが自分で書く内部の状態なので、
# 試験からは書かない。代わりに同じ agentId で 4 回続けて叩き、2,2,2,1 と遷移することを見る。
# カウンタを 3 で置いて 1 回だけ叩く手もあるが、それはファイル名と中身の形に頼るうえ、
# 「押し戻すたびに数える」という遷移そのものを飛ばしてしまう。
run_case_4() {
  local work state actual="" agent_id=a0000000000000004
  work=$(new_repo case4) || die "ケース4 の使い捨てリポジトリを作れません$(setup_reason)"
  state=$(state_dir_of "$work") || die "ケース4 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース4 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース4 のブランチ登録を書けません"
  for _ in 1 2 3 4; do
    invoke_hook "$work" "$agent_id"
    actual="${actual:+$actual,}$hook_exit_code"
  done
  report "ケース4" "未 push のまま 4 回終了しようとする -> 4 回目で打ち切り" "2,2,2,1" "$actual"
}

# --- ケース 5: 登録したブランチが push 済み -> 0、カウンタも消える ---

# ケース 1〜4 は素通し以外がすべて「押し戻す」側なので、どんな入力でも押し戻すだけの
# 壊れた実装を 1 つも捕まえられない。それは push を済ませたサブリーダーが 4 回目に打ち切られる
# という、運用でいちばん重い壊れ方にあたる。ここでは逆向き——push 済みなら通す——を確かめる。
#
# 押し戻し回数の記録（idle-count-<agentId>）を先に 2 で置いておき、フックが消すことも見る。
# hooks.md の同じ行が「素通しし、カウンタを消す」と定めているため。消し忘れると、いったん
# push したあとに次の押し戻しがすぐ上限に届いてしまう。
run_case_5() {
  local work state agent_id=a0000000000000005 counter
  work=$(new_repo case5) || die "ケース5 の使い捨てリポジトリを作れません$(setup_reason)"
  run_setup git -C "$work" push -q origin HEAD:refs/heads/topic/demo--task-1 ||
    die "ケース5 のタスクブランチを push できません$(setup_reason)"
  state=$(state_dir_of "$work") || die "ケース5 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース5 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース5 のブランチ登録を書けません"
  printf '%s' "2" >"$state/idle-count-$agent_id" || die "ケース5 の押し戻し回数を書けません"
  invoke_hook "$work" "$agent_id"
  if [ -e "$state/idle-count-$agent_id" ]; then counter=残った; else counter=消えた; fi
  report "ケース5" "登録したブランチが origin に有る -> 素通しし、押し戻し回数を消す" \
    "0,消えた" "$hook_exit_code,$counter"
}

# --- ケース 6: git worktree の中から叩く -> 2 ---

# ケース 2 と同じ状態（登録あり・未 push）を、作業リポジトリ本体ではなく git worktree の中から
# 叩く。実運用ではサブリーダー配下の subagent は .claude/worktrees/agent-* という worktree で
# 走る。worktree の中では git rev-parse --git-dir が .git/worktrees/<名前> を指し、
# --git-common-dir だけが共有の .git を指す。フックが前者に退化するとリードが書いた
# branch-<agentId> を読めず、全員を素通ししてしまう。素のリポジトリでは両者が同じ値を返すので、
# ケース 2 だけではこの退化に気づけない。
run_case_6() {
  local work worktree state agent_id=a0000000000000006
  work=$(new_repo case6) || die "ケース6 の使い捨てリポジトリを作れません$(setup_reason)"
  worktree="$sandbox/case6-worktree"
  run_setup git -C "$work" worktree add -q -b demo-worktree "$worktree" ||
    die "ケース6 の worktree を作れません: $worktree$(setup_reason)"
  # 状態ディレクトリは共有の .git の下（＝作業リポジトリ本体と同じ場所）に置く。
  state=$(state_dir_of "$worktree") || die "ケース6 の状態ディレクトリを求められません"
  mkdir -p "$state" || die "ケース6 の状態ディレクトリを作れません"
  printf '%s' "topic/demo--task-1" >"$state/branch-$agent_id" || die "ケース6 のブランチ登録を書けません"
  invoke_hook "$worktree" "$agent_id"
  report "ケース6" "worktree の中・登録あり・未 push -> 共有の .git を読んで押し戻し" 2 "$hook_exit_code"
}

# --- 実行して結果を出す ---

echo "対象のフック: $hook"
echo "使い捨てリポジトリ: $sandbox"
echo

run_case_1
run_case_2
run_case_3
run_case_4
run_case_5
run_case_6

echo
if [ -n "$failed_labels" ]; then
  echo "$total 件中 $passed 件成功。外れたケース:$failed_labels"
  exit 1
fi
echo "$total 件中 $passed 件成功"
exit 0
