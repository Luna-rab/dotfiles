"""team-supervisor の付属スクリプトが共有する内部モジュール。

エージェントはこの中を直接叩かない。エージェントが呼ぶのは `scripts/` 直下の実行ファイル
（`place.py` / `state.py` / `verify.py` / `worktree.py` / `gh-review.py`）だけである。

分け方の基準は「どの外部と話すか」である。

- `shell.py`   — 外部コマンドの実行と、標準出力・エラー出力の書式
- `gitpath.py` — git のディレクトリ配置（worktree・状態ファイル・ベース資料の置き場）
- `ghapi.py`   — GitHub の GraphQL 呼び出しと、リポジトリ・PR の同定
- `reviewbody.py`    — レビューコメント本文の組み立てと解析（GitHub を呼ばない）
- `reviewthreads.py` — レビュースレッドと提出済みレビューの取得（GitHub を呼ぶ）
"""
