from dotenv import load_dotenv
load_dotenv()

from orchestrator import route

if __name__ == "__main__":
    print("=== AIエージェント プロトタイプ ===")
    print("タスクを入力してください（終了: quit）\n")

    while True:
        task = input("タスク> ").strip()
        if task.lower() in ("quit", "exit", "q"):
            break
        if not task:
            continue

        print()
        result = route(task)
        print(f"\n【結果】\n{result}\n")
        print("-" * 50)
