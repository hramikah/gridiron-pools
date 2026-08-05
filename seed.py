"""One-time data seed: 32 NFL teams, default Loser Pool point values, and an
admin account. Safe to run multiple times (idempotent)."""

from app import app
from models import LoserPoolPoints, Team, User, db

NFL_TEAMS = [
    ("Arizona", "Cardinals"),
    ("Atlanta", "Falcons"),
    ("Baltimore", "Ravens"),
    ("Buffalo", "Bills"),
    ("Carolina", "Panthers"),
    ("Chicago", "Bears"),
    ("Cincinnati", "Bengals"),
    ("Cleveland", "Browns"),
    ("Dallas", "Cowboys"),
    ("Denver", "Broncos"),
    ("Detroit", "Lions"),
    ("Green Bay", "Packers"),
    ("Houston", "Texans"),
    ("Indianapolis", "Colts"),
    ("Jacksonville", "Jaguars"),
    ("Kansas City", "Chiefs"),
    ("Las Vegas", "Raiders"),
    ("Los Angeles", "Chargers"),
    ("Los Angeles", "Rams"),
    ("Miami", "Dolphins"),
    ("Minnesota", "Vikings"),
    ("New England", "Patriots"),
    ("New Orleans", "Saints"),
    ("New York", "Giants"),
    ("New York", "Jets"),
    ("Philadelphia", "Eagles"),
    ("Pittsburgh", "Steelers"),
    ("San Francisco", "49ers"),
    ("Seattle", "Seahawks"),
    ("Tampa Bay", "Buccaneers"),
    ("Tennessee", "Titans"),
    ("Washington", "Commanders"),
]

# Loser Pool point values, as printed on the 2026 rules sheet (points 10-41).
# These stay fixed "all season long" per the rules; re-run per season if
# the commissioners reassign values.
LOSER_POINTS = {
    "Cardinals": 10,
    "Dolphins": 11,
    "Jets": 12,
    "Raiders": 13,
    "Browns": 14,
    "Titans": 15,
    "Falcons": 16,
    "Panthers": 17,
    "Saints": 18,
    "Giants": 19,
    "Commanders": 20,
    "Colts": 21,
    "Vikings": 22,
    "Steelers": 23,
    "Buccaneers": 24,
    "Jaguars": 25,
    "Cowboys": 26,
    "Bears": 27,
    "Bengals": 28,
    "Broncos": 29,
    "Lions": 30,
    "Texans": 31,
    "49ers": 32,
    "Packers": 33,
    "Chargers": 34,
    "Patriots": 35,
    "Eagles": 36,
    "Chiefs": 37,
    "Seahawks": 38,
    "Ravens": 39,
    "Bills": 40,
    "Rams": 41,
}


def seed():
    with app.app_context():
        db.create_all()

        for city, name in NFL_TEAMS:
            if not Team.query.filter_by(name=name).first():
                db.session.add(Team(name=name, city=city))
        db.session.commit()

        season_year = app.config["CURRENT_SEASON"]
        for name, pts in LOSER_POINTS.items():
            team = Team.query.filter_by(name=name).first()
            if not team:
                continue
            existing = LoserPoolPoints.query.filter_by(season_year=season_year, team_id=team.id).first()
            if not existing:
                db.session.add(LoserPoolPoints(season_year=season_year, team_id=team.id, points=pts))
        db.session.commit()

        if User.query.filter_by(username="admin").first() is None:
            admin = User(username="admin", email="admin@example.com", is_admin=True)
            admin.set_password("changeme123")
            db.session.add(admin)
            db.session.commit()
            print("Created admin user: username=admin password=changeme123 (change this!)")

        print(f"Seeded {Team.query.count()} teams and {LoserPoolPoints.query.count()} loser-pool point rows.")


if __name__ == "__main__":
    seed()
