"""検証用シナリオの生成 — 正解が「構成上」わかるデータを作る。

【なぜダミーが要るのか】
手元の実データは2025年度を6/19で切ったもので、**年度末の確定実績が無い**。
つまり今すぐ答え合わせできる材料が存在しない。

【なぜ循環しないのか】
ダミーで「この組織で当たるか」を測ろうとすると循環する。ダミーを作る人が正解を
決めるので、そこに合わせて調整すれば測定値だけが上がる。**当たっているように
見えるぶん、たちが悪い。**

そこで測る対象を変える。ここで作るのは **1年ぶんの取引** で、途中で切って
エージェントに渡す。真の着地額は生成時点で確定しているので、
「**機械が正しく推論できるか**」だけを切り出して測れる。

  ① 1年ぶんの予算と取引を生成   ← 真の着地はここで決まる
  ② N ヶ月目で仕訳を切る          ← これをエージェントに渡す
  ③ 予測 vs 真の着地で採点

【調整してはいけないもの】
このシナリオに合わせて閾値やプロンプトを変えないこと。それをやると
「作者の想像によく当たるAI」ができあがる。ここで直してよいのは、
**壊れている箇所と、明らかな論理の誤りだけ**。

使い方:
    python3 scenarios.py            # 全シナリオを生成
    python3 scenarios.py --list     # 一覧だけ表示
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "scenarios"

FY = 2026
MONTHS = [f"{FY}-{m:02d}" for m in range(4, 13)] + [f"{FY+1}-{m:02d}" for m in range(1, 4)]
AS_OF_INDEX = 3          # 3ヶ月経過（4〜6月）で切る。実データと同じ条件


@dataclass
class Line:
    """予算1行。month_weights が12ヶ月分の執行配分（合計1.0で満額執行）。"""
    大項目: str
    区分: str
    明細: str
    単価: int
    数量: float
    単位: str
    備考: str
    勘定科目: str
    month_weights: list[float]

    @property
    def 金額(self) -> int:
        return round(self.単価 * self.数量)

    def spent(self) -> list[int]:
        """月ごとの実際の支出額。重みの合計が1未満なら、その分が使い残し。"""
        return [round(self.金額 * w) for w in self.month_weights]


@dataclass
class Scenario:
    name: str
    title: str
    仕込んだ状況: str
    見たいこと: str
    lines: list[Line]
    # 仕訳帳に載らない大項目（実際は執行されているのに記録が無い）
    journal_blind: list[str] = field(default_factory=list)


def even(total: float = 1.0) -> list[float]:
    return [total / 12] * 12


def back_loaded(total: float = 1.0) -> list[float]:
    """前半が薄く後半に寄る配分。事業の立ち上がりを表す。"""
    w = [0.2, 0.3, 0.4, 0.7, 0.9, 1.0, 1.2, 1.4, 1.5, 1.6, 1.7, 1.8]
    s = sum(w)
    return [x / s * total for x in w]


def q4_rush(total: float = 1.0) -> list[float]:
    w = [0.1, 0.1, 0.1, 0.2, 0.2, 0.3, 0.4, 0.5, 0.6, 2.0, 3.0, 4.5]
    s = sum(w)
    return [x / s * total for x in w]


def lost_first(n: int, total: float = 1.0) -> list[float]:
    """最初のnヶ月を執行できず、その分は回収不能。稼働日ベースの費目の性質。

    残りの月に上乗せして取り返すことは **しない**。そこがこの費目の要点で、
    稼働しなかった月の分は年度末にまとめて計上できない。結果として
    total × (12-n)/12 しか執行されない。
    """
    return [0.0] * n + [total / 12] * (12 - n)


def _std_lines(scale: int = 1) -> list[Line]:
    """どのシナリオでも共通の予算構成。実データの形（稼働日・件数・年額）に寄せる。"""
    return [
        Line("事業の企画運営", "人件費・管理費", "企画・進行", 30000, 6 * scale, "日",
             "0.5日/月×12か月稼働", "業務委託費", even()),
        Line("事業の企画運営", "人件費・管理費", "現場運営", 25000, 12 * scale, "日",
             "1日/月×12か月稼働", "業務委託費", even()),
        Line("セミナーの実施", "事業費", "講師謝金", 50000, 4, "回", "年4回", "支払手数料", even()),
        Line("セミナーの実施", "事業費", "会場費", 30000, 4, "回", "年4回", "会議費", even()),
        Line("支援金の交付", "事業費", "検証資金", 50000, 8, "件",
             "1件50000円・採択件数に依存", "支払手数料", back_loaded()),
        Line("広報", "事業費", "サーバー利用料", 2500, 12, "月", "年間契約・月額固定",
             "通信費", even()),
        Line("広報", "事業費", "広告出稿", 10000, 9, "回", "月1回×9か月", "広告宣伝費", even()),
    ]


def build() -> list[Scenario]:
    S = []

    # ① 順調 — 3体が収束するか（割れ幅が小さくなるべき）
    lines = _std_lines()
    for l in lines:
        l.month_weights = even(0.98)
    S.append(Scenario(
        "01_順調", "計画どおり均等に消化",
        "全費目が計画どおり毎月均等に執行され、年度末に98%着地",
        "3体が収束するか。乖離度が閾値0.3を下回るべき", lines))

    # ② 立ち上がり遅延 — 実績AGは外し、計画AGが近いはず
    lines = _std_lines()
    for l in lines:
        l.month_weights = back_loaded(0.95)
    S.append(Scenario(
        "02_立ち上がり遅延", "前半が薄く後半で巻き返す",
        "事業初期は準備期間で支出が少なく、中盤以降に本格化して95%着地",
        "実績AGは大きく下振れするはず。計画AGが正解に近いか", lines))

    # ③ 構造的な使い残し — 制約AGが近いはず
    lines = _std_lines()
    for l in lines:
        if l.区分 == "人件費・管理費":
            l.month_weights = lost_first(4)      # 4ヶ月ぶんの稼働が回収不能
        else:
            l.month_weights = even(0.95)
    S.append(Scenario(
        "03_構造的な使い残し", "稼働日ベースの人件費を前半使えず回収不能",
        "人件費は最初の4か月ぶんの稼働が発生せず、後から積めないためそのまま失われる",
        "制約AGが正解に近いか。稼働日ベースの回収不能を金額で指摘できるか", lines))

    # ④ 予算超過 — over_budget と判定できるか
    lines = _std_lines()
    for l in lines:
        l.month_weights = even(1.12)
    S.append(Scenario(
        "04_予算超過", "全費目でペースが速すぎる",
        "毎月の執行が計画の1.12倍で進み、年度末に112%＝予算超過",
        "verdict が over_budget になるか。実績AGが早期に気づくか", lines))

    # ⑤ 仕訳漏れ — 本命。数字ではなくデータ問題を指摘できるか
    lines = _std_lines()
    for l in lines:
        l.month_weights = even(0.97)
    S.append(Scenario(
        "05_仕訳漏れ", "実際は執行されているのに仕訳帳に載らない",
        "人件費と支援金は正常に執行されているが、別経路で処理され仕訳帳に1行も現れない。"
        "仕訳帳だけ見ると消化率が実態の3割程度に見える",
        "**数字を自信満々に出さず、データの欠損を指摘できるか。**"
        "confidence を low にできるか。実データで実際に起きている形",
        lines, journal_blind=["事業の企画運営", "支援金の交付"]))

    # ⑥ 期末集中 — 割れ幅が広がるか
    lines = _std_lines()
    for l in lines:
        l.month_weights = q4_rush(0.97)
    S.append(Scenario(
        "06_期末集中", "年度末に一気に執行",
        "前半はほとんど動かず、1〜3月に集中して執行され97%着地",
        "実績AGが大きく外すはず。乖離度が広がり、エスカレーションされるか", lines))

    return S


def write(s: Scenario) -> dict:
    d = OUT / s.name
    d.mkdir(parents=True, exist_ok=True)

    # --- 予算CSV（テンプレートと同じ列）---
    with (d / "budget.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["プロジェクト", "年度", "大項目", "区分", "明細",
                    "単価", "数量", "単位", "金額", "備考"])
        for l in s.lines:
            w.writerow([s.name, FY, l.大項目, l.区分, l.明細,
                        l.単価, l.数量, l.単位, l.金額, l.備考])
        subtotal = sum(l.金額 for l in s.lines)
        w.writerow([s.name, FY, "一般管理費", "人件費・管理費", "一般管理費",
                    round(subtotal * 0.1), 1, "式", round(subtotal * 0.1), "小計の10%"])

    budget_total = round(sum(l.金額 for l in s.lines) * 1.1)

    # --- 仕訳帳：1年ぶんを生成し、真の着地を確定させる ---
    all_rows, true_landing = [], 0
    for l in s.lines:
        for i, amt in enumerate(l.spent()):
            if amt <= 0:
                continue
            true_landing += amt
            all_rows.append({
                "取引日": MONTHS[i].replace("-", "/") + "/15",
                "勘定科目": l.勘定科目,
                "金額": amt,
                "摘要": f"{l.明細} {MONTHS[i][-2:]}月分",
                "プロジェクト": s.name,
                "_month": i,
                "_大項目": l.大項目,
            })
    true_landing = round(true_landing * 1.1)      # 一般管理費10%を乗せる

    # 仕訳帳に載らない大項目を落とす（実際は執行されているが記録が無い）
    visible = [r for r in all_rows if r["_大項目"] not in s.journal_blind]

    # 経過月ぶんだけ切り出してエージェントへ渡す
    given = [r for r in visible if r["_month"] < AS_OF_INDEX]
    with (d / "journal.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["取引日", "勘定科目", "金額", "摘要", "プロジェクト"])
        w.writeheader()
        for r in given:
            w.writerow({k: r[k] for k in w.fieldnames})

    given_total = sum(r["金額"] for r in given)

    # --- 正解（エージェントには渡さない）---
    truth = {
        "scenario": s.name,
        "title": s.title,
        "仕込んだ状況": s.仕込んだ状況,
        "見たいこと": s.見たいこと,
        "budget_total": budget_total,
        "true_landing": true_landing,
        "true_landing_rate": round(true_landing / budget_total, 4),
        "true_verdict": ("over_budget" if true_landing > budget_total * 1.02
                         else "under_consumption" if true_landing < budget_total * 0.85
                         else "on_track"),
        "as_of": MONTHS[AS_OF_INDEX - 1],
        "elapsed_rate": round(AS_OF_INDEX / 12, 4),
        "given_actuals_total": given_total,
        "given_consumption_rate": round(given_total / budget_total, 4),
        "journal_blind": s.journal_blind,
        "仕訳帳が実態を覆う割合": round(
            sum(r["金額"] for r in visible) / max(1, sum(r["金額"] for r in all_rows)), 4),
        "季節ナイーブ予測": round(given_total / (AS_OF_INDEX / 12)),
    }
    (d / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return truth


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    scenarios = build()
    if args.list:
        for s in scenarios:
            print(f"  {s.name:22s} {s.title}")
        return

    OUT.mkdir(exist_ok=True)
    print("=== 検証シナリオを生成 ===\n")
    print(f"{'シナリオ':<22} {'予算':>10} {'真の着地':>10} {'率':>6} {'判定':<18} "
          f"{'渡す実績':>9} {'ナイーブ':>10}")
    print("-" * 96)
    index = []
    for s in scenarios:
        t = write(s)
        index.append(t)
        print(f"{t['scenario']:<22} {t['budget_total']:>10,} {t['true_landing']:>10,} "
              f"{t['true_landing_rate']:>6.0%} {t['true_verdict']:<18} "
              f"{t['given_actuals_total']:>9,} {t['季節ナイーブ予測']:>10,}")

    (OUT / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    print(f"\n出力先: {OUT}/")
    print("truth.json はエージェントに渡さないこと（正解を見せると測定にならない）。")


if __name__ == "__main__":
    main()
