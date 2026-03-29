"""
Generate printable HTML documents for work orders and calibration certificates.

Work Order   -- follows ISO 55000 / IEC 62264 CMMS conventions.
Cal. Cert.   -- follows ISO/IEC 17025:2017 ss7.8 reporting requirements with
               5-point (0/25/50/75/100 % span) ascending test method per ISA-51.1.

Both documents are self-contained HTML pages with @media print CSS so the
technician can open them in a browser tab and print or Save as PDF directly.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.models import CalibrationRecord, Instrument, WorkOrder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _s(v) -> str:
    """Safe string -- renders None as em-dash."""
    if v is None:
        return "\u2014"
    return str(v)


def _fmt_date(dt: Optional[datetime]) -> str:
    if dt is None:
        return "\u2014"
    return dt.strftime("%d %B %Y")


def _fmt_datetime(dt: Optional[datetime]) -> str:
    if dt is None:
        return "\u2014"
    return dt.strftime("%d %B %Y  %H:%M")


def _enum_val(v) -> str:
    return v.value if hasattr(v, "value") else str(v)


# ---------------------------------------------------------------------------
# Shared CSS (triple-quoted to avoid escaping issues)
# ---------------------------------------------------------------------------

_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: Arial, Helvetica, sans-serif; font-size: 9.5pt;
  color: #000; background: #fff; }
@page { size: A4 portrait; margin: 16mm 14mm 16mm 14mm; }
.page { width: 100%; max-width: 182mm; margin: 0 auto; }

@media screen {
  body { background: #e2e8f0; padding: 20px; }
  .page { background: #fff; padding: 12mm 14mm;
    box-shadow: 0 2px 16px rgba(0,0,0,0.15); }
  .print-btn {
    position: fixed; top: 14px; right: 14px;
    background: #4f46e5; color: #fff; border: none; border-radius: 6px;
    padding: 9px 20px; font-size: 13px; font-weight: 600; cursor: pointer;
    z-index: 100; font-family: inherit; letter-spacing: 0.02em; }
  .print-btn:hover { background: #4338ca; }
}
@media print { .print-btn { display: none; } }

.doc-header {
  display: flex; justify-content: space-between; align-items: flex-start;
  border-bottom: 2pt solid #4f46e5; padding-bottom: 6pt; margin-bottom: 9pt; }
.org-name { font-size: 12.5pt; font-weight: 700; color: #4f46e5; }
.org-sub  { font-size: 8pt; color: #555; margin-top: 2pt; }
.doc-type-block { text-align: right; }
.doc-type   { font-size: 13pt; font-weight: 700; color: #000; letter-spacing: 0.04em; }
.doc-number { font-size: 11pt; font-weight: 700; color: #4f46e5; margin-top: 2pt; }
.doc-meta   { font-size: 7.5pt; color: #555; margin-top: 3pt; }

.section { margin-bottom: 7pt; border: 0.75pt solid #cbd5e1;
  border-radius: 2pt; overflow: hidden; }
.section-hdr { background: #1e293b; color: #fff; font-size: 7.5pt;
  font-weight: 700; padding: 3pt 7pt; letter-spacing: 0.07em;
  text-transform: uppercase; }
.section-body { padding: 6pt 8pt; }

.fg2 { display: grid; grid-template-columns: 1fr 1fr; gap: 5pt 12pt; }
.fg3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 5pt 10pt; }
.fg4 { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 5pt 8pt; }
.full { grid-column: 1 / -1; }
.f-label { font-size: 6.8pt; font-weight: 700; color: #555;
  text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 2pt; }
.f-value { font-size: 9pt; border-bottom: 0.5pt solid #bbb;
  min-height: 13pt; padding-bottom: 1pt; }
.f-value.lg   { font-size: 11pt; font-weight: 700; }
.f-value.bold { font-weight: 600; font-size: 9.5pt; }

.txt-box { border: 0.5pt solid #bbb; min-height: 28pt; padding: 4pt 5pt;
  font-size: 9pt; border-radius: 1pt; }
.txt-box.tall { min-height: 46pt; }

table.gt { width: 100%; border-collapse: collapse; font-size: 8.5pt; }
table.gt th { background: #1e293b; color: #fff; padding: 3pt 5pt;
  text-align: center; font-size: 7.5pt; font-weight: 700; letter-spacing: 0.04em; }
table.gt td { border: 0.5pt solid #cbd5e1; padding: 4pt 5pt;
  text-align: center; min-height: 14pt; }
table.gt tr:nth-child(even) td { background: #f8fafc; }
table.gt th.left, table.gt td.left { text-align: left; }

table.pt { width: 100%; border-collapse: collapse; font-size: 8.5pt; }
table.pt th { background: #f1f5f9; border: 0.5pt solid #cbd5e1;
  padding: 3pt 5pt; text-align: left; font-size: 7.5pt; font-weight: 700; }
table.pt td { border: 0.5pt solid #cbd5e1; padding: 5pt 5pt; min-height: 15pt; }

.cl { list-style: none; padding: 0; }
.cl li { display: flex; align-items: flex-start; gap: 6pt; padding: 2.5pt 0;
  border-bottom: 0.4pt solid #f0f0f0; font-size: 8.5pt; }
.cl li:last-child { border-bottom: none; }
.cb { width: 9pt; height: 9pt; border: 1pt solid #555; flex-shrink: 0; margin-top: 0.5pt; }

.sig-grid { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8pt; }
.sig-box  { border: 0.5pt solid #bbb; border-radius: 2pt; padding: 5pt 6pt; }
.sig-lbl  { font-size: 7pt; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.05em; color: #555; margin-bottom: 20pt; }
.sig-line { border-top: 0.75pt solid #000; margin-bottom: 3pt; }
.sig-sub  { font-size: 7pt; color: #555; margin-top: 2pt; }

.badge  { display: inline-block; border-radius: 2pt; padding: 1.5pt 6pt;
  font-size: 7.5pt; font-weight: 700; }
.b-pass   { background: #dcfce7; color: #166534; }
.b-fail   { background: #fee2e2; color: #991b1b; }
.b-open   { background: #dbeafe; color: #1e40af; }
.b-inprog { background: #fef9c3; color: #854d0e; }
.b-done   { background: #dcfce7; color: #166534; }
.b-urg    { background: #fee2e2; color: #991b1b; }
.b-high   { background: #fef3c7; color: #92400e; }
.b-med    { background: #ede9fe; color: #5b21b6; }
.b-low    { background: #f1f5f9; color: #475569; }

.conf-box { border: 1pt solid #bbb; border-radius: 2pt; padding: 6pt 8pt;
  font-size: 8.5pt; line-height: 1.55; margin-top: 5pt; }
.conf-box.pass { border-color: #059669; background: #f0fdf4; }
.conf-box.fail { border-color: #dc2626; background: #fef2f2; }

.r-pass { color: #059669; font-weight: 700; }
.r-fail { color: #dc2626; font-weight: 700; }

.doc-footer { border-top: 0.75pt solid #ccc; margin-top: 8pt; padding-top: 5pt;
  display: flex; justify-content: space-between; font-size: 7pt; color: #888; }

.note { font-size: 7.5pt; color: #555; margin-top: 4pt; line-height: 1.4; }
"""


