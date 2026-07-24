"""財務予測エージェント 最小デモ（3視点＋調整AI版）
サンプルデータ（agent/data/sample_expenses.csv）を読み込み、
費目ごとに批判的・中立・推進的の3AGが独立に翌月を予測し（互いに議論はさせない）、
調整AIがその根拠を比較して最終予測に統合する。意見の割れが大きい費目は人間へエスカレーション。
"""
import csv
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from agents import arbiter_agent, forecast_agent

DATA_PATH = Path(__file__).parent / "data" / "sample_expenses.csv"
TARGET_MONTH = "2026-07"
STANCES = ["critical", "neutral", "advocate"]
STANCE_LABELS = {"critical": "批判的AG", "neutral": "中立AG", "advocate": "推進的AG"}


def load_history() -> dict[str, list[tuple[str, int]]]:
    history: dict[str, list[tuple[str, int]]] = defaultdict(list)
    with open(DATA_PATH, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            history[row["category"]].append((row["month"], int(row["amount"])))
    return history


def main():
    history = load_history()
    print(f"=== 財務予測エージェント デモ（3視点＋調整AI・対象月: {TARGET_MONTH}） ===\n")

    for category, rows in history.items():
        print(f"[{category}]（{len(rows)}ヶ月分の実績）")

        stance_results = []
        for stance in STANCES:
            result = forecast_agent.forecast(category, rows, TARGET_MONTH, stance=stance)
            stance_results.append(result)
            amount = result.get("forecast_amount")
            amount_str = f"{amount:,}円" if isinstance(amount, (int, float)) else "パース失敗"
            print(f"  {STANCE_LABELS[stance]:6s} 予測: {amount_str}（確信度: {result.get('confidence')}）")

        final = arbiter_agent.arbitrate(category, TARGET_MONTH, stance_results)
        final_amount = final.get("final_amount")
        final_str = f"{final_amount:,}円" if isinstance(final_amount, (int, float)) else "パース失敗"
        low, high = final.get("range_low"), final.get("range_high")
        range_str = f"（幅: {low:,}〜{high:,}円）" if isinstance(low, (int, float)) and isinstance(high, (int, float)) else ""
        score = final.get("disagreement_score")
        score_str = f"{score:.0%}" if score is not None else "算出不可"

        print(f"  → 調整AI 最終予測: {final_str} {range_str}")
        print(f"     根拠: {final.get('reasoning')}")
        print(f"     意見の割れ: {score_str}", end="")
        if final.get("escalate_to_human"):
            print("  ⚠️ 割れが大きいため人間へのエスカレーション対象")
        else:
            print()
        print()


if __name__ == "__main__":
    main()
