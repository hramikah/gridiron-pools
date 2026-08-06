"""Build a printable PDF of everyone's picks for a week, across all three
pools. Only meant to be generated once that week's pick deadline has
passed (enforced by the route, not here) so nobody can see picks early.
"""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from models import Entry, GridironMiss, Pick

STYLES = getSampleStyleSheet()
CELL_STYLE = ParagraphStyle("cell", parent=STYLES["Normal"], fontSize=9, leading=12)


def _section_table(rows, col_widths):
    data = [[Paragraph(f"<b>{c}</b>", CELL_STYLE) for c in rows[0]]] + [
        [Paragraph(str(c), CELL_STYLE) for c in row] for row in rows[1:]
    ]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#212529")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6f8")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return table


def _dropdead_rows(week, season_year):
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).order_by(Entry.id).all()
    rows = [["Player", "Week Pick", "Status"]]
    for e in sorted(entries, key=lambda e: e.user.username.lower()):
        pick = next((p for p in e.picks if p.week_id == week.id), None)
        pick_text = escape(f"{pick.team.city} {pick.team.name}") if pick and pick.team else "NO PICK"
        status = "Alive" if e.is_active else f"Eliminated (Wk {e.eliminated_week})"
        rows.append([escape(e.user.username), pick_text, status])
    return rows


def _loser_rows(week, season_year):
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).order_by(Entry.id).all()
    rows = [["Player", "Week Pick (to LOSE)", "Season Points"]]
    for e in sorted(entries, key=lambda e: e.user.username.lower()):
        pick = next((p for p in e.picks if p.week_id == week.id), None)
        pick_text = escape(f"{pick.team.city} {pick.team.name}") if pick and pick.team else "NO PICK"
        total = sum(p.points or 0 for p in e.picks)
        rows.append([escape(e.user.username), pick_text, str(total)])
    return rows


def _gridiron_rows(week, season_year):
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).order_by(Entry.id).all()
    rows = [["Player", "Week Picks"]]
    for e in sorted(entries, key=lambda e: e.user.username.lower()):
        week_picks = [p for p in e.picks if p.week_id == week.id]
        if not week_picks:
            missed = GridironMiss.query.filter_by(entry_id=e.id, week_id=week.id).first()
            note = "MISSED WEEK -- scored 0-5" if missed else "NO PICKS"
            rows.append([escape(e.user.username), note])
            continue
        lines = []
        for p in week_picks:
            game = p.game
            away, home = escape(game.away_team), escape(game.home_team)
            if p.market == "spread":
                team = escape(game.home_team if p.side == "home" else game.away_team)
                lines.append(f"{away} @ {home}: {team} (spread)")
            elif p.market == "total":
                lines.append(f"{away} @ {home}: {p.side.capitalize()} {game.over_under}")
        rows.append([escape(e.user.username), "<br/>".join(lines)])
    return rows


def build_week_pdf(week):
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
    )
    season_year = week.season_year
    story = []

    title_style = ParagraphStyle("title", parent=STYLES["Title"], fontSize=18)
    sub_style = ParagraphStyle("sub", parent=STYLES["Normal"], textColor=colors.grey)
    heading_style = ParagraphStyle("heading", parent=STYLES["Heading2"], spaceBefore=16, spaceAfter=6)

    pool_labels = {"dropdead": "Drop Dead Pool", "loser": "Loser Pool", "gridiron": "Gridiron Investments"}
    story.append(
        Paragraph(
            f"Gridiron Pools &mdash; {pool_labels.get(week.pool, '')} Week {week.number} Picks ({season_year})",
            title_style,
        )
    )
    story.append(
        Paragraph(
            f"Pick deadline: {week.pick_deadline.strftime('%A %b %d, %Y %I:%M %p')} Eastern &mdash; all picks below are locked in.",
            sub_style,
        )
    )
    story.append(Spacer(1, 12))

    # Weeks are per-pool: a week PDF shows only its own pool's picks.
    if week.pool == "dropdead":
        story.append(Paragraph("Drop Dead Pool", heading_style))
        story.append(_section_table(_dropdead_rows(week, season_year), [2 * inch, 3 * inch, 1.8 * inch]))
    elif week.pool == "loser":
        story.append(Paragraph("Loser Pool", heading_style))
        story.append(_section_table(_loser_rows(week, season_year), [2 * inch, 3.2 * inch, 1.6 * inch]))
    elif week.pool == "gridiron":
        story.append(Paragraph("Gridiron Investments", heading_style))
        story.append(_section_table(_gridiron_rows(week, season_year), [1.8 * inch, 5 * inch]))

    doc.build(story)
    buf.seek(0)
    return buf
