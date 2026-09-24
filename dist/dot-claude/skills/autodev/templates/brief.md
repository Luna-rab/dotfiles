# 前提（${work}）

- 対象リポジトリ: `${repo}`
- base ブランチ: `${base}`
- 土台ブランチ: `${stack_branch}`

## 検証コマンド一式

**driver が PR を作る前にこれを流す。落ちたら完了にならない。**

${verify}

## テストとみなすパス

**実装段はここに書き込めない**（フックが止める）。テストを書くのはテスト作成段だけで、
実装段はテストが仕様と矛盾していると判断したら `testConflict` で申告する。

${test_globs}

${protected}## ブランチと PR の規約

- タスクのブランチ: `stack/${work}--task-<番号>`
- **PR を作るのは driver だけ。** 段は commit までで止める。`gh` を呼ばない。
- マージは人間が `gh stack merge` で下から行う。

## 気をつけること

${notes}
