"""supervisor の付属スクリプトが共有する内部モジュール。

エージェントはこの中を直接叩かない。エージェントが呼ぶのは `scripts/` 直下の実行ファイル
（`place.py` / `review.py` / `verify.py` / `worktree.py`）だけである。

分け方の基準は「どの外部と話すか」である。

- `shell.py`   — 外部コマンドの実行と、標準出力・エラー出力の書式
- `gitpath.py` — git のディレクトリ配置（worktree・ベース資料と引き継ぎノートの置き場）
- `reviewstore.py` — review.json の読み書きと、役割・rating・status 遷移の検査

**指摘の往復は review.json に閉じている。** レビュアーも裁定も GitHub に何も投稿しない。
"""
