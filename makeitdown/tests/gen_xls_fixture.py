"""一次性生成真 OLE2 .xls 测试样本(dev 用:pip install xlwt)。"""
from pathlib import Path

import xlwt

wb = xlwt.Workbook()
ws = wb.add_sheet("流水")
rows = [
    ["日期", "摘要", "金额"],
    ["2024-03-15", "货款", "1,234,567.89"],
    ["2024-06-30", "退款", "493,827.16"],
]
for r, row in enumerate(rows):
    for c, val in enumerate(row):
        ws.write(r, c, val)
Path(__file__).parent.joinpath("fixtures").mkdir(exist_ok=True)
wb.save(str(Path(__file__).parent / "fixtures" / "ledger.xls"))
print("wrote fixtures/ledger.xls")
