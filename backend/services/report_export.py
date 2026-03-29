"""Export report dicts to CSV and PDF (UTF-8 CSV with BOM for Excel)."""
from __future__ import annotations

import csv
import io
import json
import re
from typing import Any, Dict, List, Tuple

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_UTF8_SIG = "\ufeff"


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _trunc(s: str, n: int = 72) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def _slug_filename(report_type: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", report_type or "report").strip("_").lower()
    return base or "report"


def _summary_rows(summary: dict) -> List[Tuple[str, str]]:
    rows: List[Tuple[str, str]] = []
    for sk, sv in summary.items():
        if isinstance(sv, dict):
            for subk, subv in sv.items():
                rows.append((f"summary.{sk}.{subk}", _cell(subv)))
        else:
            rows.append((f"summary.{sk}", _cell(sv)))
    return rows


def _main_table(data: dict) -> Tuple[str | None, List[str], List[List[str]]]:
    for list_key in ("instruments", "records", "alerts", "work_orders"):
        rows = data.get(list_key)
        if not rows or not isinstance(rows, list) or not rows:
            continue
        dict_rows = [r for r in rows if isinstance(r, dict)]
        if not dict_rows:
            continue
        skip_nested = {"calibration_history", "recent_alerts"}
        keys: set[str] = set()
        for r in dict_rows:
            keys.update(k for k in r.keys() if k not in skip_nested)
        ordered = sorted(keys)
        table_rows: List[List[str]] = [ordered]
        for r in dict_rows:
            table_rows.append([_cell(r.get(k)) for k in ordered])
        return list_key, ordered, table_rows
    return None, [], []


def _calibration_history_csv(w: csv.writer, instruments: list) -> None:
    w.writerow([])
    w.writerow(["# section", "calibration_history"])
    hist_keys = ["tag_number", "performed_at", "due_next_at", "passed", "performed_by", "notes"]
    w.writerow(hist_keys)
    for r in instruments:
        if not isinstance(r, dict):
            continue
        tag = r.get("tag_number", "")
        for h in r.get("calibration_history") or []:
            if not isinstance(h, dict):
                continue
            w.writerow(
                [
                    tag,
                    _cell(h.get("performed_at")),
                    _cell(h.get("due_next_at")),
                    _cell(h.get("passed")),
                    _cell(h.get("performed_by")),
                    _cell(h.get("notes")),
                ]
            )


def _health_alerts_csv(w: csv.writer, instruments: list) -> None:
    w.writerow([])
    w.writerow(["# section", "recent_alerts"])
    w.writerow(["instrument_tag", "timestamp", "alert_type", "severity", "message"])
    for r in instruments:
        if not isinstance(r, dict):
            continue
        tag = r.get("tag_number", "")
        for a in r.get("recent_alerts") or []:
            if not isinstance(a, dict):
                continue
            w.writerow(
                [
                    tag,
                    _cell(a.get("timestamp")),
                    _cell(a.get("alert_type")),
                    _cell(a.get("severity")),
                    _cell(a.get("message")),
                ]
            )


def report_to_csv_bytes(data: Dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")

    if data.get("error"):
        w.writerow(["error", data["error"]])
        return (_UTF8_SIG + buf.getvalue()).encode("utf-8")

    w.writerow(["# report_type", data.get("report_type", "")])
    w.writerow(["# generated_at", data.get("generated_at", "")])
    for meta_key in ("period_days", "severity_threshold"):
        if meta_key in data:
            w.writerow([f"# {meta_key}", data[meta_key]])
    w.writerow([])

    summary = data.get("summary")
    if isinstance(summary, dict):
        w.writerow(["metric", "value"])
        for k, v in _summary_rows(summary):
            w.writerow([k, v])
        w.writerow([])

    list_key, _keys, main = _main_table(data)
    if main:
        for i, row in enumerate(main):
            if i == 0:
                w.writerow([list_key or "data"] + row)
            else:
                w.writerow([""] + row)
        w.writerow([])

    if isinstance(data.get("instruments"), list) and any(
        isinstance(r, dict) and r.get("calibration_history") for r in data["instruments"]
    ):
        _calibration_history_csv(w, data["instruments"])

    if isinstance(data.get("instruments"), list) and any(
        isinstance(r, dict) and r.get("recent_alerts") for r in data["instruments"]
    ):
        _health_alerts_csv(w, data["instruments"])

    recs = data.get("recommendations")
    if isinstance(recs, list) and recs:
        w.writerow([])
        w.writerow(["# section", "recommendations"])
        w.writerow(["idx", "text"])
        for i, line in enumerate(recs):
            w.writerow([i + 1, _cell(line)])

    return (_UTF8_SIG + buf.getvalue()).encode("utf-8")


def _pdf_escape(text: str) -> str:
    return (
        str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def report_to_pdf_bytes(data: Dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    styles = getSampleStyleSheet()
    story: List[Any] = []
    page_size = A4

    title = data.get("report_type", "Report")
    story.append(Paragraph(_pdf_escape(title), styles["Title"]))
    story.append(Spacer(1, 8))
    story.append(
        Paragraph(
            _pdf_escape(f"Generated: {data.get('generated_at', '')}"),
            styles["Normal"],
        )
    )
    if "period_days" in data:
        story.append(
            Paragraph(
                _pdf_escape(f"Period (days): {data['period_days']}"),
                styles["Normal"],
            )
        )
    story.append(Spacer(1, 14))

    if data.get("error"):
        story.append(Paragraph(_pdf_escape(str(data["error"])), styles["Normal"]))
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36,
        )
        doc.build(story)
        return buffer.getvalue()

    summary = data.get("summary")
    if isinstance(summary, dict) and summary:
        story.append(Paragraph("Summary", styles["Heading2"]))
        story.append(Spacer(1, 6))
        srows = [["Metric", "Value"]] + [
            [_pdf_escape(_trunc(k)), _pdf_escape(_trunc(_cell(v)))] for k, v in _summary_rows(summary)
        ]
        tw = 3.2 * inch
        st = Table(srows, colWidths=[tw, 4.3 * inch])
        st.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1976d2")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(st)
        story.append(Spacer(1, 16))

    list_key, keys, main = _main_table(data)
    if main and keys:
        story.append(
            Paragraph(_pdf_escape((list_key or "Data").title()), styles["Heading2"])
        )
        story.append(Spacer(1, 6))
        hdr = [[_pdf_escape(_trunc(k, 24)) for k in keys]]
        body = [
            [_pdf_escape(_trunc(str(row[i]), 40)) for i in range(len(keys))]
            for row in main[1:]
        ]
        data_rows = hdr + body
        ncols = len(keys)
        if ncols > 7:
            page_size = landscape(A4)
        usable = (page_size[0] / inch - 1.0) * inch
        col_w = max(0.7 * inch, usable / max(ncols, 1))
        tbl = Table(
            data_rows,
            colWidths=[col_w] * ncols,
            repeatRows=1,
        )
        tbl.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1976d2")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("GRID", (0, 0), (-1, -1), 0.2, colors.grey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(tbl)
        story.append(Spacer(1, 16))

    recs = data.get("recommendations")
    if isinstance(recs, list) and recs:
        story.append(Paragraph("Recommendations", styles["Heading2"]))
        story.append(Spacer(1, 6))
        for line in recs:
            story.append(
                Paragraph(
                    "• " + _pdf_escape(_trunc(str(line), 500)),
                    styles["Normal"],
                )
            )
            story.append(Spacer(1, 3))

    doc = SimpleDocTemplate(
        buffer,
        pagesize=page_size,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    doc.build(story)
    return buffer.getvalue()


def report_attachment(data: dict, export: str) -> tuple[bytes, str, str]:
    """Returns (body, media_type, filename)."""
    slug = _slug_filename(str(data.get("report_type", "report")))
    ex = export.lower().strip()
    if ex == "csv":
        return report_to_csv_bytes(data), "text/csv; charset=utf-8", f"{slug}.csv"
    if ex == "pdf":
        return report_to_pdf_bytes(data), "application/pdf", f"{slug}.pdf"
    raise ValueError("export must be csv or pdf")
