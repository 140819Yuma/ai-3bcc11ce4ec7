"""経費データの読み込み（CSV / Excel、縦持ち / 横持ちの両対応）

会計ソフト（freee・マネーフォワード等）の「月次推移」エクスポートは
横持ち（行=勘定科目、列=各月）が一般的なため、その形式と、
シンプルな縦持ち（month,category,amount）の両方を自動判別して読む。
.xlsx はそのまま読める（openpyxl）。Excelで開ける形式で渡してもらってOK。
"""
import csv
import re
from collections import OrderedDict
from datetime import date, datetime
from pathlib import Path

CATEGORY_HEADERS = {"category", "勘定科目", "費目", "科目", "項目", "費用"}
MONTH_HEADERS = {"month", "月", "年月"}
AMOUNT_HEADERS = {"amount", "金額", "値", "実績"}


def _norm_month(v) -> str:
    """'2025/7'・'2025年7月'・Excelの日付 などを 'YYYY-MM' に正規化。"""
    if isinstance(v, (datetime, date)):
        return f"{v.year:04d}-{v.month:02d}"
    s = str(v).strip()
    m = re.match(r"(\d{4})\D+(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}"
    return s


def _to_amount(v):
    """'1,200,000'・'1200000円'・数値 などを int に。空欄は None。"""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    s = re.sub(r"[,\s円¥]", "", str(v))
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _read_rows(path: Path) -> list[list]:
    """CSV / Excel を「行のリスト」に読み出す。"""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        return [list(r) for r in ws.iter_rows(values_only=True)]
    with open(path, encoding="utf-8-sig") as f:  # BOM付きExcel CSVも読める
        return list(csv.reader(f))


def load_expenses(path) -> "OrderedDict[str, list[dict]]":
    """費目ごとの月次実績 OrderedDict{category: [{month, amount}, ...]} を返す。"""
    path = Path(path)
    rows = [r for r in _read_rows(path) if any(c not in (None, "") for c in r)]
    if not rows:
        return OrderedDict()

    header = [("" if c is None else str(c).strip()) for c in rows[0]]
    lower = [h.lower() for h in header]

    # 金額列がヘッダーにあれば縦持ち、なければ横持ち（月次推移）と判定
    has_amount_col = any(h in AMOUNT_HEADERS for h in lower)
    if has_amount_col:
        return _load_long(header, lower, rows[1:])
    return _load_wide(header, rows[1:])


def _load_long(header, lower, data_rows) -> "OrderedDict[str, list[dict]]":
    def find(cands):
        for i, h in enumerate(lower):
            if h in cands:
                return i
        raise ValueError(f"列が見つかりません: {cands}（ヘッダー: {header}）")

    mi, ci, ai = find(MONTH_HEADERS), find(CATEGORY_HEADERS), find(AMOUNT_HEADERS)
    out: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in data_rows:
        amount = _to_amount(r[ai])
        if amount is None:
            continue
        out.setdefault(str(r[ci]).strip(), []).append(
            {"month": _norm_month(r[mi]), "amount": amount}
        )
    for v in out.values():
        v.sort(key=lambda x: x["month"])
    return out


def _load_wide(header, data_rows) -> "OrderedDict[str, list[dict]]":
    # header[0] = 勘定科目ラベル（無視）、header[1:] = 各月
    months = [_norm_month(h) for h in header[1:]]
    out: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in data_rows:
        category = ("" if r[0] is None else str(r[0]).strip())
        if not category:
            continue
        series = []
        for month, cell in zip(months, r[1:]):
            amount = _to_amount(cell)
            if amount is not None:
                series.append({"month": month, "amount": amount})
        if series:
            series.sort(key=lambda x: x["month"])
            out[category] = series
    return out
