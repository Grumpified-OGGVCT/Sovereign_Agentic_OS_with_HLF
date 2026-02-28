from __future__ import annotations
from typing import Dict, List, Any

def upload_tabs(gsheet_id: str, creds_path: str, tab_rows: Dict[str, List[Dict[str, Any]]]) -> None:
    import gspread
    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_key(gsheet_id)

    for tab, rows in tab_rows.items():
        if rows:
            headers = list(rows[0].keys())
            values = [headers] + [[str(r.get(h, "")) for h in headers] for r in rows]
        else:
            headers = ["empty"]
            values = [headers, [""]]

        try:
            ws = sh.worksheet(tab)
        except Exception:
            ws = sh.add_worksheet(title=tab, rows=3000, cols=max(20, len(headers)+2))
        ws.clear()
        ws.update("A1", values, value_input_option="RAW")
