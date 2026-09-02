"""見積書PDFの門番 — 抽出はローカル、構造化はあとでLLM。

【なぜこの分業なのか】
「PDFは門番を通せない」と一度結論したが、それは不正確だった。
Pythonが苦手なのは *表の意味を理解して構造化すること* であって、
*テキストを取り出すこと* ではない。前者だけをLLMに任せればよい。

    ① pdftotext        PDFからテキストを取り出す（ローカル）
    ② このファイル      氏名を伏字化し、単価と数量を落とす（ローカル）
    ③ LLM              意味を読んで項目に整理する（ここで初めて境界を越える）

Claude に直接PDFを読ませてはいけない。5件すべてに「単価」が含まれており、
見積書の日単価と人件費行の氏名が、門番を通す前にAPIへ渡ることになる。

【数字の扱いが仕訳帳と違う理由】
仕訳帳は列が固定なので「この列を捨てる」で済んだ。PDFのテキストは自由
レイアウトなので同じ手が使えない。金額は残したいが単価は落としたい——
どちらも数字で、見た目では区別できない。

そこで見積書の行が持つ `単価 × 数量 = 金額` という関係を使う。行内の数値から
掛け算関係にある組を探し、**掛けられる側（単価・数量）を落として結果（金額）を
残す**。書式に依存せず決定論的に判定できる。関係が見つからない行は
「どれが金額か分からない行」なので、最大値だけ残して他は落とす（安全側）。

使い方:
    python3 pdf_extract.py            # data/00_インキュ決算データ/*.pdf → agent/masked/
    python3 pdf_extract.py --dry-run  # 書き出さず監査結果だけ表示
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import unicodedata
from collections import Counter
from pathlib import Path

from masking import ALLOW_WORDS, TOKEN_PATTERN, _fully_allowed, load_extra_words

BASE = Path(__file__).parent
PDF_DIR = BASE.parent / "data" / "00_インキュ決算データ"
OUT_DIR = BASE / "masked"

# 予算PDFと仕訳帳（P1〜P3）の対応表。
#
# 対応づけの手がかりはファイル名に含まれる取引先名や案件名なので、**コードに
# 直接書くと公開リポジトリに取引先名が載る**。ここには置かず、gitignore された
# ローカルファイルから読む。書式は1行1件の「手がかり=ID」。
#
#     agent/masked/pid_hints.txt
#     ─────────────────────
#     ◯◯財団=P3
#     △△大学=P2
#
# 用意されていなければ全ファイルが B4/B5… になるだけで、処理は止まらない。
PID_HINTS_FILE = OUT_DIR / "pid_hints.txt"


def load_pid_hints() -> dict[str, str]:
    if not PID_HINTS_FILE.exists():
        return {}
    hints = {}
    for line in PID_HINTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            hints[unicodedata.normalize("NFC", k.strip())] = v.strip()
    return hints

# 金額とみなす下限。日付や項番（1, 2, 12ヶ月…）を金額と誤認しないため。
MIN_AMOUNT = 1000
# 掛け算の一致許容幅。見積書は端数処理が入るため完全一致は求めない。
MUL_TOLERANCE = 0.005

NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")

# 伏字の文字。**元の語と同じ文字数に置き換える**のが要点。
#
# pdftotext -layout は空白で列位置を表現しているため、可変長の「［伏字］」に
# 置き換えると桁が崩れ、どの数値が単価列でどれが金額列かを後段が判別できなくなる。
# 1文字ずつ塗りつぶせば、隠しながら表の形を保てる。
BLOCK = "■"


def redact(tok: str) -> str:
    return BLOCK * len(tok)

# この語がある行の数値は、掛け算関係が無くても単価とみなして落とす。
#
# 掛け算関係だけに頼ると、表ではなく **文章中に単独で書かれた単価** が素通りする。
# 「日単価は46,800円です」のような1行は、掛ける相手が同じ行に無いため関係が
# 成立せず、数字が1つしかない行として無検査で通ってしまう。提案書や覚書の
# ように散文が主体のPDFでは、これが主要な漏洩経路になる。
#
# 対象は **個人の労働単価に結びつく語** に限る。「月額」「年額」は契約の形態を
# 表すだけで個人単価ではなく、これを含めるとサブスクの年額など普通の金額まで
# 落ちてしまう。人の報酬が月額・年額で書かれている場合は、同じ行にある
# 「人件費」「報酬」等が捕まえるので取りこぼさない。
DANGER_CONTEXT = ("単価", "日額", "日給", "時給", "人日", "人月",
                  "人件費", "謝金", "報酬", "給与", "賃金", "工数")


def extract_text(path: Path) -> str:
    """pdftotext でテキスト層を取り出す。-layout で表の並びを保つ。"""
    r = subprocess.run(
        ["pdftotext", "-layout", str(path), "-"],
        capture_output=True, check=True,
    )
    return r.stdout.decode("utf-8", errors="replace")


def _to_num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def mask_numbers(line: str, audit: Counter) -> str:
    """単価・数量を落とし、金額を残す。

    掛け算関係 a×b≒c を満たす a,b を落として c を残す。関係が見つからない行は
    「どれが金額か判定できない行」として、最大値のみ残して他を落とす。
    """
    danger = any(w in line for w in DANGER_CONTEXT)

    matches = list(NUMBER_RE.finditer(line))
    vals = [(m, _to_num(m.group())) for m in matches]
    vals = [(m, v) for m, v in vals if v is not None]

    if len(vals) < 2:
        # 単価を示す語がある行に数値が1つだけ → その数値が単価そのもの。落とす。
        if danger and vals and vals[0][1] >= MIN_AMOUNT:
            m = vals[0][0]
            audit["数値を伏字化"] += 1
            audit["単価語のある行で伏字化"] += 1
            return line[:m.start()] + redact(m.group()) + line[m.end():]
        return line                                   # それ以外は判定不要

    keep: set[int] = set()      # 残す数値の開始位置
    drop: set[int] = set()      # 落とす数値の開始位置

    # ① 掛け算関係を探す
    for i, (mi, a) in enumerate(vals):
        for j, (mj, b) in enumerate(vals):
            if i == j or a == 0 or b == 0:
                continue
            for k, (mk, c) in enumerate(vals):
                if k in (i, j) or c < MIN_AMOUNT:
                    continue
                if abs(a * b - c) <= max(1.0, c * MUL_TOLERANCE):
                    keep.add(mk.start())
                    drop.add(mi.start())
                    drop.add(mj.start())
                    audit["掛け算関係を検出した行"] += 1

    if keep:
        # 掛け算の「掛けられる側」でも、落とすのは MIN_AMOUNT 以上のものだけにする。
        #
        # 機密なのは単価であって数量ではない。「0.5日/月 × 12か月」の 0.5 や 12 は
        # 誰の情報でもなく、むしろ制約AGが「経過した月の分は後から積めない」と
        # 判断するのに要る中核データで、これを落とすと按分の分母が消える。
        # 人の日単価は必ず千円以上なので、この閾値で単価と数量を分けられる。
        for m, v in vals:
            if m.start() not in keep and v >= MIN_AMOUNT:
                drop.add(m.start())
    else:
        # ② 関係なし → 最大値だけ残す（合計行とみなす）
        biggest = max(vals, key=lambda t: t[1])[0].start()
        for m, v in vals:
            if m.start() != biggest and v >= MIN_AMOUNT:
                drop.add(m.start())
        audit["掛け算関係が無く最大値のみ残した行"] += 1
        if danger:
            # 単価語のある行では最大値も信用しない。合計かもしれないが単価かもしれず、
            # 区別できない以上は落とす側に倒す。
            for m, v in vals:
                if v >= MIN_AMOUNT:
                    drop.add(m.start())
            audit["単価語のある行で伏字化"] += 1

    # 単価語のある行で意図的に残した数値を数えておく。
    # これらは掛け算で「積」と確認できた金額であり、単価ではない。検査側は
    # 行だけを見ても両者を区別できないため、門番の側から件数を申告する。
    if danger:
        audit["単価語のある行に残した積"] += sum(
            1 for m, v in vals if m.start() not in drop and v >= MIN_AMOUNT
        )

    if not drop:
        return line

    out, cursor = [], 0
    for m, _ in vals:
        if m.start() in drop:
            out.append(line[cursor:m.start()])
            out.append(redact(m.group()))
            cursor = m.end()
            audit["数値を伏字化"] += 1
    out.append(line[cursor:])
    return "".join(out)


def mask_words(line: str, extra: list[str], audit: Counter) -> str:
    """仕訳帳と同じホワイトリスト方式。許可語だけを通す。"""
    out = line
    for w in extra:
        if w in out:
            out = out.replace(w, redact(w))
            audit["指定語を伏字化"] += 1

    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        if _fully_allowed(tok):
            return tok
        audit["未許可語を伏字化"] += 1
        return redact(tok)

    return TOKEN_PATTERN.sub(_sub, out)


def mask_pdf(path: Path, extra: list[str]) -> tuple[str, dict]:
    text = extract_text(path)
    audit: Counter = Counter()
    lines_out = []
    for line in text.splitlines():
        if not line.strip():
            lines_out.append("")
            continue
        audit["行"] += 1
        masked = mask_words(line, extra, audit)
        masked = mask_numbers(masked, audit)
        # 連続した伏字はまとめない。まとめると桁が詰まり、列位置が崩れる
        lines_out.append(masked.rstrip())

    out_text = "\n".join(lines_out)
    残存数値 = len(NUMBER_RE.findall(out_text))
    元数値 = len(NUMBER_RE.findall(text))

    return out_text, {
        "元ファイル": path.name,
        "行数": audit["行"],
        "元の数値": 元数値,
        "残した数値": 残存数値,
        "落とした数値": 元数値 - 残存数値,
        "掛け算で判定できた行": audit["掛け算関係を検出した行"],
        "最大値のみ残した行": audit["掛け算関係が無く最大値のみ残した行"],
        "単価語のある行で伏字化": audit["単価語のある行で伏字化"],
        "単価語のある行に残した積": audit["単価語のある行に残した積"],
        "未許可語を伏字化": audit["未許可語を伏字化"],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(PDF_DIR.glob("*.pdf"))
    if not files:
        raise SystemExit(f"PDFが見つかりません: {PDF_DIR}")

    extra = load_extra_words()
    pid_hints = load_pid_hints()
    OUT_DIR.mkdir(exist_ok=True)
    audits, mapping, spare = [], {}, 4

    print("=== 見積書PDFの門番 ===\n")
    for path in files:
        # macOS はファイル名を分解形（NFD）で保持する。「だ」が「た」＋濁点として
        # 格納されるため、合成形（NFC）で書いたキーと素朴に比較すると一致しない。
        name = unicodedata.normalize("NFC", path.name)
        pid = next((v for k, v in pid_hints.items() if k in name), None)
        if pid is None:
            pid, spare = f"B{spare}", spare + 1
        mapping[pid] = name

        out_text, audit = mask_pdf(path, extra)
        audit["ID"] = pid
        audits.append(audit)

        if not args.dry_run:
            (OUT_DIR / f"{pid}_budget_raw.txt").write_text(out_text, encoding="utf-8")

        print(f"[{pid}] {path.name}")
        print(f"     {audit['行数']}行 ／ 数値 {audit['元の数値']} → 残 {audit['残した数値']}"
              f"（{audit['落とした数値']}件を伏字化）")
        print(f"     掛け算で判定 {audit['掛け算で判定できた行']}行 ／"
              f" 最大値のみ残した {audit['最大値のみ残した行']}行 ／"
              f" 語の伏字化 {audit['未許可語を伏字化']}件\n")

    if not args.dry_run:
        (OUT_DIR / "pdf_mask_audit.json").write_text(
            json.dumps({"監査": audits, "対応表": mapping}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"出力先: {OUT_DIR}/  （対応表は社内にのみ保持する）")
        print("※ B4以降は仕訳帳との対応が未確定。人間が確認すること")


if __name__ == "__main__":
    main()
