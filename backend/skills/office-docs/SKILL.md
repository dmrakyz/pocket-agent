---
name: office-docs
description: Make Word (.docx), Excel (.xlsx) and PowerPoint (.pptx) files with python-docx, openpyxl and python-pptx, then hand them over as file cards.
---
# Office files

Write a short Python script, run it with `bash`, then give the file to the user with `[Report](ws:out/report.docx)` on its own line
(a card with Download appears). Keep files in `out/`. After saving, reopen the file with the same library and check it has what you expect.

## Word (.docx)
```python
import os
from docx import Document
from docx.shared import Pt
os.makedirs("out", exist_ok=True)
d = Document()
d.add_heading("Quarterly report", 0)
d.add_paragraph("Short intro paragraph.")
d.add_heading("Highlights", 1)
for t in ("First point", "Second point"):
    d.add_paragraph(t, style="List Bullet")
tbl = d.add_table(rows=1, cols=3); tbl.style = "Light Grid Accent 1"
for c, h in zip(tbl.rows[0].cells, ("Item", "Qty", "Price")):
    c.text = h
for row in (("Widget", "3", "9.50"), ("Gadget", "1", "20.00")):
    cells = tbl.add_row().cells
    for c, v in zip(cells, row):
        c.text = v
# d.add_picture("charts/sales.png", width=Pt(400))   # a chart made earlier
d.save("out/report.docx")
print(len(Document("out/report.docx").paragraphs), "paragraphs saved")
```

## Excel (.xlsx)
```python
import os
from openpyxl import Workbook
from openpyxl.styles import Font
os.makedirs("out", exist_ok=True)
wb = Workbook(); ws = wb.active; ws.title = "Budget"
ws.append(["Item", "Cost"])
for r in (("Rent", 900), ("Food", 350), ("Transport", 80)):
    ws.append(r)
ws.append(["Total", "=SUM(B2:B4)"])            # real formulas stay live in Excel
for c in ws[1]:
    c.font = Font(bold=True)
ws.column_dimensions["A"].width = 18
wb.save("out/budget.xlsx")
print(wb.sheetnames, ws["B5"].value)
```
Formulas are not calculated by openpyxl; if you need the number in your answer, compute it in Python too.

## PowerPoint (.pptx)
```python
import os
from pptx import Presentation
from pptx.util import Inches
os.makedirs("out", exist_ok=True)
p = Presentation()
s = p.slides.add_slide(p.slide_layouts[0]); s.shapes.title.text = "Project update"; s.placeholders[1].text = "November"
s = p.slides.add_slide(p.slide_layouts[1]); s.shapes.title.text = "Key points"
tf = s.placeholders[1].text_frame; tf.text = "Shipped the browser"
tf.add_paragraph().text = "Next: Telegram"
# s = p.slides.add_slide(p.slide_layouts[5]); s.shapes.add_picture("charts/sales.png", Inches(1), Inches(1.5), width=Inches(8))
p.save("out/update.pptx")
print(len(Presentation("out/update.pptx").slides), "slides saved")
```
Keep slides to a title and 3-5 short bullets; put detail in the speaker notes (`slide.notes_slide.notes_text_frame.text = "..."`).
