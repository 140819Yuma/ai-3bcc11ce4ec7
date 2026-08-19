"""門番 — 社外に出す前に、個人や組織を特定できる情報を落とす。

【なぜPythonなのか】
LLMにマスキングさせると、マスク対象の生データをマスクする前にLLM（＝社外API）へ
送ることになり、本末転倒になる。加えてLLMの処理は確率的なので一定確率で漏れる。
門番は決定論的でなければならない。ここではLLMを一切呼ばない。

【方針（2026-08-18決定）】
個人単位の情報は「集約して落とす」。氏名も日単価も構造的に存在しない形にする。
少人数組織では役職と個人が1対1に対応するため、役割ラベルでは匿名化にならない。

使い方:
    python3 masking.py                    # data/経費/*.csv → agent/masked/
    python3 masking.py --dry-run          # 書き出さずに監査結果だけ表示
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent
RAW_DIR = BASE.parent / "data" / "経費"
OUT_DIR = BASE / "masked"

# --- マスキング規則 -------------------------------------------------------

# 丸ごと落とす列。自由記述や取引先を含みうるため、部分マスクではなく削除する。
DROP_COLUMNS = {
    "メモ", "タグ",                      # 自由記述。何が書かれるか予測できない
    "借方補助科目", "貸方補助科目",        # 取引先名が入りうる
    "借方取引先", "貸方取引先",            # 定義上の機密（今回は全行空欄だが規則として残す）
    "借方インボイス", "貸方インボイス",     # 事業者登録番号は事業者を特定する
    "借方税区分", "貸方税区分",            # 分析に不要
    "取引No",                            # 社内の連番。外部に出す意味がない
}

# 残す列（これだけが社外に出る）
KEEP = {
    "取引日": "取引日",
    "借方勘定科目": "勘定科目",
    "借方金額(円)": "金額",
}

# --- 摘要のマスキングは「ホワイトリスト方式」 -----------------------------
#
# 【2026-08-18 方式変更】当初はブロックリスト（法人格を含む語を伏字化）だったが、
# 実データで検証したところ2種類の漏れが判明した：
#   ① 個人名 — 仕訳帳の摘要には精算相手や出席者の氏名が入る。想定していなかった
#   ② ブランド名 — 鉄道会社やコインパーキングの通称は法人格を含まないため素通り
# 固有名詞を列挙して塞ぐ方式は原理的に漏れる。よって逆にして、
# 「業務語として明示的に許可した語だけを通し、それ以外は全て伏字」にする。
# 過剰に伏字化されるが、緩く始めて後で締めるのは不可逆なので厳しい側から始める。

# 通してよい語（会計・業務用語）。これ以外の漢字・カタカナ列は伏字化される。
ALLOW_WORDS = set("""
旅費交通費 会議費 業務委託費 広告宣伝費 支払手数料 備品 消耗品費 消耗品 租税公課 通信費
未払金 普通預金 現金 立替金 仮払金 前払費用 雑費 新聞図書費 会議 打合 打合せ 会場 会場費
交通費 宿泊費 宿泊 移動 移動費 出張 参加 参加費 開催 実施 運営 準備 委託 外注 制作 修正
運用 保守 購入 発注 支払 振込 手数料 月分 月次 年額 年間 契約 更新 利用料 使用料 登録料
広告 宣伝 印刷 郵送 送料 資料 資材 消耗 事務 事務用品 備品購入 セミナー ワークショップ
座談会 交流会 説明会 相談 面談 訪問 視察 講師 謝金 報酬 支援 支援金 検証 資金 助成
学生 起業 創業 事業 予算 実績 経費 精算 領収 請求 見積 納品 第一回 第二回 第三回
サーバー システム ソフト ライセンス ドメイン デザイン サイト ウェブ ページ 更新料
""".split())

# 常に伏字化する語（自社名・取引先名・従業員名など）。
# 実運用では agent/masked/redact_words.txt に1行1語で置く（gitignore対象）。
# ホワイトリストで大半は塞がるが、確実に落としたい語をここで明示できる。
EXTRA_WORDS_FILE = OUT_DIR / "redact_words.txt"

# 固有名詞になりうる文字列（漢字・カタカナ・英字の連なり）
TOKEN_PATTERN = re.compile(r"[一-龥]{2,}|[ァ-ヶー]{3,}|[A-Za-z]{3,}")


def detect_encoding(path: Path) -> str:
    """会計ソフトの出力は cp932 が多い。UTF-8 と両方試す。"""
    for enc in ("utf-8-sig", "cp932", "shift_jis"):
        try:
            path.read_text(encoding=enc)
            return enc
        except UnicodeDecodeError:
            continue
    raise ValueError(f"文字コードを判定できません: {path.name}")


def load_extra_words() -> list[str]:
    if not EXTRA_WORDS_FILE.exists():
        return []
    words = [w.strip() for w in EXTRA_WORDS_FILE.read_text(encoding="utf-8").splitlines()]
    # 長い語から先に置換しないと部分一致で崩れる
    return sorted([w for w in words if w and not w.startswith("#")], key=len, reverse=True)


def _fully_allowed(tok: str) -> bool:
    """語が許可語だけで完全に分解できるか判定する（前から貪欲ではなく全探索）。"""
    n = len(tok)
    reachable = [False] * (n + 1)
    reachable[0] = True
    for i in range(n):
        if not reachable[i]:
            continue
        for j in range(i + 1, n + 1):
            if tok[i:j] in ALLOW_WORDS:
                reachable[j] = True
    return reachable[n]


def mask_text(text: str, extra: list[str], audit: Counter) -> str:
    """摘要をホワイトリスト方式でマスクする。

    許可語（ALLOW_WORDS）に完全一致しない漢字・カタカナ・英字の連なりは、
    人名か組織名か地名か判別できないものとして一律に伏字化する。
    数字・日付・助詞は残るため「[伏字] 交通費 4月分」のような形で文脈は保たれる。
    """
    if not text:
        return ""
    out = text

    # 明示指定された語（従業員名・取引先名など）を先に処理
    for w in extra:
        if w in out:
            out = out.replace(w, "［伏字］")
            audit["指定語を伏字化"] += 1

    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        # 許可語だけで完全に分解できる語のみ通す。
        # 「旅費交通費」＝旅費+交通費 は通るが、「〈地名〉交通費」は地名が未許可なので通さない。
        # 部分一致で通すと、許可語を含んだ固有名詞（例:「◯◯会議費」）が丸ごと漏れる。
        if _fully_allowed(tok):
            return tok
        audit["未許可語を伏字化"] += 1
        return "［伏字］"

    out = TOKEN_PATTERN.sub(_sub, out)
    out = re.sub(r"(［伏字］[\s　]*){2,}", "［伏字］", out)   # 連続した伏字をまとめる
    return out.strip()


def mask_file(path: Path, project_id: str, extra: list[str]) -> tuple[list[dict], dict]:
    enc = detect_encoding(path)
    rows = list(csv.DictReader(path.open(encoding=enc)))
    audit: Counter = Counter()
    dropped = sorted({c for c in (rows[0].keys() if rows else []) if c in DROP_COLUMNS})

    masked = []
    for r in rows:
        rec = {new: (r.get(old) or "").strip() for old, new in KEEP.items()}
        rec["金額"] = re.sub(r"[,\s円]", "", rec["金額"])
        rec["摘要"] = mask_text((r.get("摘要") or "").strip(), extra, audit)
        rec["プロジェクト"] = project_id       # 部門名は出さず、不透明なIDに置換
        masked.append(rec)
        audit["行"] += 1

    return masked, {
        "元ファイル": path.name,
        "文字コード": enc,
        "プロジェクトID": project_id,
        "行数": len(masked),
        "削除した列": dropped,
        "部門名": "不透明なIDに置換",
        "伏字化": {k: v for k, v in audit.items() if k != "行"},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="書き出さず監査結果だけ表示")
    args = ap.parse_args()

    files = sorted(RAW_DIR.glob("*.csv"))
    if not files:
        raise SystemExit(f"生データが見つかりません: {RAW_DIR}")

    extra = load_extra_words()
    OUT_DIR.mkdir(exist_ok=True)
    audits, mapping = [], {}

    for i, path in enumerate(files, 1):
        pid = f"P{i}"
        mapping[pid] = path.name            # 対応表は社内にのみ残す
        masked, audit = mask_file(path, pid, extra)
        audits.append(audit)

        if not args.dry_run:
            out = OUT_DIR / f"{pid}_journal.csv"
            with out.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["取引日", "勘定科目", "金額", "摘要", "プロジェクト"])
                w.writeheader()
                w.writerows(masked)

    print("=== 門番の監査結果 ===")
    for a in audits:
        print(f"\n[{a['プロジェクトID']}] {a['元ファイル']}  ({a['文字コード']}, {a['行数']}行)")
        print(f"  削除した列 : {'、'.join(a['削除した列']) or 'なし'}")
        print(f"  部門名     : {a['部門名']}")
        print(f"  伏字化     : {a['伏字化'] or 'なし'}")

    if not args.dry_run:
        (OUT_DIR / "mask_audit.json").write_text(
            json.dumps({"監査": audits, "対応表": mapping}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n出力先: {OUT_DIR}/  （対応表とあわせて社内にのみ保持する）")


if __name__ == "__main__":
    main()
