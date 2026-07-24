import json
import re
import statistics
from agents import groq_agent

MODEL = "llama-3.3-70b-versatile"
DISAGREEMENT_THRESHOLD = 0.3  # (max-min)/中央値 がこれを超えたら人間へエスカレーション

STANCE_LABELS = {"critical": "批判的AG", "neutral": "中立AG", "advocate": "推進的AG"}

PROMPT_TEMPLATE = """あなたは財務予測の調整AI（司令塔）です。「{category}」の翌月（{target_month}）の予測について、
批判的AG・中立AG・推進的AGの3体が互いに議論せず独立に出した予測と根拠が集まりました。

{stance_summaries}

これら3つの予測・根拠を比較し、対立点を踏まえたうえで最終予測を1つにまとめてください。

以下のJSON形式のみで出力してください（他のテキストは含めない）。
reasoningの文中で「」などの全角記号は使わず、必ずダブルクォート(")で文字列を閉じてください:
{{"category": "{category}", "target_month": "{target_month}", "final_amount": 数値, "range_low": 数値, "range_high": 数値, "reasoning": "3体の対立点を踏まえた統合根拠を2〜3文で"}}
"""


def _summarize_stances(stance_results: list[dict]) -> str:
    lines = []
    for r in stance_results:
        label = STANCE_LABELS.get(r.get("stance"), r.get("stance"))
        lines.append(
            f"- {label}: {r.get('forecast_amount'):,}円（確信度: {r.get('confidence')}） "
            f"根拠: {r.get('reasoning')}"
        )
    return "\n".join(lines)


def disagreement_score(stance_results: list[dict]):
    """3体の予測金額のばらつきを (最大-最小)/中央値 で算出。値がなければNone。"""
    amounts = [r["forecast_amount"] for r in stance_results if isinstance(r.get("forecast_amount"), (int, float))]
    if len(amounts) < 2:
        return None
    return (max(amounts) - min(amounts)) / statistics.median(amounts)


def arbitrate(category: str, target_month: str, stance_results: list[dict]) -> dict:
    score = disagreement_score(stance_results)
    escalate = score is not None and score > DISAGREEMENT_THRESHOLD

    prompt = PROMPT_TEMPLATE.format(
        category=category,
        target_month=target_month,
        stance_summaries=_summarize_stances(stance_results),
    )
    for attempt in range(2):
        raw = groq_agent.run(prompt, model=MODEL)
        result = _parse(raw, category, target_month)
        if result.get("final_amount") is not None:
            break

    result["disagreement_score"] = round(score, 3) if score is not None else None
    result["escalate_to_human"] = escalate
    return result


def _parse(raw: str, category: str, target_month: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "category": category,
            "target_month": target_month,
            "final_amount": None,
            "range_low": None,
            "range_high": None,
            "reasoning": raw,
        }
