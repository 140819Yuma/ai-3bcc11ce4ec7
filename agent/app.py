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

# 視点は「性格」ではなく「担当する問い」で分ける。
# 同じ証拠を配ると3体は同じ答えに寄る（同調）ため、見る材料自体を分けてある。
STANCE_LABELS = {
    "actuals": "実績AG",
    "plan": "計画AG",
    "constraint": "制約AG",
}
STANCE_ROLES = {
    "actuals": "このペースが続いたら、いくらか（実績のみ）",
    "plan": "計画どおり実行されたら、いくらか（予算のみ）",
    "constraint": "そもそも執行可能なのは、いくらまでか（費目の性質と期限）",
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


def normalize(fc: dict) -> dict:
    """出力スキーマの違いを表示側で吸収する。

    3視点を「性格」から「担当する問い」へ再設計した際に、調整AIの出力キーも
    変わった（range_low → range.min など）。分析側の語彙を表示の都合で縛りたく
    ないので、変換はここに閉じ込める。テンプレートは常に平坦なキーだけを見る。
    """
    rng = fc.get("range") or {}
    fc.setdefault("range_low", rng.get("min"))
    fc.setdefault("range_high", rng.get("max"))
    fc.setdefault("spent", fc.get("actuals_total"))
    fc.setdefault("disagreement_score", fc.get("disagreement"))
    # 乖離が閾値を超えた＝3視点が同じ絵を見ていない。人が読む必要がある。
    fc.setdefault("escalate_to_human", fc.get("converged") is False)
    fc.setdefault("reasoning", fc.get("adopted_view", ""))

    if "key_findings" not in fc:
        findings = list(fc.get("why_they_split", []))
        ku = fc.get("key_uncertainty")
        if ku:
            findings.append(f"【最大の不確実性】{ku['question']} — {ku['why_it_matters']}")
        findings += [f"【打ち手】{a}" for a in fc.get("actions", [])]
        fc["key_findings"] = findings
    return fc


def enrich(fc: dict) -> dict:
    """表示に必要な派生値を足す（すべて確定計算・AIは介在しない）。"""
    fc = normalize(fc)
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