# ---------------------------------------------------------------------------
# HTML skeleton
# ---------------------------------------------------------------------------

def _html_wrap(title: str, body: str) -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8"/>\n'
        f"<title>{title}</title>\n"
        f"<style>\n{_CSS}\n</style>\n"
        "</head>\n<body>\n"
        '<button class="print-btn" onclick="window.print()">Print / Save PDF</button>\n'
        '<div class="page">\n'
        + body
        + "\n</div>\n</body>\n</html>"
    )


# ---------------------------------------------------------------------------
# Work Order
# ---------------------------------------------------------------------------

def work_order_html(wo: WorkOrder) -> str:
    """Render a printable work order (ISO 55000 / CMMS industry format)."""

    pri = _enum_val(wo.priority)
    priority_badge = {
        "urgent": '<span class="badge b-urg">URGENT</span>',
        "high":   '<span class="badge b-high">HIGH</span>',
        "medium": '<span class="badge b-med">MEDIUM</span>',
        "low":    '<span class="badge b-low">LOW</span>',
    }.get(pri, f'<span class="badge b-low">{pri.upper()}</span>')

    stat = _enum_val(wo.status)
    status_badge = {
        "open":        '<span class="badge b-open">OPEN</span>',
        "in_progress": '<span class="badge b-inprog">IN PROGRESS</span>',
        "completed":   '<span class="badge b-done">COMPLETED</span>',
        "cancelled":   '<span class="badge b-low">CANCELLED</span>',
    }.get(stat, f'<span class="badge b-low">{stat.upper()}</span>')

    work_type_label = _enum_val(wo.work_type).replace("_", " ").title()

    inst = wo.instrument
    if inst:
        tag_no    = inst.tag_number
        inst_type = _enum_val(inst.instrument_type).title()
        location  = _s(inst.location)
        assoc_eq  = _s(inst.associated_equipment)
        meas_var  = _s(inst.measured_variable)
        range_str = f"{inst.range_min} \u2013 {inst.range_max} {inst.unit or ''}".strip()
        accuracy  = _s(inst.accuracy_class)
    else:
        tag_no = inst_type = location = assoc_eq = meas_var = range_str = accuracy = "\u2014"

    completed_val = _fmt_datetime(wo.completed_at) if wo.completed_at else "\u00a0"
    generated     = datetime.utcnow().strftime("%d %B %Y  %H:%M UTC")

    parts_rows = "".join(
        f"<tr><td>{i}</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>"
        for i in range(1, 6)
    )

    body = f"""
  <div class="doc-header">
    <div>
      <div class="org-name">Copperbelt University</div>
      <div class="org-sub">Department of Engineering &mdash; Instrumentation &amp; Maintenance</div>
    </div>
    <div class="doc-type-block">
      <div class="doc-type">WORK ORDER</div>
      <div class="doc-number">{_s(wo.work_order_number)}</div>
      <div class="doc-meta">Issued: {_fmt_date(wo.created_at)}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">1 &mdash; Work Order Identification</div>
    <div class="section-body">
      <div class="fg2">
        <div><div class="f-label">WO Number</div><div class="f-value lg">{_s(wo.work_order_number)}</div></div>
        <div><div class="f-label">Work Type</div><div class="f-value">{work_type_label}</div></div>
        <div><div class="f-label">Priority</div><div class="f-value">{priority_badge}</div></div>
        <div><div class="f-label">Status</div><div class="f-value">{status_badge}</div></div>
        <div><div class="f-label">Assigned To</div><div class="f-value">{_s(wo.assigned_to)}</div></div>
        <div><div class="f-label">Due Date</div><div class="f-value">{_fmt_date(wo.due_date)}</div></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">2 &mdash; Equipment / Instrument Identification</div>
    <div class="section-body">
      <div class="fg2">
        <div><div class="f-label">Tag Number</div><div class="f-value lg">{tag_no}</div></div>
        <div><div class="f-label">Instrument Type</div><div class="f-value">{inst_type}</div></div>
        <div><div class="f-label">Measured Variable</div><div class="f-value">{meas_var}</div></div>
        <div><div class="f-label">Range</div><div class="f-value">{range_str}</div></div>
        <div><div class="f-label">Accuracy Class</div><div class="f-value">{accuracy}</div></div>
        <div><div class="f-label">Location</div><div class="f-value">{location}</div></div>
        <div class="full"><div class="f-label">Associated Equipment</div><div class="f-value">{assoc_eq}</div></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">3 &mdash; Work Description</div>
    <div class="section-body">
      <div style="margin-bottom:5pt;">
        <div class="f-label">Title / Fault description</div>
        <div class="f-value bold">{_s(wo.title)}</div>
      </div>
      <div>
        <div class="f-label">Detailed description / Instructions</div>
        <div class="txt-box tall">{_s(wo.description) if wo.description else ''}</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">4 &mdash; Safety Requirements &amp; Permit to Work</div>
    <div class="section-body">
      <ul class="cl">
        <li><div class="cb"></div>Lock-out / Tag-out (LOTO) applied and verified before commencing work</li>
        <li><div class="cb"></div>Process isolation confirmed &mdash; upstream &amp; downstream block valves closed and tagged</li>
        <li><div class="cb"></div>Pressure vented / process fluid drained or isolated as required</li>
        <li><div class="cb"></div>Hot-work permit obtained (if welding, grinding, or open-flame work required)</li>
        <li><div class="cb"></div>Confined-space entry permit obtained (if applicable)</li>
        <li><div class="cb"></div>PPE verified: safety glasses, chemical-resistant gloves, hard hat, safety boots</li>
        <li><div class="cb"></div>Area barricaded and warning signs posted at access points</li>
        <li><div class="cb"></div>Instrument loop placed in manual / bypass at DCS before isolation</li>
        <li><div class="cb"></div>Work area returned to safe condition and LOTO removed on completion</li>
      </ul>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">5 &mdash; Parts &amp; Materials Required</div>
    <div class="section-body">
      <table class="pt">
        <thead><tr>
          <th style="width:6%;">#</th>
          <th style="width:42%;">Description</th>
          <th style="width:22%;">Part / Catalogue No.</th>
          <th style="width:15%;">Qty Required</th>
          <th style="width:15%;">Qty Used</th>
        </tr></thead>
        <tbody>{parts_rows}</tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">6 &mdash; Work Execution Record</div>
    <div class="section-body">
      <div class="fg2" style="margin-bottom:5pt;">
        <div><div class="f-label">Work started (date / time)</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Work completed (date / time)</div><div class="f-value">{completed_val}</div></div>
      </div>
      <div>
        <div class="f-label">Work performed &mdash; findings and actions taken</div>
        <div class="txt-box tall">&nbsp;</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">7 &mdash; Authorization &amp; Sign-off</div>
    <div class="section-body">
      <div class="sig-grid">
        <div class="sig-box">
          <div class="sig-lbl">Technician</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: {_s(wo.assigned_to)}</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
        <div class="sig-box">
          <div class="sig-lbl">Supervisor / Approving Authority</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
        <div class="sig-box">
          <div class="sig-lbl">Plant / Operations Representative</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
      </div>
    </div>
  </div>

  <div class="doc-footer">
    <span>CBU-CMMS &middot; Work Order &middot; Rev. 1</span>
    <span>WO: {_s(wo.work_order_number)}</span>
    <span>Generated: {generated}</span>
  </div>
"""
    return _html_wrap(f"Work Order {_s(wo.work_order_number)}", body)


