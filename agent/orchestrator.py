from agents import groq_agent

DRAFT_MODEL = "llama-3.3-70b-versatile"
REVIEW_MODEL = "llama-3.1-8b-instant"


def route(task: str) -> str:
    """タスクの種類に応じてエージェントを振り分けるPython司令官"""

    if any(k in task for k in ["要約", "まとめ", "整理"]):
        return _summarize(task)

    if any(k in task for k in ["調べ", "リサーチ", "検索"]):
        return _research(task)

    if any(k in task for k in ["評価", "チェック", "レビュー"]):
        return _review(task)

    return _reflection(task)


def _summarize(task: str) -> str:
    print(f"[司令官] 要約タスク → Groq ({DRAFT_MODEL})")
    return groq_agent.run(f"以下を簡潔に要約してください:\n\n{task}")


def _research(task: str) -> str:
    print(f"[司令官] リサーチタスク → Groq 70B（下書き）→ Groq 8B（整理）")
    draft = groq_agent.run(f"次のテーマについて詳しく調べて説明してください:\n\n{task}")
    return groq_agent.run(f"以下の情報を読みやすく整理してください:\n\n{draft}", model=REVIEW_MODEL)


def _review(task: str) -> str:
    print(f"[司令官] レビュータスク → Groq ({REVIEW_MODEL})")
    return groq_agent.run(f"以下を評価・改善点を指摘してください:\n\n{task}", model=REVIEW_MODEL)


def _reflection(task: str, max_iter: int = 3) -> str:
    print("[司令官] リフレクションタスク開始")
    draft = groq_agent.run(f"次のタスクに回答してください:\n\n{task}")

    for i in range(max_iter):
        print(f"  └ 反省ループ {i + 1}/{max_iter}")
        feedback = groq_agent.run(
            f"以下の回答を評価してください。十分な品質なら「OK」とだけ答えてください。\n\n"
            f"【タスク】{task}\n【回答】{draft}",
            model=REVIEW_MODEL,
        )
        if feedback.strip().startswith("OK"):
            print(f"  └ 品質OK（{i + 1}回目で完了）")
            break
        draft = groq_agent.run(
            f"以下のフィードバックを踏まえて回答を改善してください。\n\n"
            f"【元の回答】{draft}\n【フィードバック】{feedback}",
        )

    return draft
