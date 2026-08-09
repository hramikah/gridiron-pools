from datetime import datetime
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

POOLS = ("dropdead", "loser", "gridiron")
POOL_LABELS = {
    "dropdead": "Drop Dead Pool",
    "loser": "Loser Pool",
    "gridiron": "Gridiron Investments",
}


def now():
    # Naive Eastern time throughout the app -- the pool rules peg every
    # deadline to Eastern time explicitly ("Saturday at noon EST").
    return datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), nullable=False)  # not unique: one player may run several accounts (one per entry) sharing an email
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    max_teams = db.Column(db.Integer, default=1, nullable=False)  # admin-set cap on accounts sharing this email
    created_at = db.Column(db.DateTime, default=now)

    entries = db.relationship("Entry", backref="user", lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method="pbkdf2:sha256")

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Setting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255))


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)  # nickname, e.g. "Steelers"
    city = db.Column(db.String(50))  # e.g. "Pittsburgh"

    def __repr__(self):
        return f"{self.city} {self.name}".strip()


class LoserPoolPoints(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    season_year = db.Column(db.Integer, nullable=False)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False)
    points = db.Column(db.Integer, nullable=False)

    team = db.relationship("Team")

    __table_args__ = (db.UniqueConstraint("season_year", "team_id", name="uq_loser_points_season_team"),)


class Week(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    season_year = db.Column(db.Integer, nullable=False)
    number = db.Column(db.Integer, nullable=False)  # 1-18 regular season
    pool = db.Column(db.String(20), nullable=False)  # each pool keeps its own weeks + deadline
    pick_deadline = db.Column(db.DateTime, nullable=False)
    picks_emailed = db.Column(db.Boolean, default=False)  # picks recap sent to players
    missed_processed = db.Column(db.Boolean, default=False, nullable=False)  # missed-pick penalties applied (auto or manual)

    games = db.relationship("Game", backref="week", lazy=True, cascade="all, delete-orphan")
    picks = db.relationship("Pick", backref="week", lazy=True, cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("season_year", "number", "pool", name="uq_week_season_number_pool"),)

    def __repr__(self):
        return f"Week {self.number} ({self.season_year})"


class Game(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    week_id = db.Column(db.Integer, db.ForeignKey("week.id"), nullable=False)
    pool = db.Column(db.String(20), default="gridiron", nullable=False)  # which pool's line: 'dropdead' / 'loser' / 'gridiron'
    sport = db.Column(db.String(10), default="nfl", nullable=False)  # 'nfl' or 'college'

    home_team = db.Column(db.String(80), nullable=False)
    away_team = db.Column(db.String(80), nullable=False)
    home_team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)
    away_team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)

    favorite = db.Column(db.String(10))  # 'home' or 'away' or None (pick'em)
    spread = db.Column(db.Float)  # points the favorite is favored by
    over_under = db.Column(db.Float, nullable=True)

    kickoff = db.Column(db.DateTime, nullable=True)
    is_mnf = db.Column(db.Boolean, default=False)  # Monday Night Football game (loser pool auto-pick default)

    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)
    is_final = db.Column(db.Boolean, default=False)

    home_team_obj = db.relationship("Team", foreign_keys=[home_team_id])
    away_team_obj = db.relationship("Team", foreign_keys=[away_team_id])

    @property
    def winner(self):
        if not self.is_final or self.home_score is None or self.away_score is None:
            return None
        if self.home_score > self.away_score:
            return "home"
        if self.away_score > self.home_score:
            return "away"
        return "tie"

    @property
    def label(self):
        return f"{self.away_team} @ {self.home_team}"


class Entry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    pool = db.Column(db.String(20), nullable=False)
    label = db.Column(db.String(50), default="Entry 1")
    season_year = db.Column(db.Integer, nullable=False)

    is_active = db.Column(db.Boolean, default=True)  # drop dead: still alive; gridiron: not benched for missing 5+ weeks
    eliminated_week = db.Column(db.Integer, nullable=True)
    buy_backs_used = db.Column(db.Integer, default=0)
    buyback_week = db.Column(db.Integer, nullable=True)  # drop dead: week number the entry last bought back in, if ever
    paid = db.Column(db.Boolean, default=False)  # entry-fee paid, admin-only visibility

    created_at = db.Column(db.DateTime, default=now)

    picks = db.relationship("Pick", backref="entry", lazy=True, cascade="all, delete-orphan")
    gridiron_misses = db.relationship("GridironMiss", backref="entry", lazy=True, cascade="all, delete-orphan")

    def used_team_ids(self):
        return {p.team_id for p in self.picks if p.team_id is not None}


class GridironMiss(db.Model):
    """Records a week a Gridiron entry failed to submit any picks: it's
    scored as 0-5 for that week, and unlocks an 8-pick week immediately
    after. 5+ missed weeks benches the entry (Entry.is_active = False)."""

    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("entry.id"), nullable=False)
    week_id = db.Column(db.Integer, db.ForeignKey("week.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=now)

    week = db.relationship("Week")

    __table_args__ = (db.UniqueConstraint("entry_id", "week_id", name="uq_gridiron_miss_entry_week"),)


class Pick(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entry_id = db.Column(db.Integer, db.ForeignKey("entry.id"), nullable=False)
    week_id = db.Column(db.Integer, db.ForeignKey("week.id"), nullable=False)
    pool = db.Column(db.String(20), nullable=False)

    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=True)  # dropdead / loser
    game_id = db.Column(db.Integer, db.ForeignKey("game.id"), nullable=True)  # gridiron
    market = db.Column(db.String(10), nullable=True)  # gridiron: 'spread' or 'total'
    side = db.Column(db.String(10), nullable=True)  # gridiron: 'home'/'away' or 'over'/'under'

    result = db.Column(db.String(10), default="pending")  # pending/win/loss/push
    points = db.Column(db.Float, default=0)

    created_at = db.Column(db.DateTime, default=now)

    team = db.relationship("Team")
    game = db.relationship("Game")


class Announcement(db.Model):
    """Admin-posted notice shown to every user on the message board."""

    id = db.Column(db.Integer, primary_key=True)
    author_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=now)

    author = db.relationship("User")


class ContactMessage(db.Model):
    """A message a player sends to the admin (one-way, admin-only inbox)."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=now)
    is_read = db.Column(db.Boolean, default=False)

    user = db.relationship("User")


class Invite(db.Model):
    """An admin-sent invite: registration is only allowed via a valid,
    unused token tied to the invited email, so the site can't be joined by
    anyone who just finds the URL."""

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), nullable=False)
    token = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now)
    used_at = db.Column(db.DateTime, nullable=True)
