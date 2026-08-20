from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

PASSWORD_RESET_LIFETIME = timedelta(hours=1)

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


# Preseason weeks live in the same season/pool space as the regular season,
# so their numbers are offset past any real week number (1-18) to keep the
# (season, number, pool) uniqueness from colliding once Week 1 is published.
# Preseason week N is stored as PRESEASON_OFFSET + N.
PRESEASON_OFFSET = 100


def default_buyback_open(pool, number, is_preseason=False):
    """What a freshly created week's buy-back window should start as: open
    for Drop Dead weeks 1-4 of a real season, matching the printed rules, so
    a normal season needs no admin action. Preseason and test weeks start
    closed and are opened by hand from the Pool Manager."""
    return pool == "dropdead" and not is_preseason and number <= 4


class Week(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    season_year = db.Column(db.Integer, nullable=False)
    number = db.Column(db.Integer, nullable=False)  # 1-18 regular season, 101+ preseason
    pool = db.Column(db.String(20), nullable=False)  # each pool keeps its own weeks + deadline
    pick_deadline = db.Column(db.DateTime, nullable=False)
    picks_emailed = db.Column(db.Boolean, default=False)  # picks recap sent to players
    missed_processed = db.Column(db.Boolean, default=False, nullable=False)  # missed-pick penalties applied (auto or manual)
    is_preseason = db.Column(db.Boolean, default=False, nullable=False)  # exhibition week, doesn't count toward standings
    # Drop Dead: may an entry eliminated in this week buy back in? The printed
    # rules say weeks 1-4, but week numbers don't line up during preseason
    # testing, so the admin sets it per week instead of it being derived.
    buyback_open = db.Column(db.Boolean, default=False, nullable=False)

    games = db.relationship("Game", backref="week", lazy=True, cascade="all, delete-orphan")
    picks = db.relationship("Pick", backref="week", lazy=True, cascade="all, delete-orphan")

    __table_args__ = (db.UniqueConstraint("season_year", "number", "pool", name="uq_week_season_number_pool"),)

    @property
    def display_number(self):
        """The number players see: preseason weeks count from 1 again."""
        return self.number - PRESEASON_OFFSET if self.is_preseason else self.number

    @property
    def label(self):
        return f"Preseason Week {self.display_number}" if self.is_preseason else f"Week {self.number}"

    def __repr__(self):
        return f"{self.label} ({self.season_year})"


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
    # Drop Dead: the week number the entry last bought back in, if ever.
    # Gridiron: the last week voided by a buy-back -- every week up to and
    # including this one stops counting toward the entry's record. Entries are
    # per-pool, so the two meanings never share a row.
    buyback_week = db.Column(db.Integer, nullable=True)
    # Retired: held the Gridiron "startover" election, from before the makeup
    # week became a single fixed allowance and the week-2 reset became a paid
    # buy-back. Left in place because dropping a column means rebuilding the
    # table in SQLite. Nothing reads it.
    makeup_choice = db.Column(db.String(10), nullable=True)
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
    """One message in a two-way conversation thread between a player and the
    admins. ``user_id`` always identifies the thread (the player it's
    about); ``sender_id`` is whoever actually wrote this particular message
    -- the player themselves, or whichever admin replied. ``is_read`` means
    "seen by the other side of the conversation": for a player-authored
    message that's the admins; for an admin-authored reply that's the
    player."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=now)
    is_read = db.Column(db.Boolean, default=False)

    user = db.relationship("User", foreign_keys=[user_id])
    sender = db.relationship("User", foreign_keys=[sender_id])

    @property
    def from_admin(self):
        return self.sender_id != self.user_id


class ActivityLog(db.Model):
    """Audit trail of what a player did while logged in: sign-ins, picks saved
    and removed, pool joins, password changes, messages -- anything an admin
    might need to reconstruct later when someone disputes a pick.

    ``detail`` is a plain human-readable sentence rather than structured data,
    because this is read by people, not queried by code. Rows are never edited
    or deleted by the app; they're append-only.
    """

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    username = db.Column(db.String(80))  # kept verbatim so a deleted account still reads sensibly
    action = db.Column(db.String(40), nullable=False)  # 'login', 'pick_saved', ...
    pool = db.Column(db.String(20))
    detail = db.Column(db.Text)
    ip = db.Column(db.String(64))
    created_at = db.Column(db.DateTime, default=now, index=True)

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


class PasswordReset(db.Model):
    """A single-use, time-limited token from the "forgot password" form.
    Tied to one account, not one email: several accounts may share an
    email (one per entry), so each gets its own link."""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    token = db.Column(db.String(64), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=now)
    used_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User")

    def is_valid(self):
        return self.used_at is None and now() - self.created_at < PASSWORD_RESET_LIFETIME
