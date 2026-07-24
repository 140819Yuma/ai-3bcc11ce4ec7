"""財務予測エージェント 可視化ダッシュボード（ローカルWebアプリ）

起動:  python3 app.py  → ブラウザで http://127.0.0.1:5001 を開く

各費目の月次実績を折れ線で表示し、「予測する」を押すと
批判的AG・中立AG・推進的AGが独立に翌月を予測 → 調整AIが統合し、
チャート上に3視点の予測点と最終予測レンジを重ねて表示する。
意見の割れが大きい費目は人間へのエスカレーションとして警告表示。
"""
from collections import OrderedDict
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

load_dotenv()

from agents import arbiter_agent, forecast_agent
from data_loader import load_expenses

app = Flask(__name__)

DATA_DIR = Path(__file__).parent / "data"
TARGET_MONTH = "2026-07"
STANCES = ["critical", "neutral", "advocate"]
TOTAL_LABEL = "総支出"

# --- 予算管理（石川さんの中核要件：あといくら使えるか／使ったか）---
# 予算 − 実績累計 は足し算引き算（AI不要）。その上に予測AIを「着地見込み」として乗せる。
ANNUAL_BUDGET = 22_000_000          # 年度予算（とりあえず定数。後でExcelに移せる）
FISCAL_LABEL = "2026年度（1〜12月）"
FISCAL_MONTHS = [f"2026-{m:02d}" for m in range(1, 13)]

# 渡されたデータファイルを優先的に使う（Excel → CSV → 同梱サンプルの順）。
# 会計ソフトの月次推移(横持ち)でも縦持ちでも自動判別して読む。
DATA_CANDIDATES = [
    DATA_DIR / "expenses.xlsx",
    DATA_DIR / "expenses.csv",
    DATA_DIR / "sample_expenses.csv",
]


def _data_path() -> Path:
    for p in DATA_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("data/ に経費データが見つかりません")


def load_history() -> "OrderedDict[str, list[dict]]":
    return load_expenses(_data_path())


def load_total() -> "OrderedDict[str, list[dict]]":
    """全費目を月ごとに合算し、「総支出」1本の系列にまとめる（とりあえずの単一支出ビュー）。"""
    monthly: "OrderedDict[str, int]" = OrderedDict()
    for rows in load_history().values():
        for r in rows:
            monthly[r["month"]] = monthly.get(r["month"], 0) + r["amount"]
    series = [{"month": m, "amount": monthly[m]} for m in sorted(monthly)]
    return OrderedDict([(TOTAL_LABEL, series)])


def budget_base(total_series: list) -> dict:
    """予算に対する現状（AI不要の足し算引き算）。今年度に使った額と残予算。"""
    spent = sum(r["amount"] for r in total_series if r["month"] in FISCAL_MONTHS)
    elapsed = sum(1 for r in total_series if r["month"] in FISCAL_MONTHS)
    return {
        "annual_budget": ANNUAL_BUDGET,
        "fiscal_label": FISCAL_LABEL,
        "spent": spent,                                  # いくら使ってしまったか
        "remaining_budget": ANNUAL_BUDGET - spent,       # あといくら使えるか
        "elapsed_months": elapsed,
        "remaining_months": len(FISCAL_MONTHS) - elapsed,
    }


def budget_landing(base: dict, forecast_monthly) -> dict:
    """予測AIの月額を残り月に延長した「年度末の着地見込み」と予算超過額。"""
    out = dict(base)
    if isinstance(forecast_monthly, (int, float)):
        projected = base["spent"] + forecast_monthly * base["remaining_months"]
        out["forecast_monthly"] = forecast_monthly
        out["projected_landing"] = projected
        out["over_amount"] = projected - base["annual_budget"]  # +なら超過
    return out


@app.route("/")
def index():
    total = load_total()
    return render_template(
        "dashboard.html",
        history=total,
        target_month=TARGET_MONTH,
        budget=budget_base(total[TOTAL_LABEL]),
    )


@app.route("/api/forecast", methods=["POST"])
def api_forecast():
    category = (request.get_json() or {}).get("category")
    history = load_total()
    if category not in history:
        return jsonify({"error": f"unknown category: {category}"}), 400

    rows = [(h["month"], h["amount"]) for h in history[category]]
    stance_results = [
        forecast_agent.forecast(category, rows, TARGET_MONTH, stance=s)
        for s in STANCES
    ]
    final = arbiter_agent.arbitrate(category, TARGET_MONTH, stance_results)

    budget = None
    if category == TOTAL_LABEL:
        budget = budget_landing(
            budget_base(history[TOTAL_LABEL]), final.get("final_amount")
        )

    return jsonify(
        {"category": category, "target_month": TARGET_MONTH,
         "stances": stance_results, "final": final, "budget": budget}
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001)
