# officebench ob-2-23-0 rep 6

- result: failed: evaluate_contain
- calls 5, 11.06 s, $0.0030

## Request

I'll read the invoice PDFs to find the total due.

## Steps
  - result: # Invoice.pdf 1 pages  ## Page 1 INVOICE     333 3 rd Ave   Seattle, WA 12345     Phone:  123 -456-7890  INVOICE #  100  DATE:  1/30/23   PURCHASED BY:    Liane Cormier    The Social Strategists    4321 Maplewood Ave    Nashville, TN 54321     Phone:  111-222-3333  SHIP TO:   Liane Cormier    The So
  - result: [exit 0] total 208 drwxr-xr-x 2 root root   4096 Nov 25 17:33 . drwxr-xr-x 4 root root   4096 Nov 25 18:55 .. -rw-r--r-- 1 root root  57911 Nov 25 17:33 Invoice.pdf -rw-r--r-- 1 root root 140712 Nov 25 17:33 Invoice_2.pdf 
- **assistant:** Total due = 104.98. Writing the workbook.  
- **shell:** `{"command": ["bash", "-lc", "cat > /tmp/mk.py <<'EOF'\nfrom openpyxl import Workbook\nwb = Workbook()\nws = wb.active\nws.title = \"Costs\"\nws[\"A1\"] = \"Invoice #\"\nws[\"B1\"] = \"Date\"\nws[\"C1\"] = \"Subtotal\"\nws[\"D1\"] = \"Sales tax\"\nws[\"E1\"] = \"Shipping and handling\"\nws[\"F1\"] = \"Total due\"\nws[\"A2\"] = \"100\"\nws[\"B2\"] = \"2023-01-30\"\nws[\"C2\"] = 99.99\nws[\"D2\"] = 4.99\nws[\"E2\"] = 0.00\nws[\"F2\"] = \"=SUM(C2:E2)\"\nfor c in \"CDEF\":\n    ws[f\"{c}2\"].number_f`
  - result: [exit 0] 
- **assistant:**   
- **shell:** `{"command": ["bash", "-lc", "cd data && soffice --headless --convert-to xlsx --outdir /tmp/calc costs.xlsx >/dev/null 2>&1; python3 -c \"\nfrom openpyxl import load_workbook\nwb=load_workbook('costs.xlsx'); ws=wb.active\nprint('sheet',ws.title,'formula',ws['F2'].value)\nw2=load_workbook('/tmp/calc/costs.xlsx',data_only=True); print('total due =',w2.active['F2'].value)\nassert abs(w2.active['F2'].value-104.98)<1e-9\nprint('OK')\n\"", "ls -la data"]}`
  - result: [exit 0] sheet Costs formula =SUM(C2:E2) total due = 104.98 OK 
- **assistant:** Done. `data/costs.xlsx` (sheet "Costs") holds the invoice data: invoice #100, date 2023-01-30, subtotal 99.99, sales tax 4.99, shipping 0.00, and Total due 104.98 as a live formula `=SUM(C2:E2)`; LibreOffice recalculation confirms 104.98. Defaults I picked: single line-item invoice, so the cost breakdown (subtotal/tax/shipping) is kept instead of only the grand total; date stored as ISO text. Invo
