"""財務予測エージェント — 予算消化ダッシュボード

起動:  python3 app.py  → ブラウザで http://127.0.0.1:5001

【役割分担】
  分析: Claude Code 側（.claude/agents/ の5体 ＋ fc-forecast スキル）
        → agent/output/forecast_<YYYY-MM>.json を出力する
  表示: このアプリ
        → そのJSONを読んで可視化するだけ。**LLMは一切呼ばない。**

分析と表示を分離してあるため、モデルやAPIを差し替えても
このアプリは変更不要（出力JSONの形だけが両者の契約）。
"""
from __future__ import annotations  # dict | None 記法をPython 3.9でも使うため

import json
from pathlib import Path

from flask import Flask, jsonify, render_template

app = Flask(__name__)

OUTPUT_DIR = Path(__file__).parent / "output"

STANCE_LABELS = {
    "critical": "批判的AG",
    "neutral": "中立AG",
    "advocate": "推進的AG",
}
STANCE_ROLES = {
    "critical": "計画どおり進まない前提でリスクを見る",
    "neutral": "実績だけを外挿したベースライン",
    "advocate": "予算の計画情報を織り込む",
}
VERDICT_LABELS = {
    "over_budget": ("予算超過の見込み", "over"),
    "under_consumption": ("大幅な使い残しの見込み", "under"),
    "on_track": ("おおむね計画どおり", "ontrack"),
}


def latest_forecast() -> dict | None:
    """最新の forecast_*.json を読む。無ければ None。"""
    files = sorted(OUTPUT_DIR.glob("forecast_*.json"))
    if not files:
        return None
    data = json.loads(files[-1].read_text(encoding="utf-8"))
    data["_source_file"] = files[-1].name
    data["_available_runs"] = [f.name for f in files]
    return data


def enrich(fc: dict) -> dict:
    """表示に必要な派生値を足す（すべて確定計算・AIは介在しない）。"""
    budget = fc["budget_total"]
    landing = fc.get("final_landing")

    fc["_verdict_label"], fc["_verdict_class"] = VERDICT_LABELS.get(
        fc.get("verdict"), ("判定なし", "ontrack")
    )
    # 予算に対する過不足（+なら超過、−なら使い残し）
    fc["_gap"] = (landing - budget) if isinstance(landing, (int, float)) else None
    # 経過率と消化率の差＝ペースの遅速
    fc["_pace_gap"] = fc["consumption_rate"] - fc["elapsed_rate"]

    for s in fc.get("stances", []):
        s["_label"] = STANCE_LABELS.get(s["stance"], s["stance"])
        s["_role"] = STANCE_ROLES.get(s["stance"], "")
    # 着地見込みの小さい順に並べ、レンジとして読みやすくする
    fc["stances"] = sorted(
        fc.get("stances", []), key=lambda s: s.get("landing_estimate") or 0
    )
    return fc


@app.route("/")
def index():
    fc = latest_forecast()
    return render_template(
        "budget_dashboard.html", fc=enrich(fc) if fc else None
    )


@app.route("/api/forecast")
def api_forecast():
    fc = latest_forecast()
    if not fc:
        return jsonify({"error": "forecast_*.json がまだありません"}), 404
    return jsonify(fc)


if __name__ == "__main__":
    app.run(debug=True, port=5001)
