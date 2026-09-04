"""シナリオの採点 — 予測が真の着地にどれだけ近いかを測る。

【何を測っているか】
「この組織で当たるか」ではない。それはダミーでは測れない。ここで測るのは
**機械が正しく推論できるか**で、次の4点を見る。

  ① 誤差        真の着地との差。ただし季節ナイーブに勝てなければ意味がない
  ② 判定の向き   over_budget / under_consumption / on_track が合っているか
  ③ 割れ方の較正 3体の割れ幅が「本当に不確実なとき」に広がっているか
  ④ 欠損の検出   データが実態を覆っていないとき、それを言えているか

【③が一番重要な理由】
予測が外れること自体は避けられない。避けたいのは **外れているのに自信満々**
であること。割れ幅と確信度が不確実性と連動していれば、外れても「これは信用
するな」と言えるので使える。連動していなければ、当たった予測も偶然になる。

使い方:
    python3 score_scenarios.py              # scenarios/ 配下を全部採点
    python3 score_scenarios.py 05_仕訳漏れ    # 個別
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
SC = BASE / "scenarios"


def score_one(d: Path) -> dict | None:
    truth = json.loads((d / "truth.json").read_text(encoding="utf-8"))
    fc_files = sorted(d.glob("forecast*.json"))
    if not fc_files:
        return None
    fc = json.loads(fc_files[-1].read_text(encoding="utf-8"))

    true_landing = truth["true_landing"]
    pred = fc["final_landing"]
    naive = truth["季節ナイーブ予測"]

    err = abs(pred - true_landing) / true_landing
    naive_err = abs(naive - true_landing) / true_landing

    stances = {s["stance"]: s["landing_estimate"] for s in fc.get("stances", [])}
    stance_err = {k: abs(v - true_landing) / true_landing for k, v in stances.items()}

    # ③ 割れ方の較正：本当に難しい局面で割れているか。
    #    仕訳帳が実態を覆えていないほど、割れは大きくあってほしい。
    coverage = truth["仕訳帳が実態を覆う割合"]

    return {
        "シナリオ": truth["scenario"],
        "見たいこと": truth["見たいこと"],
        "真の着地": true_landing,
        "予測": pred,
        "誤差": round(err, 4),
        "ナイーブ": naive,
        "ナイーブ誤差": round(naive_err, 4),
        "ナイーブに勝った": err < naive_err,
        "真の判定": truth["true_verdict"],
        "予測の判定": fc.get("verdict"),
        "判定が一致": truth["true_verdict"] == fc.get("verdict"),
        "確信度": fc.get("confidence"),
        "乖離度": fc.get("disagreement"),
        "仕訳帳の被覆率": coverage,
        "各AGの誤差": {k: round(v, 4) for k, v in stance_err.items()},
        "最も近かったAG": min(stance_err, key=stance_err.get) if stance_err else None,
    }


def main() -> None:
    targets = ([SC / a for a in sys.argv[1:]] if len(sys.argv) > 1
               else sorted(p for p in SC.iterdir() if p.is_dir()))
    results = [r for r in (score_one(d) for d in targets if d.is_dir()) if r]

    if not results:
        raise SystemExit("採点できる予測がない。各シナリオで forecast*.json を作ってから実行する。")

    print("=== シナリオ採点 ===\n")
    print(f"{'シナリオ':<20} {'真の着地':>10} {'予測':>10} {'誤差':>7} "
          f"{'ナイーブ':>7} {'勝敗':<5} {'判定':<5} {'確信度':<7} {'乖離':>6}")
    print("-" * 92)
    for r in results:
        print(f"{r['シナリオ']:<20} {r['真の着地']:>10,} {r['予測']:>10,} "
              f"{r['誤差']:>6.1%} {r['ナイーブ誤差']:>7.1%} "
              f"{'○' if r['ナイーブに勝った'] else '×':<5} "
              f"{'○' if r['判定が一致'] else '×':<5} "
              f"{str(r['確信度']):<7} {r['乖離度'] if r['乖離度'] is not None else '—':>6}")

    n = len(results)
    win = sum(r["ナイーブに勝った"] for r in results)
    hit = sum(r["判定が一致"] for r in results)
    avg = sum(r["誤差"] for r in results) / n

    print(f"\n  平均誤差 {avg:.1%} ／ ナイーブに勝ち {win}/{n} ／ 判定一致 {hit}/{n}")

    # ③ 較正の確認：被覆率が低いシナリオで、確信度を下げられているか
    print("\n=== 較正（外れるときに「信用するな」と言えているか）===")
    for r in sorted(results, key=lambda x: x["仕訳帳の被覆率"]):
        warn = r["確信度"] == "low" or (r["乖離度"] or 0) > 0.3
        mark = "✅" if (r["仕訳帳の被覆率"] < 0.9) == warn or r["仕訳帳の被覆率"] >= 0.9 else "⚠️"
        print(f"  {mark} {r['シナリオ']:<20} 被覆率 {r['仕訳帳の被覆率']:>5.0%} → "
              f"確信度 {str(r['確信度']):<7} 乖離 {r['乖離度']}  誤差 {r['誤差']:.1%}")

    print("\n=== 各AGの誤差（どの視点がどの局面で強いか）===")
    for r in results:
        best = r["最も近かったAG"]
        errs = " ".join(f"{k}={v:.0%}" for k, v in r["各AGの誤差"].items())
        print(f"  {r['シナリオ']:<20} {errs}   → 最良: {best}")

    (SC / "score.json").write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"\n出力: {SC / 'score.json'}")
    print("\n⚠️ この結果に合わせて閾値やプロンプトを調整しないこと。")
    print("   直してよいのは壊れている箇所と明らかな論理の誤りだけ。")


if __name__ == "__main__":
    main()
