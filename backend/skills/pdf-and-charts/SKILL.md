---
name: pdf-and-charts
description: Make PDF files with reportlab and charts with matplotlib (PNG), then show them inline or as file cards.
---
# PDFs and charts

Run scripts with `bash`. Show a chart inline with `![Sales by month](ws:charts/sales.png)` (add `|l` after the caption for a large one
when the labels must be readable). Hand over a PDF with `[Invoice](ws:out/invoice.pdf)` on its own line.

## Chart (PNG)
```python
import os
import matplotlib
matplotlib.use("Agg")                      # no display on the server; never call plt.show()
import matplotlib.pyplot as plt
os.makedirs("charts", exist_ok=True)
months = ["Jan", "Feb", "Mar", "Apr"]; sales = [12, 18, 9, 22]
fig, ax = plt.subplots(figsize=(6, 4))     # phone-friendly size
ax.bar(months, sales, color="#6d4aff")
ax.set_title("Sales by month"); ax.set_ylabel("Units"); ax.grid(axis="y", alpha=.3)
for i, v in enumerate(sales):
    ax.text(i, v + .3, str(v), ha="center")
fig.tight_layout(); fig.savefig("charts/sales.png", dpi=150); plt.close(fig)
print(os.path.getsize("charts/sales.png"), "bytes")
```
Label axes and units, keep one message per chart, use at most 5-6 colours. For a line over time use `ax.plot(x, y, marker="o")`.

## PDF
```python
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
os.makedirs("out", exist_ok=True)
st = getSampleStyleSheet()
doc = SimpleDocTemplate("out/invoice.pdf", pagesize=A4, title="Invoice")
rows = [["Item", "Qty", "Price"], ["Widget", "3", "9.50"], ["Gadget", "1", "20.00"]]
t = Table(rows, hAlign="LEFT")
t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6d4aff")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                       ("GRID", (0, 0), (-1, -1), .4, colors.grey), ("ALIGN", (1, 0), (-1, -1), "RIGHT")]))
story = [Paragraph("Invoice", st["Title"]), Paragraph("Thanks for your order.", st["BodyText"]), Spacer(1, 12), t]
# from reportlab.platypus import Image; story.append(Image("charts/sales.png", width=400, height=266))
doc.build(story)
print(os.path.getsize("out/invoice.pdf"), "bytes")
```
Use `<b>`, `<i>` and `<br/>` inside Paragraph text; escape `&` as `&amp;`. Non-Latin text needs a TTF font registered with `pdfmetrics.registerFont`.
