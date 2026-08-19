"""証拠の分割 — 3視点AGに「それぞれ違うもの」を配る。

【なぜ分けるのか】
全エージェントに同一の証拠を与えると、熟議は同調(herding)に堕して単一エージェントと
変わらなくなる（InfoDelphi, 2026-07）。証拠を共有部分と専有部分に分けると、
Brierスコアが12〜18%、精度が4〜8ポイント改善すると報告されている。

【なぜPythonでやるのか】
「どのAGが何を見るか」は守るべき境界であり、確定処理である。プロンプトで
「これは見ないでください」と頼む形だと、ファイルには全部書いてあるので実効性がない。
物理的にファイルを分けることで、見えないものは見えない状態にする。

使い方:
    python3 split_evidence.py P3
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).parent / "output"

# 稼働日ベースなど「後から積めない」性質を示す語。制約AGの判断材料になる。
CONSTRAINT_HINTS = ("稼働日", "後から積めない", "件", "年間契約", "月次", "ヶ月", "年額")

# 収集AGは予算と実績の両方を見ているため、実績側の所見に予算額を書き添えることがある。
# 例:「人件費の仕訳が1件も無い（予算では1,750,320円計上）」。前半は実績の事実だが、
# 括弧内は計画AGの担当領域であり、実績AGに渡ると証拠の分割が崩れる。
BUDGET_WORDS = ("予算", "計上されている", "予算額")
PAREN_WITH_BUDGET = re.compile(r"[（(][^（()）]*予算[^（()）]*[)）]")


def _strip_budget(notes: list) -> tuple[list[str], int]:
    """実績側の所見から予算への言及を落とす。落とせない所見は丸ごと捨てる。"""
    kept, dropped = [], 0
    for n in notes:
        s = PAREN_WITH_BUDGET.sub("", str(n)).strip()
        if any(w in s for w in BUDGET_WORDS):
            dropped += 1
            continue
        kept.append(s)
    return kept, dropped


def split(pid: str) -> None:
    src = OUT / f"collected_{pid}.json"
    d = json.loads(src.read_text(encoding="utf-8"))

    base = {
        "project": d["project"],
        "fiscal_year": d["fiscal_year"],
        "fiscal_period": d["fiscal_period"],
        "as_of": d["as_of"],
    }

    # --- 共有（全員が見てよい最低限の土台）---
    shared = {
        **base,
        "budget_total": d["budget"]["total"],
        "actuals_total": d["actuals"]["total"],
        "elapsed": d["elapsed"],
        "consumption_rate": d["consumption_rate"],
        "note": "これは3視点で共有する土台。詳細は各自の専有ファイルにある",
    }

    # --- 実績AG専有（予算の内訳は入れない）---
    act = dict(d["actuals"])
    act["notes"], n_dropped = _strip_budget(act.get("notes", []))
    actuals = {
        **base,
        "actuals": act,
        "note": "予算の内訳は意図的に渡していない。実績だけから外挿すること",
    }
    if n_dropped:
        print(f"  （実績スライスから予算に触れた所見を{n_dropped}件除去）")

    # --- 計画AG専有（実績の月次推移は入れない）---
    plan = {
        **base,
        "budget": {
            "total": d["budget"]["total"],
            "subtotal": d["budget"]["subtotal"],
            "overhead": d["budget"]["overhead"],
            "by_type": d["budget"]["by_type"],
            "by_category": d["budget"]["by_category"],
            "plan_notes": d["budget"]["plan_notes"],
        },
        # 「どれだけ既に消化したか」は残額計算に要るので総額だけ渡す。
        # 月次推移（ペースの情報）は渡さない。
        "already_spent_total": d["actuals"]["total"],
        "mapping": d.get("mapping", []),
        "note": "実績の月次推移は意図的に渡していない。計画の残りを積み上げること",
    }

    # --- 制約AG専有（費目の性質と期限）---
    constraint_notes = [n for n in d["budget"]["plan_notes"]
                        if any(h in str(n) for h in CONSTRAINT_HINTS)]
    constraints = {
        **base,
        "budget_by_category": d["budget"]["by_category"],
        "budget_by_type": d["budget"]["by_type"],
        "cost_nature_notes": constraint_notes,
        "elapsed": d["elapsed"],
        "months_remaining": d["elapsed"]["months_total"] - d["elapsed"]["months_elapsed"],
        "already_spent_total": d["actuals"]["total"],
        "spent_by_account": d["actuals"]["by_account"],
        "note": (
            "費目の性質と残り期間から、執行可能な上限を積み上げること。"
            "稼働日ベースの費目は経過分を後から積めない点に注意"
        ),
    }

    for name, payload in [("shared", shared), ("actuals", actuals),
                          ("plan", plan), ("constraints", constraints)]:
        p = OUT / f"collected_{pid}_{name}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {p.name:34s} {len(p.read_text(encoding='utf-8')):>6,}字")


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "P3"
    print(f"=== {pid} の証拠を分割 ===")
    split(pid)
    print("\n各AGは自分のファイルと shared だけを読む。互いの専有ファイルは渡さない。")
