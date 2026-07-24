"""Excelで開いて記入できる横持ち（月次推移）テンプレートを生成する。
  python3 make_template.py
生成物:
  data/template_expenses.xlsx … Excelでそのまま開いて数字を入れられる
  data/template_expenses.csv  … 同じ内容のCSV（UTF-8 BOM付き＝Excelで文字化けしない）
サンプル値を入れてあるので、費目・月・金額を自分の会計データに置き換えて使う。
本番で使うときは data/expenses.xlsx （または expenses.csv）にリネームすれば app.py が自動で読む。
"""
import csv
from pathlib import Path

import openpyxl

DATA_DIR = Path(__file__).parent / "data"
MONTHS = [
    "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12",
    "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06",
]
# 費目 → 各月の金額（サンプル。実データに差し替えて使う）
ROWS = {
    "人件費": [1200000, 1200000, 1250000, 1250000, 1250000, 2100000,
               1250000, 1250000, 1300000, 1350000, 1350000, 2200000],
    "外注費": [180000, 150000, 320000, 90000, 410000, 120000,
               60000, 280000, 150000, 95000, 340000, 110000],
    "家賃":   [220000, 220000, 220000, 220000, 220000, 220000,
               230000, 230000, 230000, 230000, 230000, 230000],
}
HEADER = ["勘定科目", *MONTHS]


def make_xlsx(path: Path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "月次推移"
    ws.append(HEADER)
    for category, amounts in ROWS.items():
        ws.append([category, *amounts])
    ws.freeze_panes = "B2"  # 1行目・1列目を固定して見やすく
    ws.column_dimensions["A"].width = 14
    wb.save(path)


def make_csv(path: Path):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # BOM付き
        w = csv.writer(f)
        w.writerow(HEADER)
        for category, amounts in ROWS.items():
            w.writerow([category, *amounts])


if __name__ == "__main__":
    make_xlsx(DATA_DIR / "template_expenses.xlsx")
    make_csv(DATA_DIR / "template_expenses.csv")
    print("生成しました:")
    print("  data/template_expenses.xlsx （Excelで開いて記入）")
    print("  data/template_expenses.csv  （UTF-8 BOM付き）")
