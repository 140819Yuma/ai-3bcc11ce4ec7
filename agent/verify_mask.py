"""門番の出力を、中身を見ずに検査する。

【なぜ必要か】
マスク結果が安全かを確かめるのに人間やLLMが本文を読むと、
漏れていた場合に「確認のために漏らす」ことになる。検査は集計値だけを返す。

判定する観点:
  ① 未許可語が残っていないか        — ホワイトリストの取りこぼし
  ② 単価らしき数値が残っていないか  — 単価語のある行に生の金額がある
  ③ 敬称の直前が伏字になっているか  — 「様」「御中」の前は氏名か組織名
  ④ 分析に必要な情報が残っているか  — 全部伏字にすれば「安全」だが無意味

使い方:
    python3 verify_mask.py                    # masked/ 内の全ファイル
    python3 verify_mask.py P3_budget_raw.txt  # 個別
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from masking import TOKEN_PATTERN, _fully_allowed
from pdf_extract import BLOCK, DANGER_CONTEXT, MIN_AMOUNT, NUMBER_RE, _to_num

OUT_DIR = Path(__file__).parent / "masked"
AUDIT_FILE = OUT_DIR / "pdf_mask_audit.json"


def declared_products(path: Path) -> int:
    """門番が「単価語のある行に意図的に残した積」として申告した件数。

    申告が無ければ0を返す＝検査は厳しい側に倒れる。門番を通していないファイルを
    甘く判定してしまわないため、欠損を「問題なし」とみなさない。
    """
    if not AUDIT_FILE.exists():
        return 0
    pid = path.name.split("_")[0]
    data = json.loads(AUDIT_FILE.read_text(encoding="utf-8"))
    for a in data.get("監査", []):
        if a.get("ID") == pid:
            return int(a.get("単価語のある行に残した積", 0))
    return 0


def verify(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # ① 許可語で分解できないトークンが残っていないか（伏字マーカーは除く）
    leaked_tokens = 0
    for m in TOKEN_PATTERN.finditer(text.replace(BLOCK, " ")):
        if not _fully_allowed(m.group(0)):
            leaked_tokens += 1

    # ② 単価語のある行に MIN_AMOUNT 以上の生数値が残っていないか
    #
    #    ただし、掛け算で「積」と確認できた金額は意図的に残している（単価と数量は
    #    落ちている）。行を見るだけでは積と単価を区別できないので、門番が申告した
    #    件数を差し引く。申告より多く残っていれば、それは説明のつかない数値。
    danger_lines, danger_numbers = 0, 0
    for line in lines:
        if not any(w in line for w in DANGER_CONTEXT):
            continue
        nums = [v for v in (_to_num(x) for x in NUMBER_RE.findall(line))
                if v is not None and v >= MIN_AMOUNT]
        if nums:
            danger_lines += 1
            danger_numbers += len(nums)
    danger_numbers = max(0, danger_numbers - declared_products(path))

    # ③ 敬称の直前が伏字になっているか
    #
    #    ①はホワイトリストの取りこぼしを見るが、**許可語だけで構成された固有名詞**
    #    は素通りする（「起業」「支援」がどちらも許可語なら、それを並べた組織名は
    #    分解できてしまう）。①では原理的に捕まえられない穴なので、別の角度から見る。
    #    敬称の直前は氏名か組織名と決まっているため、そこが伏字でなければ漏れている。
    honorific = 0
    for m in re.finditer(r"(.{0,6})(様|御中|殿|先生)", text):
        before = m.group(1).rstrip()
        if before and not before.endswith(BLOCK) and re.search(r"[一-龥ァ-ヶA-Za-z]", before):
            honorific += 1

    # ④ 残っている情報量（全部伏字なら安全だが使えない）
    amounts = [v for v in (_to_num(x) for x in NUMBER_RE.findall(text))
               if v is not None and v >= MIN_AMOUNT]

    return {
        "ファイル": path.name,
        "行数": len(lines),
        "①未許可語の残存": leaked_tokens,
        "②単価語の行に残る数値": danger_numbers,
        "②該当行数": danger_lines,
        "③敬称の前が生のまま": honorific,
        "④残った金額の件数": len(amounts),
        "④最大額": int(max(amounts)) if amounts else 0,
        "伏字マーカー数": text.count(BLOCK),
    }


def main() -> None:
    targets = ([OUT_DIR / a for a in sys.argv[1:]] if len(sys.argv) > 1
               else sorted(OUT_DIR.glob("*_budget_raw.txt")))
    if not targets:
        raise SystemExit("検査対象がありません。先に pdf_extract.py を実行してください")

    print("=== マスク結果の検査（本文は読まない）===\n")
    ng = 0
    for p in targets:
        if not p.exists():
            print(f"  {p.name}: 見つかりません\n")
            continue
        r = verify(p)
        危険 = r["①未許可語の残存"] > 0 or r["②単価語の行に残る数値"] > 0 or r["③敬称の前が生のまま"] > 0
        ng += 危険
        print(f"[{'⚠️ 要確認' if 危険 else '✅ 安全'}] {r['ファイル']}（{r['行数']}行）")
        print(f"     ① 未許可語の残存      : {r['①未許可語の残存']}")
        print(f"     ② 単価語の行の生数値  : {r['②単価語の行に残る数値']}"
              f"（該当 {r['②該当行数']}行）")
        print(f"     ③ 敬称の前が生のまま    : {r['③敬称の前が生のまま']}")
        print(f"     ④ 残った金額          : {r['④残った金額の件数']}件"
              f" ／ 最大 {r['④最大額']:,}円 ／ 伏字 {r['伏字マーカー数']}箇所\n")

    print("①②③がすべて0なら社外へ出してよい。" if not ng
          else f"⚠️ {ng}件が基準を満たしていない。修正するまで社外へ出さないこと。")


if __name__ == "__main__":
    main()
