"""3視点の意見を数値として集計する — 調整AIの手前の確定処理。

【役割分担】
・Python（このファイル）… 中央値・レンジ・乖離度の計算、各AGの算数の検算
・調整AI（LLM）        … なぜ割れたのかの解釈、採用する見方の判断、示唆の言語化

数字をLLMに数えさせない。逆に、意見の意味づけをPythonにさせない。
どちらも「できてしまう」が、できることと任せてよいことは違う。

使い方:
    python3 aggregate.py P3
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

OUT = Path(__file__).parent / "output"
STANCES = ("actuals", "plan", "constraint")

# 乖離度の閾値。これを超えたら「3視点が同じ絵を見ていない」とみなし、
# 平均で丸めずに割れたまま提示する。丸めると判断材料が消える。
DISAGREEMENT_THRESHOLD = 0.30


def load(pid: str) -> tuple[dict, list[dict]]:
    shared = json.loads((OUT / f"collected_{pid}_shared.json").read_text(encoding="utf-8"))
    opinions = []
    for s in STANCES:
        p = OUT / f"stance_{pid}_{s}.json"
        if not p.exists():
            raise SystemExit(f"意見が揃っていません: {p.name}")
        opinions.append(json.loads(p.read_text(encoding="utf-8")))
    return shared, opinions


def audit(shared: dict, opinions: list[dict]) -> list[str]:
    """各AGが自分で出した数字の整合を検算する。LLMの算数は信用せず必ず突き合わせる。"""
    budget = shared["budget_total"]
    spent = shared["actuals_total"]
    issues = []
    for o in opinions:
        est, rate = o["landing_estimate"], o["landing_rate"]
        expected = est / budget
        if abs(expected - rate) > 0.01:
            issues.append(
                f"{o['stance']}: landing_rate {rate} が {est:,}÷{budget:,}={expected:.3f} と一致しない"
            )
        if est < spent:
            issues.append(f"{o['stance']}: 着地 {est:,}円 が既支出 {spent:,}円 を下回っている")
        if est > budget:
            issues.append(f"{o['stance']}: 着地 {est:,}円 が予算 {budget:,}円 を超えている")
    return issues


def aggregate(pid: str) -> dict:
    shared, opinions = load(pid)
    budget = shared["budget_total"]
    ests = [o["landing_estimate"] for o in opinions]
    med = statistics.median(ests)

    # 乖離度 =（最大−最小）÷ 中央値。予算に対する比ではなく意見どうしの開きを見る。
    disagreement = (max(ests) - min(ests)) / med if med else 0.0

    return {
        "project": pid,
        "as_of": shared["as_of"],
        "budget_total": budget,
        "actuals_total": shared["actuals_total"],
        "elapsed_rate": shared["elapsed"]["elapsed_rate"],
        "consumption_rate": shared["consumption_rate"],
        "opinions": opinions,
        "range": {"min": min(ests), "median": int(med), "max": max(ests)},
        "range_rate": {
            "min": round(min(ests) / budget, 3),
            "median": round(med / budget, 3),
            "max": round(max(ests) / budget, 3),
        },
        "spread_yen": max(ests) - min(ests),
        "disagreement": round(disagreement, 3),
        "converged": disagreement <= DISAGREEMENT_THRESHOLD,
        "threshold": DISAGREEMENT_THRESHOLD,
        "audit_issues": audit(shared, opinions),
    }


if __name__ == "__main__":
    pid = sys.argv[1] if len(sys.argv) > 1 else "P3"
    r = aggregate(pid)
    path = OUT / f"aggregate_{pid}.json"
    path.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== {pid} 集計（as_of {r['as_of']}）===")
    print(f"予算 {r['budget_total']:,}円 / 実績 {r['actuals_total']:,}円 "
          f"/ 経過率 {r['elapsed_rate']:.0%} / 消化率 {r['consumption_rate']:.0%}")
    print()
    for o in r["opinions"]:
        print(f"  {o['stance']:10s} {o['landing_estimate']:>10,}円 "
              f"({o['landing_rate']:>5.1%})  {o['risk']:<18s} 確信度 {o['confidence']}")
    print()
    print(f"  レンジ    {r['range']['min']:,} 〜 {r['range']['max']:,}円 "
          f"（中央値 {r['range']['median']:,}円、開き {r['spread_yen']:,}円）")
    print(f"  乖離度    {r['disagreement']:.3f}（閾値 {r['threshold']}）"
          f" → {'収束' if r['converged'] else '不一致：割れたまま提示する'}")
    if r["audit_issues"]:
        print("\n  ⚠ 検算で見つかった不整合:")
        for i in r["audit_issues"]:
            print(f"    - {i}")
    else:
        print("  検算    3視点とも数値の整合に問題なし")
    print(f"\n出力: {path.name}")
