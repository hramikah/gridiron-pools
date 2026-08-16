"""Primary brand colour for each NFL club, used to tint the pick buttons.

Keyed by nickname, because game rows carry the full name ("Denver Broncos")
while the Team table stores city and nickname separately.

Each colour is paired with an ink colour chosen by luminance rather than by
eye: a few clubs (Chargers, Dolphins, Rams, Vikings' gold, Steelers' gold)
are light enough that white lettering on them drops below the 4.5:1 needed
for small text, so those get near-black ink instead. ``text_on_white`` is the
colour used for the *unselected* button label -- the brand colour darkened
where it would otherwise be too pale to read on a white button.
"""

PRIMARY = {
    "Cardinals": "#97233F",
    "Falcons": "#A71930",
    "Ravens": "#241773",
    "Bills": "#00338D",
    "Panthers": "#0085CA",
    "Bears": "#0B162A",
    "Bengals": "#FB4F14",
    "Browns": "#311D00",
    "Cowboys": "#041E42",
    "Broncos": "#FB4F14",
    "Lions": "#0076B6",
    "Packers": "#203731",
    "Texans": "#03202F",
    "Colts": "#002C5F",
    "Jaguars": "#006778",
    "Chiefs": "#E31837",
    "Raiders": "#000000",
    "Chargers": "#0080C6",
    "Rams": "#003594",
    "Dolphins": "#008E97",
    "Vikings": "#4F2683",
    "Patriots": "#002244",
    "Saints": "#D3BC8D",
    "Giants": "#0B2265",
    "Jets": "#125740",
    "Eagles": "#004C54",
    "Steelers": "#FFB612",
    "49ers": "#AA0000",
    "Seahawks": "#002244",
    "Buccaneers": "#D50A0A",
    "Titans": "#0C2340",
    "Commanders": "#5A1414",
}


def _luminance(hex_colour):
    hex_colour = hex_colour.lstrip("#")
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(fg, bg):
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _darken(hex_colour, factor):
    hex_colour = hex_colour.lstrip("#")
    rgb = [int(hex_colour[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02x%02x%02x" % tuple(int(c * factor) for c in rgb)


def _readable_on_white(colour):
    """Darken a brand colour until it reads on a white button (4.5:1)."""
    candidate = colour
    factor = 1.0
    while _contrast(candidate, "#ffffff") < 4.5 and factor > 0.2:
        factor -= 0.1
        candidate = _darken(colour, factor)
    return candidate


def _nickname(team_name):
    """'Denver Broncos' -> 'Broncos'. Returns None for college sides."""
    if not team_name:
        return None
    last = team_name.split()[-1]
    return last if last in PRIMARY else None


def team_style(team_name):
    """(fill, ink, label) for a team, or None when we have no brand colour --
    college opponents, or a name we don't recognise, fall back to the default
    pool styling rather than guessing."""
    nick = _nickname(team_name)
    if nick is None:
        return None
    fill = PRIMARY[nick]
    ink = "#ffffff" if _contrast("#ffffff", fill) >= 4.5 else "#111111"
    # A handful of mid-tone brand colours (the Chargers' powder blue) are too
    # dark for black lettering and too light for white. Deepen the fill until
    # white clears 4.5:1 rather than shipping a button nobody can read.
    if _contrast(ink, fill) < 4.5:
        factor = 1.0
        while _contrast("#ffffff", fill) < 4.5 and factor > 0.2:
            factor -= 0.1
            fill = _darken(PRIMARY[nick], factor)
        ink = "#ffffff"
    return {"fill": fill, "ink": ink, "label": _readable_on_white(PRIMARY[nick])}


def styles_for(team_names):
    """Map of name -> style for every recognised team in the list."""
    out = {}
    for name in team_names:
        style = team_style(name)
        if style:
            out[name] = style
    return out
