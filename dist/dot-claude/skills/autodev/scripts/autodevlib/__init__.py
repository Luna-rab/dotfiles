"""autodev の driver が使う部品。

**ステージ（エージェント）はここを import しない。** ステージが呼ぶのは `autodev review …` の
サブコマンドだけで、進行状態（state.json）には触らせない。
"""
