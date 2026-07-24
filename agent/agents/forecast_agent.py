import json
import re
from agents import groq_agent

MODEL = "llama-3.3-70b-versatile"

STANCES = {
    "critical": (
        "批判的AG",
        "楽観的な見積もりを疑ってください。過去の予測外れ・突発的な支出増加・季節性の実績を重視し、"
        "「悪い場合どこまで膨らみうるか」という下振れ（支出が予想より増える）リスクを重視して予測してください。"
        "保守的にやや高めの金額になってもかまいません。",
    ),
    "neutral": (
        "中立AG",
        "楽観・悲観どちらにも偏らず、過去の実績から読み取れるトレンドと季節性だけを根拠に、"
        "統計的に妥当なベースライン予測を出してください。",
    ),
    "advocate": (
        "推進的AG",
        "今後の成長・拡大要因（新規案件・増員・活動量の増加など、実績データから読み取れる上向きの兆し）を積極的に織り込み、"
        "実績が上振れる（増える）可能性を重視して予測してください。楽観的な見積もりになってもかまいません。",
    ),
}

PROMPT_TEMPLATE = """あなたは経費予測を担当する{stance_name}です。{stance_instruction}

以下は「{category}」の過去の月次実績です。

{history}

この実績をもとに、翌月（{target_month}）の金額を予測してください。

以下のJSON形式のみで出力してください（他のテキストは含めない）。
reasoningの文中で「」などの全角記号は使わず、必ずダブルクォート(")で文字列を閉じてください:
{{"stance": "{stance_key}", "category": "{category}", "target_month": "{target_month}", "forecast_amount": 数値, "confidence": "high/medium/low", "reasoning": "根拠を1〜2文で"}}
"""


def _format_history(rows: list[tuple[str, int]]) -> str:
    return "\n".join(f"- {month}: {amount:,}円" for month, amount in rows)


def forecast(category: str, history: list[tuple[str, int]], target_month: str, stance: str) -> dict:
    stance_name, stance_instruction = STANCES[stance]
    prompt = PROMPT_TEMPLATE.format(
        stance_key=stance,
        stance_name=stance_name,
        stance_instruction=stance_instruction,
        category=category,
        history=_format_history(history),
        target_month=target_month,
    )
    for attempt in range(2):  # 稀にJSONが崩れることがあるため1回だけ再試行
        raw = groq_agent.run(prompt, model=MODEL)
        parsed = _parse(raw, stance, category, target_month)
        if parsed["confidence"] != "parse_error":
            return parsed
    return parsed


def _parse(raw: str, stance: str, category: str, target_month: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    # LLMが前後に余分な文字（全角記号など）を混ぜることがあるため、
    # 最初の { から対応する最後の } までを抜き出してからパースする。
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "stance": stance,
            "category": category,
            "target_month": target_month,
            "forecast_amount": None,
            "confidence": "parse_error",
            "reasoning": raw,
        }
