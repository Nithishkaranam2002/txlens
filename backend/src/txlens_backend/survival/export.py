"""CSV and PDF exports of a survival report, built only from the suppressed Report.

Both carry the synthetic-data label (the same text as the app's banner): the CSV in its first
row, the PDF at the top and in every page footer.
"""

import csv
import io
from collections.abc import Iterator, Sequence

import matplotlib

matplotlib.use("Agg")  # no display: render to memory
import matplotlib.pyplot as plt
from matplotlib.figure import Figure
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus.flowables import Flowable

from txlens_backend.survival.analysis import Report
from txlens_backend.survival.suppression import ShownCurve

SYNTHETIC_LABEL = "Synthetic data. Clinical decision support only, not diagnosis."
DAYS_PER_MONTH = 30.4375
Value = str | int | float | None
Row = tuple[str, str, Value, str, Value]  # section, group, day, metric, value


def about(report: Report) -> Iterator[Row]:
    """What was analysed."""
    yield ("about", "", None, "label", SYNTHETIC_LABEL)
    facts: list[tuple[str, Value]] = [
        ("population", report.population),
        ("grouping", report.grouping_label),
        ("landmark_days", report.landmark_days),
        ("patients", report.n),
        ("graft_losses", report.events),
        ("software", report.software),
    ]
    for metric, value in facts:
        yield ("about", "", None, metric, value)


def curve_rows(section: str, curves: list[ShownCurve]) -> Iterator[Row]:
    """Displayed curve steps (or why a curve is hidden)."""
    for c in curves:
        if c.hidden_reason:
            yield (section, c.group, None, "hidden", c.hidden_reason)
        for p in c.points:
            yield (section, c.group, p.day, "estimate", p.estimate)
            yield (section, c.group, p.day, "lower", p.lower)
            yield (section, c.group, p.day, "upper", p.upper)


def table_rows(report: Report) -> Iterator[Row]:
    """Risk table, log-rank test, incidence at 1-3 years and the Cox model."""
    for row in report.risk_table:
        for cell in row.cells:
            yield ("risk_table", row.group, cell.day, "at_risk", cell.n_risk)
            yield ("risk_table", row.group, cell.day, "graft_losses_in_interval", cell.events)
    if report.log_rank:
        lr = report.log_rank
        yield ("log_rank", "", None, "chi2", lr.chi2)
        yield ("log_rank", "", None, "df", lr.df)
        yield ("log_rank", "", None, "p_value", lr.p_value)
        for group, observed in lr.observed.items():
            yield ("log_rank", group, None, "observed", observed)
            yield ("log_rank", group, None, "expected", lr.expected[group])
    for i in report.incidence_at:
        yield ("incidence_at", i.group, i.day, "as_of_curve_step_day", i.step_day)
        yield ("incidence_at", i.group, i.day, "cumulative_incidence", i.cumulative_incidence)
        yield ("incidence_at", i.group, i.day, "one_minus_km", i.one_minus_km)
    if report.cox:
        yield ("cox", "", None, "landmark_days", report.cox.landmark_days)
        for t in report.cox.terms:
            terms: list[tuple[str, Value]] = [
                ("hazard_ratio", t.hazard_ratio),
                ("lower", t.lower),
                ("upper", t.upper),
                ("p_value", t.p_value),
                ("ph_p_value", t.ph_p_value),
                ("n_exposed", t.n_exposed),
                ("events_exposed", t.events_exposed),
            ]
            for metric, value in terms:
                yield ("cox", t.label, None, metric, value)


def rows(report: Report) -> Iterator[Row]:
    """The whole report as long-format rows."""
    yield from about(report)
    yield from curve_rows("kaplan_meier", report.km)
    yield from curve_rows("cumulative_incidence", report.incidence)
    yield from curve_rows("one_minus_km", report.one_minus_km)
    yield from table_rows(report)
    yield ("methods", "", None, "text", report.methods)
    for w in report.warnings:
        yield ("warning", "", None, "text", w)


