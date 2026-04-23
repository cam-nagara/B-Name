"""禁則処理 (行頭禁則・行末禁則・ぶら下げ).

計画書 3.1.5 参照。metrics.is_kinsoku_start / is_kinsoku_end を利用して
行分割位置を補正する。layout.py 内で簡易処理を行っているが、より詳細な
組版が必要になった際にこのモジュールを本格化する想定。
"""

from __future__ import annotations

from . import metrics


def split_respecting_kinsoku(text: str, max_chars_per_line: int) -> list[str]:
    """素朴な行分割 + 禁則処理.

    max_chars_per_line で機械的に分割した後、行頭禁則/行末禁則を検出
    したら前後の行へ 1 文字ずらす簡易補正を行う。
    """
    if max_chars_per_line < 1:
        return [text]
    lines: list[str] = []
    i = 0
    while i < len(text):
        j = min(i + max_chars_per_line, len(text))
        line = text[i:j]
        # 行末禁則: line[-1] が 「（【 等なら 1 文字戻す
        if j < len(text) and line and metrics.is_kinsoku_end(line[-1]):
            line = line[:-1]
            j -= 1
        # 行頭禁則: 次行先頭が 、。」 なら現在行に吸収 (ぶら下げ簡易版)
        if j < len(text) and metrics.is_kinsoku_start(text[j]):
            line += text[j]
            j += 1
        lines.append(line)
        i = j if j > i else i + 1
    return lines
