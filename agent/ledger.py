"""予測の台帳 — 出した予測を消えない形で積み、あとで答え合わせする。

【なぜ要るのか】
予測を出しっぱなしにすると精度が永久に測れない。モデルは使っても賢くならないので、
このシステムが良くなる経路は「予測と実績を突き合わせ、得た教訓をmdへ書き戻す」
ループだけしかない。台帳はそのループの入口にあたる。

【2つの置き場】
  agent/output/forecast_log.jsonl   全記録。金額を含むので git 管理外（追記のみ）
  agent/forecast_index.md           比率だけの要約。金額を含まないので git 追跡下

後者があるのは、**予測を出した事実と精度の推移だけは引き継ぎたい**ため。
金額は社内に置き、比率は設計とともに持ち歩く。

使い方:
    python3 ledger.py record P3            # 予測を台帳に追記
    python3 ledger.py list                 # 記録一覧
    python3 ledger.py settle P3 2025-06 3210000
                                           # 年度末の確定実績を入れて答え合わせ
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).parent
OUT = BASE / "output"
LOG = OUT / "forecast_log.jsonl"          # 追記のみ。既存行は書き換えない
INDEX = BASE / "forecast_index.md"        # git追跡下（金額を含めない）


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _load_log() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]


def record(pid: str) -> None:
    """forecast_<PID>_<as_of>.json を台帳へ追記する。"""
    files = sorted(OUT.glob(f"forecast_{pid}_*.json"))
    if not files:
        raise SystemExit(f"予測ファイルがありません: forecast_{pid}_*.json")
    fc = json.loads(files[-1].read_text(encoding="utf-8"))

    entry = {
        "recorded_at": _now(),
        "project": fc["project"],
        "as_of": fc["as_of"],
        "budget_total": fc["budget_total"],
        "actuals_total": fc["actuals_total"],
        "elapsed_rate": fc["elapsed_rate"],
        "consumption_rate": fc["consumption_rate"],
        "final_landing": fc["final_landing"],
        "landing_rate": round(fc["final_landing"] / fc["budget_total"], 4),
        "verdict": fc["verdict"],
        "confidence": fc.get("confidence"),
        "disagreement": fc.get("disagreement"),
        "stances": {s["stance"]: s["landing_estimate"] for s in fc.get("stances", [])},
        "source": files[-1].name,
        "actual_landing": None,       # settle で埋める
        "error_rate": None,
    }

    # 同じ (project, as_of) の予測が既にあっても上書きしない。
    # 設計を変えるたびに予測は変わるので、その履歴自体が検証の材料になる。
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    n = sum(1 for e in _load_log() if e["project"] == pid and e["as_of"] == fc["as_of"])
    print(f"台帳へ追記: {pid} / {fc['as_of']} / 着地 {fc['final_landing']:,}円 "
          f"({entry['landing_rate']:.1%})  この時点の予測は通算{n}件目")
    write_index()


def settle(pid: str, as_of: str, actual_landing: int) -> None:
    """年度末の確定実績を入れて、その時点の予測すべてを採点する。

    比較の基準は **季節ナイーブ**（経過率どおりに使うと仮定した外挿）とする。
    これに勝てないなら、3体を並べる意味は無い。
    """
    log = _load_log()
    hits = [e for e in log if e["project"] == pid and e["as_of"] == as_of]
    if not hits:
        raise SystemExit(f"該当する予測がありません: {pid} / {as_of}")

    print(f"=== 答え合わせ  {pid} / {as_of} 時点の予測 ===")
    print(f"確定実績: {actual_landing:,}円\n")

    for e in log:
        if e["project"] == pid and e["as_of"] == as_of:
            e["actual_landing"] = actual_landing
            e["error_rate"] = round(abs(e["final_landing"] - actual_landing) / actual_landing, 4)

    for i, e in enumerate(hits, 1):
        # 季節ナイーブ: 実績累計 ÷ 経過率。「今のペースがそのまま続く」だけの予測。
        naive = e["actuals_total"] / e["elapsed_rate"] if e["elapsed_rate"] else 0
        naive_err = abs(naive - actual_landing) / actual_landing
        err = e["error_rate"]
        judge = "✅ 勝ち" if err < naive_err else ("△ 引き分け" if abs(err - naive_err) < 0.01 else "❌ 負け")
        print(f"[{i}] {e['recorded_at']}  統合予測 {e['final_landing']:>10,}円  誤差 {err:>6.1%}")
        print(f"     季節ナイーブ     {naive:>10,.0f}円  誤差 {naive_err:>6.1%}   → {judge}")
        for st, v in e.get("stances", {}).items():
            print(f"       {st:<11} {v:>10,}円  誤差 {abs(v-actual_landing)/actual_landing:>6.1%}")
        print()

    LOG.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in log),
                   encoding="utf-8")
    write_index()
    print("台帳を更新した。誤差の原因を特定したら、必ず該当のmdファイルへ書き戻すこと。")


def write_index() -> None:
    """金額を含まない要約を書き出す（git追跡下）。

    比率だけなら予算額は復元できない。予測を出した事実と精度の推移は
    設計とともに引き継ぎたいので、ここだけは持ち歩けるようにしておく。
    """
    log = _load_log()
    lines = [
        "# 予測の記録",
        "",
        "`ledger.py` が自動生成する。**金額は含めない**（比率のみ）。",
        "実額は `agent/output/forecast_log.jsonl` にあり、そちらは社内にのみ保持する。",
        "",
        "| 記録日時 | 案件 | 時点 | 経過率 | 消化率 | 着地(予算比) | 判定 | 割れ | 誤差 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for e in log:
        err = f"{e['error_rate']:.1%}" if e.get("error_rate") is not None else "—"
        dis = f"{e['disagreement']:.2f}" if e.get("disagreement") is not None else "—"
        lines.append(
            f"| {e['recorded_at']} | {e['project']} | {e['as_of']} | "
            f"{e['elapsed_rate']:.0%} | {e['consumption_rate']:.0%} | "
            f"{e['landing_rate']:.1%} | {e['verdict']} | {dis} | {err} |"
        )
    settled = [e for e in log if e.get("error_rate") is not None]
    lines += ["", f"記録 {len(log)}件 ／ 答え合わせ済み {len(settled)}件"]
    if settled:
        avg = sum(e["error_rate"] for e in settled) / len(settled)
        lines.append(f"／ 平均誤差 {avg:.1%}")
    else:
        lines.append("")
        lines.append("> ⚠️ **まだ1件も答え合わせをしていない。現時点で精度の裏づけは無い。**")
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")


def show() -> None:
    log = _load_log()
    if not log:
        print("記録がまだない。")
        return
    print(f"=== 予測の記録（{len(log)}件）===\n")
    for e in log:
        err = f" ／ 誤差 {e['error_rate']:.1%}" if e.get("error_rate") is not None else ""
        print(f"  {e['recorded_at']}  {e['project']} {e['as_of']}  "
              f"着地 {e['final_landing']:>10,}円 ({e['landing_rate']:.1%})  "
              f"{e['verdict']}{err}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "record":
        record(sys.argv[2])
    elif cmd == "settle":
        settle(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    elif cmd == "list":
        show()
    else:
        raise SystemExit(f"不明なコマンド: {cmd}")