def to_csv(report: Report) -> str:
    """CSV text: section, group, day, metric, value (empty for none)."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["section", "group", "day", "metric", "value"])
    for row in rows(report):
        writer.writerow(["" if v is None else v for v in row])
    return out.getvalue()


def months(curve: ShownCurve) -> list[float]:
    return [p.day / DAYS_PER_MONTH for p in curve.points]


def step_figure(
    curves: list[ShownCurve], title: str, ylabel: str, dashed: Sequence[ShownCurve] = ()
) -> Image:
    """Step curves with CI bands (and dashed comparison curves) as an image for the PDF."""
    fig, ax = plt.subplots(figsize=(7, 3.4), dpi=150)
    for c in curves:
        if not c.points:
            continue
        estimate = [p.estimate for p in c.points]
        line = ax.step(months(c), estimate, where="post", label=c.group)[0]
        lower = [p.estimate if p.lower is None else p.lower for p in c.points]
        upper = [p.estimate if p.upper is None else p.upper for p in c.points]
        ax.fill_between(months(c), lower, upper, step="post", alpha=0.15, color=line.get_color())
    for c in dashed:
        if c.points:
            estimate = [p.estimate for p in c.points]
            ax.step(months(c), estimate, where="post", linestyle="--", label=f"{c.group} (1 - KM)")
    ax.set(title=title, xlabel="Months since transplant", ylabel=ylabel)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=7)
    return figure_image(fig)


def forest_figure(report: Report) -> Image | None:
    """Hazard ratios with 95% CIs on a log axis."""
    if not report.cox or not report.cox.terms:
        return None
    terms = report.cox.terms
    fig, ax = plt.subplots(figsize=(7, 0.45 * len(terms) + 1), dpi=150)
    y = list(range(len(terms)))[::-1]
    below = [t.hazard_ratio - t.lower for t in terms]
    above = [t.upper - t.hazard_ratio for t in terms]
    ax.errorbar([t.hazard_ratio for t in terms], y, xerr=[below, above], fmt="o")
    ax.axvline(1, color="grey", linestyle="--")
    ax.set_xscale("log")
    labels = [t.label + (" (PH?)" if t.ph_violated else "") for t in terms]
    ax.set_yticks(y, labels, fontsize=7)
    ax.set_xlabel("Hazard ratio (95% CI), log scale")
    return figure_image(fig)


def figure_image(fig: Figure) -> Image:
    """A matplotlib figure as a reportlab image, 16 cm wide."""
    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png")
    plt.close(fig)
    buffer.seek(0)
    width, height = fig.get_size_inches()
    return Image(buffer, width=16 * cm, height=16 * cm * height / width)


def table(data: Sequence[Sequence[object]]) -> Table:
    """A small grid table with a header row."""
    t = Table([[str(v) for v in row] for row in data], hAlign="LEFT")
    style = [
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
    ]
    t.setStyle(TableStyle(style))
    return t


def three(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def cox_part(report: Report, p: object) -> list[Flowable]:
    """Forest plot and hazard-ratio table."""
    forest = forest_figure(report)
    if forest is None or report.cox is None:
        return []
    cox = report.cox
    heading = (
        f"Cox model (from day {cox.landmark_days}; {cox.n} patients, {cox.events} graft losses)"
    )
    header = ["Covariate", "HR", "95% CI", "p", "PH test p"]
    body = [
        [t.label, f"{t.hazard_ratio:.2f}", f"{t.lower:.2f}-{t.upper:.2f}", f"{t.p_value:.3f}",
         f"{t.ph_p_value:.3f}"]
        for t in cox.terms
    ]  # fmt: skip
    return [Paragraph(heading, p), forest, table([header, *body])]


def story(report: Report) -> list[Flowable]:
    """The PDF's content, top to bottom."""
    styles = getSampleStyleSheet()
    p = styles["BodyText"]
    parts: list[Flowable] = [
        Paragraph("Graft survival analysis", styles["Title"]),
        Paragraph(f"<b>{SYNTHETIC_LABEL}</b>", p),
        Paragraph(
            f"Population: {report.population}. Grouping: {report.grouping_label}. Follow-up "
            f"from day {report.landmark_days}. Patients: {report.n}; graft losses: "
            f"{report.events}.",
            p,
        ),
        step_figure(report.km, "Death-censored graft survival (Kaplan-Meier, 95% CI)", "Survival"),
    ]
    if report.risk_table:
        days = [f"day {c.day}" for c in report.risk_table[0].cells]
        body = [
            [r.group, *(f"{c.n_risk} ({c.events})" for c in r.cells)] for r in report.risk_table
        ]
        parts += [
            Paragraph("Number at risk (graft losses since the previous column)", p),
            table([["Group", *days], *body]),
        ]
    if report.log_rank:
        lr = report.log_rank
        text = f"Log-rank test: chi-square {lr.chi2:.2f} on {lr.df} df, p = {lr.p_value:.4f}."
        parts.append(Paragraph(text, p))
    title = "Cumulative incidence of graft loss (death competing) vs 1 - KM"
    parts.append(step_figure(report.incidence, title, "Cumulative incidence", report.one_minus_km))
    if report.incidence_at:
        incidence = [
            [i.group, str(i.day), three(i.cumulative_incidence), three(i.one_minus_km),
             "-" if i.step_day is None else f"{i.step_day:.0f}"]
            for i in report.incidence_at
        ]  # fmt: skip
        header = ["Group", "Day", "Cumulative incidence", "1 - KM", "As of curve step (day)"]
        parts.append(table([header, *incidence]))
    parts += cox_part(report, p)
    parts += [Spacer(1, 0.3 * cm), Paragraph("<b>Methods</b>", p), Paragraph(report.methods, p)]
    if report.warnings:
        parts += [Paragraph("<b>Notes</b>", p), *(Paragraph(w, p) for w in report.warnings)]
    return parts


def footer(canvas: Canvas, doc: SimpleDocTemplate) -> None:
    """The synthetic-data label and page number on every page."""
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.drawString(1.5 * cm, 1 * cm, f"TxLens - {SYNTHETIC_LABEL} - page {doc.page}")
    canvas.restoreState()


def to_pdf(report: Report) -> bytes:
    """The report as an A4 PDF."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title="Graft survival analysis (synthetic data)",
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
    )
    doc.build(story(report), onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
