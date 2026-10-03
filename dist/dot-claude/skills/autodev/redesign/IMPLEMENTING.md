# 実装の作法

autodev の作り直しを実装するときに、全員が従うこと。設計は [ARCHITECTURE.md](ARCHITECTURE.md)・[DOMAIN_MODEL.md](DOMAIN_MODEL.md)・[ADDENDUM.md](ADDENDUM.md)（食い違えば ADDENDUM が正しい）、外部の仕組みの事実は [LEDGER.md](LEDGER.md) の「持ち込む」にある。

## 1. 絶対に守ること: ビジネスロジックはドメイン層にだけ書く

**「何が起きたら何をするか」「してよいか・いけないか」「次に何を走らせるか」「どの状態に移るか」は、すべてドメイン層（`autodevlib/domain/`）に書く。** ドメイン層の外には 1 行も書かない。

| 層 | 書いてよいもの | 書いてはいけないもの |
| --- | --- | --- |
| `domain/` | 値オブジェクト、コマンド、イベント、集約（`handle` と `apply`）、ポリシー（イベント → コマンドの一覧を返す純粋な関数）、ドメインサービス、ステージの定義（`StageSpec`） | I/O（ファイル・プロセス・ネットワーク・時刻・乱数・環境変数）、`app`・`infra`・`adapters` の import |
| `app/` | メインループ、受け手（ポリシーと反応）の登録と配達、統括の判断の JSON をコマンドに置き換える表、反応（ドメインが決めた副作用を、ポートを呼んで実行するだけ）、実行器（ステージを起動し、証拠を集めて `ReportStageResult` にするだけ） | 条件分岐で進め方を決めること（「この場合は別のステージへ」「この種類のタスクなら飛ばす」など）。判断が要るなら、ドメインに聞く |
| `infra/` | イベントストア（SQLite）、`requests`、チェックポイント、`status --json` の組み立て | ビジネスロジック |
| `adapters/` | `claude -p`・git・gh・サブプロセス・ガードのフックとの橋渡し。外部の仕組みの事実（LEDGER）はここで扱う | ビジネスロジック |

- **特別扱いの分岐を、ドメインの外に足さない。** 設計書の細部（とくに ADDENDUM）を実装するときに、アプリケーション層やアダプタに `if task.kind == ...` のような分岐を足したくなったら、それはドメインの規則が足りていない印である。ドメインの集約・ポリシー・ドメインサービスに、名前の付いた規則として足す。
- **設計書が細かすぎて、規則がその場しのぎになりそうなら、より一般的なドメインの規則で表してよい。** そのときは、設計書と違う形にしたことと理由を、最後の報告に書く（後で設計書を直す）。設計書を文字どおりなぞるために、特別扱いを散らさない。
- **反応は判断しない。** 反応がすることは、イベントに書かれたことをポートで実行するだけである。「実行するかどうか」はイベントが出た時点でドメインが決めている。
- **実行器は判断しない。** 証拠（コミット数・ファイルの有無・検証の結果・終了の仕方・defer されたか）を集めて渡すだけで、完了・失敗・エスカレーションのどれにするかは `Task.handle` が決める。
- 層の依存の向きは、検査（`test/autodev/test_layers.py`）で確かめる。`domain/` が `domain/` の外を import したら落ちる。

## 2. 置き場

```
dist/dot-claude/skills/autodev/
  SKILL.md
  scripts/
    autodev.py              入口（数行。autodevlib.cli を呼ぶだけ）
    autodevlib/
      domain/               ビジネスロジックのすべて
        values.py           値オブジェクト
        commands.py         コマンド
        events.py           ドメインイベント（版の番号付き。名前 → クラスの表を持つ）
        stages.py           ステージの種類と StageSpec の一覧・Guard
        flow.py             Flow・FlowStep・Cursor・FlowValidator
        aggregate.py        集約の土台（handle と apply の振り分け・処理済みのコマンドの id）
        run.py task.py review.py design.py stack.py questions.py   集約
        policies.py         ポリシー（イベント → コマンドの一覧）
        services/           ドメインサービス
      app/                  メインループ・配達・反応・実行器・統括の起動
      infra/                イベントストア・requests・チェックポイント・status
      adapters/             AgentRuntime・Git・Forge・ProcessRunner
      cli.py
  hooks/                    ガードのフック（PreToolUse）
  contracts/                ステージと統括への指示書
  schemas/                  ステージと統括が返す JSON の形
  templates/                PR 本文などの雛形
test/autodev/               検査（pytest）。dist の中には置かない
```

ファイルの分け方は、大きくなったら分けてよい。層の境目（`domain/` とそれ以外）は動かさない。

## 3. 書き方

- Python 3.10 以上・標準ライブラリだけ。`from __future__ import annotations`。型を付ける
- 値オブジェクト・コマンド・イベントは `@dataclass(frozen=True)`
- 時刻・id・HEAD のような外の値は、ドメインの中で取らず、コマンドの中身として受け取る
- docstring とコメントは日本語。周りに合わせ、「なぜそうしたか」と「知らないと間違えること」だけを書く。処理の言い直しは書かない
- 旧実装は消えるので、外部の仕組みの扱いを確かめたいときは 旧実装が残っている固定のコミット `56b72fa` から `git show 56b72fa:<path>` で引く（例: `git show 56b72fa:dist/dot-claude/skills/autodev/scripts/autodevlib/ports/runner.py`。main はマージの後に旧実装を指さなくなるので使わない）。**旧実装のビジネスロジックは持ち込まない**（LEDGER の「持ち込まない」）

## 4. 検査

作業を終える前に、すべて通す。

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
.claude/scripts/check-skills.py
```

- ドメインの規則は、イベントの列と コマンドだけで書けるテストで確かめる（I/O 無し）
- アダプタは、外部コマンドを偽物に差し替えて確かめる。本物の `claude`・`gh` は叩かない

## 5. コミット

- ブランチは `feature/autodev-rewrite`。main では作業しない。push しない
- 意味のまとまりごとにコミットする。メッセージは日本語で、1 行目に何をしたか、本文に理由。末尾に次の 1 行を付ける

  ```
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

- `git add` は自分が触ったパスだけ

## 6. 最後の報告

- 作ったもの（ファイルと役目）、コミットのハッシュ、検査の結果
- 設計書と違う形にしたところと理由（後で設計書を直す）
- 設計書だけでは決められず、自分で決めたこと
