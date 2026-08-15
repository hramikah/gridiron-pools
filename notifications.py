"""Builds and sends each player's pick recap email once a week's deadline
has passed. Kept separate from scoring.py (win/loss math) and mailer.py
(the actual send/log mechanics)."""

from models import Entry, User, Week, db
from mailer import send_picks_recap_email

POOL_LABELS_LOCAL = {"dropdead": "Drop Dead Pool", "loser": "Loser Pool", "gridiron": "Gridiron Investments"}


def _entry_week_pick_lines(pool_name, entry, week):
    lines = []
    for p in [p for p in entry.picks if p.week_id == week.id]:
        if pool_name in ("dropdead", "loser"):
            team = f"{p.team.city} {p.team.name}" if p.team else "?"
            lines.append(f"  - {team}")
        elif p.market == "spread":
            team = p.game.home_team if p.side == "home" else p.game.away_team
            lines.append(f"  - {p.game.away_team} @ {p.game.home_team}: {team} (spread)")
        else:
            lines.append(f"  - {p.game.away_team} @ {p.game.home_team}: {p.side.capitalize()} {p.game.over_under}")
    return lines


def _user_week_recap_text(user, week):
    lines = []
    for pool_name, label in POOL_LABELS_LOCAL.items():
        entries = Entry.query.filter_by(pool=pool_name, season_year=week.season_year, user_id=user.id).all()
        for entry in entries:
            pick_lines = _entry_week_pick_lines(pool_name, entry, week)
            if not pick_lines:
                continue
            lines.append(f"{label} ({entry.label}):")
            lines.extend(pick_lines)
    return "\n".join(lines) if lines else "No picks were submitted this week."




def email_week_picks(week):
    """Send every user with at least one pick this week a recap email.
    Idempotent: does nothing if already emailed for this week."""
    if week.picks_emailed:
        return 0

    user_ids = {
        e.user_id
        for e in Entry.query.filter_by(season_year=week.season_year).all()
        if any(p.week_id == week.id for p in e.picks)
    }
    users = User.query.filter(User.id.in_(user_ids)).all()
    for user in users:
        recap = _user_week_recap_text(user, week)
        send_picks_recap_email(user, week, recap)

    week.picks_emailed = True
    db.session.commit()
    return len(users)