# ---------------------------------------------------------------------------
# Calibration Certificate
# ---------------------------------------------------------------------------

def calibration_certificate_html(cal: CalibrationRecord, inst: Instrument) -> str:
    """
    Render a calibration certificate per ISO/IEC 17025:2017.

    5-point test table (0 / 25 / 50 / 75 / 100 % of span, ascending) per ISA-51.1.
    The as-found/as-left values stored in the DB are placed at the 50 % row;
    remaining test-point cells are left blank for manual completion in the field.
    """

    cert_number = f"CAL-{inst.tag_number.replace('/', '-')}-{cal.id:05d}"
    passed      = cal.passed
    pass_class  = "pass" if passed else "fail"
    pass_label  = "PASS" if passed else "FAIL"
    inst_type   = _enum_val(inst.instrument_type).title()
    span        = inst.range_max - inst.range_min
    range_str   = f"{inst.range_min} to {inst.range_max} {inst.unit or ''}".strip()
    acceptance  = _s(inst.accuracy_class) if inst.accuracy_class else "per datasheet"
    generated   = datetime.utcnow().strftime("%d %B %Y  %H:%M UTC")

    # Build a lookup from stored 5-point data (if available)
    _pts_by_pct: dict = {}
    if cal.calibration_points:
        for pt in cal.calibration_points:
            _pts_by_pct[float(pt["pct"])] = pt

    def _tp_row(pct: float) -> str:
        ref_val = inst.range_min + span * pct / 100.0
        ref_str = f"{ref_val:.4g}"

        if _pts_by_pct:
            # Use stored multi-point data
            pt = _pts_by_pct.get(pct)
            af  = pt["as_found"]   if pt else None
            al  = pt["as_left"]    if pt else None
            efe = pt["err_found_pct"] if pt else None
            ele = pt["err_left_pct"]  if pt else None
            af_str  = f"{af:.4g}"                   if af  is not None else "&nbsp;"
            al_str  = f"{al:.4g}"                   if al  is not None else "&nbsp;"
            eaf_str = f"{efe:.3f}&nbsp;%"           if efe is not None else "&nbsp;"
            eal_str = f"{ele:.3f}&nbsp;%"           if ele is not None else "&nbsp;"
            result  = f'<span class="r-{pass_class}">{pass_label}</span>' if pct == 50.0 else "&nbsp;"
        elif pct == 50.0:
            # Legacy single-point data
            af_str  = f"{cal.as_found_value:.4g}"  if cal.as_found_value  is not None else "&nbsp;"
            eaf_str = f"{cal.error_found_pct:.3f}&nbsp;%" if cal.error_found_pct is not None else "&nbsp;"
            al_str  = f"{cal.as_left_value:.4g}"   if cal.as_left_value   is not None else "&nbsp;"
            eal_str = f"{cal.error_left_pct:.3f}&nbsp;%"  if cal.error_left_pct  is not None else "&nbsp;"
            result  = f'<span class="r-{pass_class}">{pass_label}</span>'
        else:
            af_str = eaf_str = al_str = eal_str = "&nbsp;"
            result = "&nbsp;"

        return (
            f"<tr>"
            f"<td>{pct:.0f}&nbsp;%</td>"
            f"<td>{ref_str}</td>"
            f"<td>{af_str}</td>"
            f"<td>{eaf_str}</td>"
            f"<td>{al_str}</td>"
            f"<td>{eal_str}</td>"
            f"<td>&plusmn;&nbsp;{acceptance}</td>"
            f"<td>{result}</td>"
            f"</tr>"
        )

    cal_rows = "".join(_tp_row(p) for p in (0.0, 25.0, 50.0, 75.0, 100.0))

    if passed:
        conformance = (
            "The instrument described herein has been calibrated against reference standards "
            "traceable to national measurement standards. Based on the calibration results obtained, "
            "the instrument <strong>IS IN CONFORMANCE</strong> with the stated acceptance criteria "
            "at the time of calibration."
        )
    else:
        conformance = (
            "The instrument described herein has been calibrated against reference standards "
            "traceable to national measurement standards. Based on the calibration results obtained, "
            "the instrument <strong>IS NOT IN CONFORMANCE</strong> with the stated acceptance criteria. "
            "The instrument must be adjusted, repaired, or withdrawn from service before further use."
        )

    notes_html = (
        f'<div style="margin-top:5pt;"><span class="f-label">Notes:</span> '
        f'<span style="font-size:8.5pt;">{_s(cal.notes)}</span></div>'
        if cal.notes else ""
    )

    body = f"""
  <div class="doc-header">
    <div>
      <div class="org-name">Copperbelt University</div>
      <div class="org-sub">Department of Engineering &mdash; Instrumentation Laboratory</div>
      <div class="org-sub" style="margin-top:2pt;">ISO/IEC 17025:2017 Calibration Certificate</div>
    </div>
    <div class="doc-type-block">
      <div class="doc-type">CALIBRATION CERTIFICATE</div>
      <div class="doc-number">{cert_number}</div>
      <div class="doc-meta">Date of calibration: {_fmt_date(cal.performed_at)}</div>
      <div class="doc-meta">Certificate issued: {_fmt_date(datetime.utcnow())}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">1 &mdash; Instrument Under Test (IUT)</div>
    <div class="section-body">
      <div class="fg2">
        <div><div class="f-label">Tag Number</div><div class="f-value lg">{inst.tag_number}</div></div>
        <div><div class="f-label">Instrument Type</div><div class="f-value">{inst_type}</div></div>
        <div><div class="f-label">Measured Variable</div><div class="f-value">{_s(inst.measured_variable)}</div></div>
        <div><div class="f-label">Engineering Unit</div><div class="f-value">{_s(inst.unit)}</div></div>
        <div><div class="f-label">Measurement Range</div><div class="f-value">{range_str}</div></div>
        <div><div class="f-label">Span</div><div class="f-value">{span:.4g}&nbsp;{_s(inst.unit)}</div></div>
        <div><div class="f-label">Accuracy Class</div><div class="f-value">{_s(inst.accuracy_class)}</div></div>
        <div><div class="f-label">Criticality</div><div class="f-value">{_enum_val(inst.criticality).title()}</div></div>
        <div><div class="f-label">Location / Plant area</div><div class="f-value">{_s(inst.location)}</div></div>
        <div><div class="f-label">Associated Equipment</div><div class="f-value">{_s(inst.associated_equipment)}</div></div>
        <div><div class="f-label">Manufacturer</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Model / Serial No.</div><div class="f-value">&nbsp;</div></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">2 &mdash; Reference Standards &amp; Traceability</div>
    <div class="section-body">
      <table class="pt">
        <thead><tr>
          <th style="width:32%;">Standard / Equipment Description</th>
          <th style="width:18%;">Asset / Serial No.</th>
          <th style="width:18%;">Last Cal. Date</th>
          <th style="width:18%;">Cal. Due Date</th>
          <th style="width:14%;">Traceability</th>
        </tr></thead>
        <tbody>
          <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>ZABS / BIPM</td></tr>
          <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
        </tbody>
      </table>
      <p class="note">All reference standards are calibrated and traceable to the Zambia Bureau of Standards (ZABS)
      national measurement standards, traceable to the International Bureau of Weights and Measures (BIPM)
      through an unbroken chain of comparisons with stated uncertainties.</p>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">3 &mdash; Environmental Conditions at Time of Calibration</div>
    <div class="section-body">
      <div class="fg3">
        <div><div class="f-label">Temperature (&deg;C)</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Relative Humidity (%)</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Atmospheric Pressure (kPa)</div><div class="f-value">&nbsp;</div></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">4 &mdash; Calibration Data &mdash; As-Found &amp; As-Left (ISA-51.1 Five-Point Ascending)</div>
    <div class="section-body">
      <table class="gt">
        <thead><tr>
          <th>Test Point<br/>(%&nbsp;span)</th>
          <th>Reference / Applied<br/>({_s(inst.unit)})</th>
          <th>As-Found<br/>Reading ({_s(inst.unit)})</th>
          <th>As-Found Error<br/>(%&nbsp;span)</th>
          <th>As-Left<br/>Reading ({_s(inst.unit)})</th>
          <th>As-Left Error<br/>(%&nbsp;span)</th>
          <th>Acceptance<br/>Criterion</th>
          <th>Result</th>
        </tr></thead>
        <tbody>{cal_rows}</tbody>
      </table>
      <p class="note">5-point ascending test (0&thinsp;/&thinsp;25&thinsp;/&thinsp;50&thinsp;/&thinsp;75&thinsp;/&thinsp;100&thinsp;%
      of span) per ISA-51.1. Values recorded in the CMMS are shown at the 50&thinsp;% test point;
      remaining rows are pre-printed for on-site completion.
      Error&thinsp;=&thinsp;(Reading&thinsp;&minus;&thinsp;Reference)&thinsp;/&thinsp;Span&thinsp;&times;&thinsp;100.</p>
      {notes_html}
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">5 &mdash; Result &amp; Statement of Conformance (ISO/IEC 17025:2017 &sect;7.8.6)</div>
    <div class="section-body">
      <div class="fg4" style="margin-bottom:6pt;">
        <div>
          <div class="f-label">Overall Result</div>
          <div class="f-value">
            <span class="badge b-{pass_class}" style="font-size:9pt;padding:2pt 8pt;">{pass_label}</span>
          </div>
        </div>
        <div><div class="f-label">Calibrated By</div><div class="f-value">{_s(cal.performed_by)}</div></div>
        <div><div class="f-label">Date of Calibration</div><div class="f-value">{_fmt_date(cal.performed_at)}</div></div>
        <div><div class="f-label">Next Calibration Due</div><div class="f-value bold">{_fmt_date(cal.due_next_at)}</div></div>
      </div>
      <div class="conf-box {pass_class}">
        <strong>Statement of Conformance:</strong><br/>
        {conformance}
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">6 &mdash; Authorized Signatures</div>
    <div class="section-body">
      <div class="sig-grid">
        <div class="sig-box">
          <div class="sig-lbl">Calibrating Technician</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: {_s(cal.performed_by)}</div>
          <div class="sig-sub">Date: {_fmt_date(cal.performed_at)}</div>
        </div>
        <div class="sig-box">
          <div class="sig-lbl">Reviewed &amp; Verified by</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
        <div class="sig-box">
          <div class="sig-lbl">Authorized / Approved by</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
      </div>
    </div>
  </div>

  <div class="doc-footer">
    <span>CBU-CMMS &middot; Calibration Certificate &middot; ISO/IEC 17025:2017 &middot; Rev. 1</span>
    <span>{cert_number}</span>
    <span>Generated: {generated}</span>
  </div>
"""
    return _html_wrap(f"Calibration Certificate {cert_number}", body)


