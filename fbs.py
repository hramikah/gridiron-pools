"""Which college teams count as FBS.

The odds feed for `americanfootball_ncaaf` carries every Division I college
game it can price, which means FBS-vs-FCS body-bag games come back alongside
the real slate -- 85 games in Week 1 of 2026, most of them 30-to-56-point
mismatches. The house rule is **FBS vs FBS only**, so the publisher asks this
module about both teams and skips the game unless both are on the list.

A whitelist can only be wrong by being out of date, and the failure mode of an
out-of-date whitelist is a *missing* game, which is the quiet kind. So
`is_fbs()` is paired with `unrecognized()`: every college name the filter
rejects is reported by the publisher and written to the log, and
`check-fbs-filter.command` prints the same report against the live feed
without touching the database. If a real FBS school shows up there, add its
name (or an alias) below.

Matching is on a normalized form -- lowercased, accents and punctuation
stripped -- so "Hawai'i Rainbow Warriors" and "Hawaii Rainbow Warriors" are
the same team.
"""

import re
import unicodedata

# 2026 alignment. School + mascot, spelled the way the odds feed spells it.
FBS_TEAMS = [
    # ACC
    "Boston College Eagles", "California Golden Bears", "Clemson Tigers",
    "Duke Blue Devils", "Florida State Seminoles", "Georgia Tech Yellow Jackets",
    "Louisville Cardinals", "Miami Hurricanes", "NC State Wolfpack",
    "North Carolina Tar Heels", "Pittsburgh Panthers", "SMU Mustangs",
    "Stanford Cardinal", "Syracuse Orange", "Virginia Cavaliers",
    "Virginia Tech Hokies", "Wake Forest Demon Deacons",
    # Big Ten
    "Illinois Fighting Illini", "Indiana Hoosiers", "Iowa Hawkeyes",
    "Maryland Terrapins", "Michigan Wolverines", "Michigan State Spartans",
    "Minnesota Golden Gophers", "Nebraska Cornhuskers", "Northwestern Wildcats",
    "Ohio State Buckeyes", "Oregon Ducks", "Penn State Nittany Lions",
    "Purdue Boilermakers", "Rutgers Scarlet Knights", "UCLA Bruins",
    "USC Trojans", "Washington Huskies", "Wisconsin Badgers",
    # Big 12
    "Arizona Wildcats", "Arizona State Sun Devils", "Baylor Bears",
    "BYU Cougars", "Cincinnati Bearcats", "Colorado Buffaloes",
    "Houston Cougars", "Iowa State Cyclones", "Kansas Jayhawks",
    "Kansas State Wildcats", "Oklahoma State Cowboys", "TCU Horned Frogs",
    "Texas Tech Red Raiders", "UCF Knights", "Utah Utes",
    "West Virginia Mountaineers",
    # SEC
    "Alabama Crimson Tide", "Arkansas Razorbacks", "Auburn Tigers",
    "Florida Gators", "Georgia Bulldogs", "Kentucky Wildcats", "LSU Tigers",
    "Mississippi State Bulldogs", "Missouri Tigers", "Oklahoma Sooners",
    "Ole Miss Rebels", "South Carolina Gamecocks", "Tennessee Volunteers",
    "Texas Longhorns", "Texas A&M Aggies", "Vanderbilt Commodores",
    # Pac-12
    "Boise State Broncos", "Colorado State Rams", "Fresno State Bulldogs",
    "Oregon State Beavers", "San Diego State Aztecs", "Texas State Bobcats",
    "Utah State Aggies", "Washington State Cougars",
    # Mountain West
    "Air Force Falcons", "Hawaii Rainbow Warriors", "Nevada Wolf Pack",
    "New Mexico Lobos", "Northern Illinois Huskies", "San Jose State Spartans",
    "UNLV Rebels", "UTEP Miners", "Wyoming Cowboys",
    # American
    "Army Black Knights", "Charlotte 49ers", "East Carolina Pirates",
    "Florida Atlantic Owls", "Memphis Tigers", "Navy Midshipmen",
    "North Texas Mean Green", "Rice Owls", "South Florida Bulls",
    "Temple Owls", "Tulane Green Wave", "Tulsa Golden Hurricane",
    "UAB Blazers", "UTSA Roadrunners",
    # Conference USA
    "Delaware Fightin Blue Hens", "Florida International Panthers",
    "Jacksonville State Gamecocks", "Kennesaw State Owls", "Liberty Flames",
    "Louisiana Tech Bulldogs", "Middle Tennessee Blue Raiders",
    "Missouri State Bears", "New Mexico State Aggies", "Sam Houston Bearkats",
    "Western Kentucky Hilltoppers",
    # MAC
    "Akron Zips", "Ball State Cardinals", "Bowling Green Falcons",
    "Buffalo Bulls", "Central Michigan Chippewas", "Eastern Michigan Eagles",
    "Kent State Golden Flashes", "Miami (OH) RedHawks", "Ohio Bobcats",
    "Toledo Rockets", "UMass Minutemen", "Western Michigan Broncos",
    # Sun Belt
    "Appalachian State Mountaineers", "Arkansas State Red Wolves",
    "Coastal Carolina Chanticleers", "Georgia Southern Eagles",
    "Georgia State Panthers", "James Madison Dukes",
    "Louisiana Ragin Cajuns", "Louisiana Monroe Warhawks",
    "Marshall Thundering Herd", "Old Dominion Monarchs",
    "South Alabama Jaguars", "Southern Miss Golden Eagles", "Troy Trojans",
    # Independents
    "Notre Dame Fighting Irish", "UConn Huskies",
]

# Other spellings the feed has been known to use, or that a book might.
# Left side is what may arrive; it maps onto a team already listed above.
ALIASES = [
    "Miami (FL) Hurricanes", "Miami Florida Hurricanes",
    "Miami Ohio RedHawks", "Miami-Ohio RedHawks",
    "North Carolina State Wolfpack",
    "Southern California Trojans", "Southern Methodist Mustangs",
    "Texas Christian Horned Frogs", "Brigham Young Cougars",
    "Louisiana State Tigers", "Mississippi Rebels",
    "Central Florida Knights", "Alabama-Birmingham Blazers",
    "Texas-San Antonio Roadrunners", "Texas-El Paso Miners",
    "Nevada-Las Vegas Rebels", "Massachusetts Minutemen",
    "Connecticut Huskies", "Louisiana-Lafayette Ragin Cajuns",
    "Louisiana Lafayette Ragin Cajuns", "UL Monroe Warhawks",
    "Louisiana-Monroe Warhawks", "Southern Mississippi Golden Eagles",
    "San Jose St Spartans", "Hawai'i Rainbow Warriors",
    "Florida Intl Panthers", "FIU Panthers",
    "Sam Houston State Bearkats", "Appalachian St Mountaineers",
]


def _norm(name):
    """Lowercase, strip accents and anything that isn't a letter or digit."""
    decomposed = unicodedata.normalize("NFKD", name or "")
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", ascii_only.lower()).strip()


_ALLOWED = {_norm(n) for n in FBS_TEAMS} | {_norm(n) for n in ALIASES}
_ALLOWED.discard("")


def is_fbs(team_name):
    """True if this college team plays in FBS (or is a known alias of one)."""
    return _norm(team_name) in _ALLOWED


def unrecognized(team_names):
    """The subset of `team_names` the filter does not recognize, sorted.

    Every one of these is a game the publisher skipped. Most will be FCS, as
    intended; an FBS school in this list means the whitelist needs its name.
    """
    return sorted({n for n in team_names if n and not is_fbs(n)})
