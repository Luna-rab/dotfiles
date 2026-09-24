"""autodev の driver が使う部品。

**段（エージェント）はここを import しない。** 段が呼ぶのは `autodev review …` の
サブコマンドだけで、進行状態（state.json）には触らせない。
"""