# ---------------------------------------------------------------------------
# Blank Calibration Certificate (print before field calibration)
# ---------------------------------------------------------------------------

def calibration_certificate_blank_html(inst: Instrument, latest_cal=None) -> str:
    """
    Render a blank calibration certificate pre-filled with instrument data.

    Used when a technician needs a printable form to take into the field
    *before* entering results into the CMMS.  All measurement cells are left
    blank for manual completion.  If a previous CalibrationRecord is supplied
    the previous calibration date and next-due date are shown for reference.
    """

    cert_number  = f"CAL-{inst.tag_number.replace('/', '-')}-FIELD"
    inst_type    = _enum_val(inst.instrument_type).title()
    span         = inst.range_max - inst.range_min
    range_str    = f"{inst.range_min} to {inst.range_max} {inst.unit or ''}".strip()
    acceptance   = _s(inst.accuracy_class) if inst.accuracy_class else "per datasheet"
    generated    = datetime.utcnow().strftime("%d %B %Y  %H:%M UTC")
    print_date   = _fmt_date(datetime.utcnow())

    prev_cal_date = _fmt_date(latest_cal.performed_at) if latest_cal else "\u2014"
    next_due_date = _fmt_date(latest_cal.due_next_at)  if latest_cal else "\u2014"

    def _blank_row(pct: float) -> str:
        ref_val = inst.range_min + span * pct / 100.0
        ref_str = f"{ref_val:.4g}"
        return (
            f"<tr>"
            f"<td>{pct:.0f}&nbsp;%</td>"
            f"<td>{ref_str}</td>"
            f"<td>&nbsp;</td>"
            f"<td>&nbsp;</td>"
            f"<td>&nbsp;</td>"
            f"<td>&nbsp;</td>"
            f"<td>&plusmn;&nbsp;{acceptance}</td>"
            f"<td>&nbsp;</td>"
            f"</tr>"
        )

    cal_rows = "".join(_blank_row(p) for p in (0.0, 25.0, 50.0, 75.0, 100.0))

    body = f"""
  <div class="doc-header">
    <div>
      <div class="org-name">Copperbelt University</div>
      <div class="org-sub">Department of Engineering &mdash; Instrumentation Laboratory</div>
      <div class="org-sub" style="margin-top:2pt;">ISO/IEC 17025:2017 Calibration Certificate</div>
    </div>
    <div class="doc-type-block">
      <div class="doc-type">CALIBRATION CERTIFICATE</div>
      <div class="doc-number">{cert_number}</div>
      <div class="doc-meta">Date of calibration: ___________________</div>
      <div class="doc-meta">Certificate issued: {print_date}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">1 &mdash; Instrument Under Test (IUT)</div>
    <div class="section-body">
      <div class="fg2">
        <div><div class="f-label">Tag Number</div><div class="f-value lg">{inst.tag_number}</div></div>
        <div><div class="f-label">Instrument Type</div><div class="f-value">{inst_type}</div></div>
        <div><div class="f-label">Measured Variable</div><div class="f-value">{_s(inst.measured_variable)}</div></div>
        <div><div class="f-label">Engineering Unit</div><div class="f-value">{_s(inst.unit)}</div></div>
        <div><div class="f-label">Measurement Range</div><div class="f-value">{range_str}</div></div>
        <div><div class="f-label">Span</div><div class="f-value">{span:.4g}&nbsp;{_s(inst.unit)}</div></div>
        <div><div class="f-label">Accuracy Class</div><div class="f-value">{_s(inst.accuracy_class)}</div></div>
        <div><div class="f-label">Criticality</div><div class="f-value">{_enum_val(inst.criticality).title()}</div></div>
        <div><div class="f-label">Location / Plant area</div><div class="f-value">{_s(inst.location)}</div></div>
        <div><div class="f-label">Associated Equipment</div><div class="f-value">{_s(inst.associated_equipment)}</div></div>
        <div><div class="f-label">Manufacturer</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Model / Serial No.</div><div class="f-value">&nbsp;</div></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">1b &mdash; Calibration Schedule Reference</div>
    <div class="section-body">
      <div class="fg3">
        <div><div class="f-label">Calibration Interval (days)</div><div class="f-value">{inst.calibration_interval_days}</div></div>
        <div><div class="f-label">Previous Calibration Date</div><div class="f-value">{prev_cal_date}</div></div>
        <div><div class="f-label">Next Calibration Due</div><div class="f-value bold">{next_due_date}</div></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">2 &mdash; Reference Standards &amp; Traceability</div>
    <div class="section-body">
      <table class="pt">
        <thead><tr>
          <th style="width:32%;">Standard / Equipment Description</th>
          <th style="width:18%;">Asset / Serial No.</th>
          <th style="width:18%;">Last Cal. Date</th>
          <th style="width:18%;">Cal. Due Date</th>
          <th style="width:14%;">Traceability</th>
        </tr></thead>
        <tbody>
          <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>ZABS / BIPM</td></tr>
          <tr><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td><td>&nbsp;</td></tr>
        </tbody>
      </table>
      <p class="note">All reference standards are calibrated and traceable to the Zambia Bureau of Standards (ZABS)
      national measurement standards, traceable to the International Bureau of Weights and Measures (BIPM)
      through an unbroken chain of comparisons with stated uncertainties.</p>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">3 &mdash; Environmental Conditions at Time of Calibration</div>
    <div class="section-body">
      <div class="fg3">
        <div><div class="f-label">Temperature (&deg;C)</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Relative Humidity (%)</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Atmospheric Pressure (kPa)</div><div class="f-value">&nbsp;</div></div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">4 &mdash; Calibration Data &mdash; As-Found &amp; As-Left (ISA-51.1 Five-Point Ascending)</div>
    <div class="section-body">
      <table class="gt">
        <thead><tr>
          <th>Test Point<br/>(%&nbsp;span)</th>
          <th>Reference / Applied<br/>({_s(inst.unit)})</th>
          <th>As-Found<br/>Reading ({_s(inst.unit)})</th>
          <th>As-Found Error<br/>(%&nbsp;span)</th>
          <th>As-Left<br/>Reading ({_s(inst.unit)})</th>
          <th>As-Left Error<br/>(%&nbsp;span)</th>
          <th>Acceptance<br/>Criterion</th>
          <th>Result</th>
        </tr></thead>
        <tbody>{cal_rows}</tbody>
      </table>
      <p class="note">5-point ascending test (0&thinsp;/&thinsp;25&thinsp;/&thinsp;50&thinsp;/&thinsp;75&thinsp;/&thinsp;100&thinsp;%
      of span) per ISA-51.1. All cells to be completed on-site by the calibrating technician.
      Error&thinsp;=&thinsp;(Reading&thinsp;&minus;&thinsp;Reference)&thinsp;/&thinsp;Span&thinsp;&times;&thinsp;100.</p>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">5 &mdash; Result &amp; Statement of Conformance (ISO/IEC 17025:2017 &sect;7.8.6)</div>
    <div class="section-body">
      <div class="fg4" style="margin-bottom:6pt;">
        <div>
          <div class="f-label">Overall Result</div>
          <div class="f-value">
            <span style="font-size:8.5pt;border:1pt solid #bbb;border-radius:2pt;padding:1.5pt 8pt;">
              PASS &nbsp;/&nbsp; FAIL &nbsp;(circle)</span>
          </div>
        </div>
        <div><div class="f-label">Calibrated By</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Date of Calibration</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">Next Calibration Due</div><div class="f-value bold">{next_due_date}</div></div>
      </div>
      <div class="conf-box">
        <strong>Statement of Conformance:</strong><br/>
        The instrument described herein has been calibrated against reference standards
        traceable to national measurement standards. Based on the calibration results obtained,
        the instrument &nbsp;<strong>[ IS ]&nbsp; / &nbsp;[ IS NOT ]</strong>&nbsp; (circle one)&nbsp;
        in conformance with the stated acceptance criteria at the time of calibration.
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">6 &mdash; Authorized Signatures</div>
    <div class="section-body">
      <div class="sig-grid">
        <div class="sig-box">
          <div class="sig-lbl">Calibrating Technician</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
        <div class="sig-box">
          <div class="sig-lbl">Reviewed &amp; Verified by</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
        <div class="sig-box">
          <div class="sig-lbl">Authorized / Approved by</div>
          <div class="sig-line"></div>
          <div class="sig-sub">Name: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
          <div class="sig-sub">Date: &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;</div>
        </div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-hdr">7 &mdash; CMMS Entry Record</div>
    <div class="section-body">
      <p class="note" style="font-size:8pt;">
        After completing the calibration, enter all results into the CMMS using the
        <strong>Record Calibration</strong> function on the Instruments panel.
        Attach a signed copy of this certificate to the instrument file.
      </p>
      <div class="fg2" style="margin-top:6pt;">
        <div><div class="f-label">CMMS Entry Date</div><div class="f-value">&nbsp;</div></div>
        <div><div class="f-label">CMMS Entry By</div><div class="f-value">&nbsp;</div></div>
      </div>
    </div>
  </div>

  <div class="doc-footer">
    <span>CBU-CMMS &middot; Calibration Certificate &middot; ISO/IEC 17025:2017 &middot; Rev. 1</span>
    <span>{cert_number}</span>
    <span>Printed: {generated}</span>
  </div>
"""
    return _html_wrap(f"Calibration Certificate {cert_number}", body)
