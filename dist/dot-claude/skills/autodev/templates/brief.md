# ブリーフ（${run_name}）

- 対象リポジトリ: `${repo}`
- base ブランチ: `${base}`
- 概要ブランチ: `${overview_branch}`

## 検証コマンド一式

**driver が PR を作る前にこれを流す。落ちたら完了にならない。**

${verify}

## テストとみなすパス

**実装ステージはここに書き込めない**（フックが止める）。テストを書くのはテスト作成ステージだけで、
実装ステージはテストが仕様と矛盾していると判断したら `testConflict` で報告する。

${test_globs}

${protected}## ブランチと PR の規約

- タスクのブランチ: `stack/${run_name}--task-<番号>`
- **PR を作るのは driver だけ。** ステージは commit までで止める。`gh` を呼ばない。
- マージは人間が `gh stack merge` で下から行う。

## 気をつけること

${notes}
