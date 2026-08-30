"""Scoring logic for the three pools. Kept separate from routes so admin
actions and any future batch/cron processing can share the same code."""

from helpers import (
    _read_cache,
    deadline_passed,
    week_is_complete,
    week_sort_key,
    week_started,
)
from models import (
    DROPDEAD_BUYBACK_FEE,
    PRESEASON_OFFSET,
    Entry,
    Game,
    GridironMiss,
    LoserPoolPoints,
    Pick,
    User,
    Week,
    db,
    name_order,
)

# Rule 8: "the loss of all games for that week" -- what a week the entry sat
# out costs, whether it's their first missed week or their fifth.
GRIDIRON_MISS_PENALTY_LOSSES = 5
GRIDIRON_MAKEUP_PICKS = 8
GRIDIRON_NORMAL_PICKS = 5
GRIDIRON_BENCH_AFTER_MISSES = 5
# Rule 8: the makeup week is 8 picks out of 10 -- the 2 unpickable slots
# are charged as losses from the start.
GRIDIRON_MAKEUP_PENALTY_LOSSES = 2
# Gridiron has no buy-back. A $100 week-2 clean slate that voided week 1
# existed briefly and was removed in August 2026, together with the late
# signup window it existed to rescue: entries now close at the week 1
# deadline, so nobody can join late and need week 1 undone.
# The Drop Dead buy-back fee is defined in models.py, where the BuyBack row
# needs it as a column default, and re-exported here so the callers that have
# always imported it from scoring keep working.


def counts_for_season(week):
    """Whether a week's results feed the season proper.

    Preseason weeks are a trial run: the picks are made, graded and shown, so
    a player can see how the site works and what their record would have been,
    but nothing about them survives into the season. Week.is_preseason has said
    as much since it was added -- it just wasn't honoured anywhere until now.

    Everything preseason-related keys off this one function, so there is a
    single place to change if the commissioners ever decide otherwise.
    """
    if week is None:
        return False
    return not week.is_preseason


def score_dropdead_pick(pick, game):
    if game.winner is None:
        return
    team_won = (game.winner == "home" and pick.team_id == game.home_team_id) or (
        game.winner == "away" and pick.team_id == game.away_team_id
    )
    pick.result = "win" if team_won else "loss"
    pick.points = 1 if team_won else 0
    # A preseason pick is still graded -- the player sees whether they got it
    # right -- but a wrong one cannot end their season before it starts.
    if not team_won and counts_for_season(pick.week):
        entry = pick.entry
        if entry.is_active:
            entry.is_active = False
            entry.eliminated_week = pick.week.number


def score_loser_pick(pick, game, points_for_team):
    if game.winner is None:
        return
    team_lost = (game.winner == "home" and pick.team_id == game.away_team_id) or (
        game.winner == "away" and pick.team_id == game.home_team_id
    )
    if team_lost:
        pick.result = "win"
        pick.points = points_for_team
    else:
        pick.result = "loss"
        pick.points = -points_for_team


def score_gridiron_pick(pick, game):
    if not game.is_final or game.home_score is None or game.away_score is None:
        return
    margin = game.home_score - game.away_score
    if pick.market == "spread":
        spread = game.spread or 0
        if game.favorite == "home":
            adj = margin - spread
        elif game.favorite == "away":
            adj = margin + spread
        else:
            adj = margin
        if adj == 0:
            pick.result, pick.points = "push", 0
        elif (adj > 0 and pick.side == "home") or (adj < 0 and pick.side == "away"):
            pick.result, pick.points = "win", 1
        else:
            pick.result, pick.points = "loss", 0
    elif pick.market == "total":
        if game.over_under is None:
            return
        total = game.home_score + game.away_score
        if total == game.over_under:
            pick.result, pick.points = "push", 0
        elif (total > game.over_under and pick.side == "over") or (
            total < game.over_under and pick.side == "under"
        ):
            pick.result, pick.points = "win", 1
        else:
            pick.result, pick.points = "loss", 0


def score_game(game):
    """Score the picks tied to this (now-final) game. Each pool owns its own
    lines, so a game scores only the picks belonging to its own pool."""
    if game.pool == "gridiron":
        for pick in Pick.query.filter_by(pool="gridiron", game_id=game.id).all():
            score_gridiron_pick(pick, game)

    elif game.pool in ("dropdead", "loser") and game.home_team_id and game.away_team_id:
        team_picks = Pick.query.filter(
            Pick.pool == game.pool,
            Pick.week_id == game.week_id,
            Pick.team_id.in_([game.home_team_id, game.away_team_id]),
        ).all()
        for pick in team_picks:
            if pick.pool == "dropdead":
                score_dropdead_pick(pick, game)
            else:
                lp = LoserPoolPoints.query.filter_by(
                    season_year=game.week.season_year, team_id=pick.team_id
                ).first()
                score_loser_pick(pick, game, lp.points if lp else 0)

    db.session.commit()


def enforce_dropdead_no_tie(week):
    """Drop Dead can never end in a tie for first place. If a week's results
    (or no-show eliminations) wipe out every remaining active entry at once,
    revive whichever entries share that week's elimination -- the tied
    leaders -- so they keep playing until a solo survivor emerges. Only acts
    once the week's games are all final, so it never fires on a partial
    slate, and never touches a genuine solo winner (a single entry left
    active, or a single entry eliminated last)."""
    if week is None or week.pool != "dropdead":
        return
    games = Game.query.filter_by(week_id=week.id, pool="dropdead").all()
    if not games or not all(g.is_final for g in games):
        return
    if Entry.query.filter_by(pool="dropdead", season_year=week.season_year, is_active=True).count() > 0:
        return
    tied = Entry.query.filter_by(
        pool="dropdead", season_year=week.season_year, eliminated_week=week.number
    ).all()
    if len(tied) <= 1:
        return
    for e in tied:
        e.is_active = True
        e.eliminated_week = None
    db.session.commit()


def process_missed_picks(week):
    """Handle entries that never submitted a pick for this pool's week.
    Weeks are per-pool, so this applies only the logic for ``week.pool``:
    - Drop Dead: active entries with no pick are eliminated.
    - Loser Pool: entries with no pick are auto-assigned the visiting team
      of the week's Monday Night Football game (per the printed rules).
    - Gridiron: entries with no pick are scored 0-5 for the week (via a
      GridironMiss record) and get an 8-pick makeup allowance the following
      week; an entry with more than 5 missed weeks is benched (is_active
      set to False).
    """
    # A preseason week charges nothing in Drop Dead or the Loser Pool: no
    # no-show elimination, no auto-pick. Gridiron is the exception. A preseason
    # week an entry sat out IS recorded as a missed week, because that is what
    # makes it behave like a real one: forgiven as a first miss (0-0-0, not
    # 0-5), named in the standings' Penalties column, and worth the 8-pick
    # makeup week that follows. Recorded, but never fatal -- the bench check
    # below stays gated on counts_for_season(), so preseason misses cannot end
    # anyone's season. (Commissioner's call, 2026-08-30.)
    if not counts_for_season(week) and week.pool != "gridiron":
        return

    if week.pool == "dropdead":
        picked_dropdead_entry_ids = {
            p.entry_id
            for p in Pick.query.filter_by(pool="dropdead", week_id=week.id).all()
        }
        for entry in Entry.query.filter_by(pool="dropdead", season_year=week.season_year, is_active=True).all():
            if entry.id not in picked_dropdead_entry_ids:
                entry.is_active = False
                entry.eliminated_week = week.number

    elif week.pool == "loser":
        # "the visiting team on the last Monday Night Football game of the
        # betting week" -- latest kickoff wins when a week flags more than one.
        mnf_game = (
            Game.query.filter_by(week_id=week.id, is_mnf=True)
            .order_by(Game.kickoff.desc())
            .first()
        )
        if mnf_game and mnf_game.away_team_id:
            picked_loser_entry_ids = {
                p.entry_id for p in Pick.query.filter_by(pool="loser", week_id=week.id).all()
            }
            assigned = False
            for entry in Entry.query.filter_by(pool="loser", season_year=week.season_year).all():
                if entry.id not in picked_loser_entry_ids:
                    db.session.add(
                        Pick(
                            entry_id=entry.id,
                            week_id=week.id,
                            pool="loser",
                            team_id=mnf_game.away_team_id,
                        )
                    )
                    assigned = True
            # Score them here or nothing ever will. A Pick row is scored when
            # its game is scored, and these rows are created after the fact --
            # by a page load, or by the admin's Process Missed Picks button,
            # either of which can happen once the Monday night result is
            # already in. Every one of them is on the MNF away team, so
            # re-scoring that one game covers the lot. Left unscored they sat
            # at "pending" for good: grey in the reports, and worth 0 points
            # in a pool where a no-show is supposed to cost you.
            if assigned and mnf_game.is_final:
                db.session.flush()
                score_game(mnf_game)

    elif week.pool == "gridiron":
        picked_gridiron_entry_ids = {
            p.entry_id for p in Pick.query.filter_by(pool="gridiron", week_id=week.id).all()
        }
        for entry in Entry.query.filter_by(pool="gridiron", season_year=week.season_year, is_active=True).all():
            if entry.id in picked_gridiron_entry_ids:
                continue
            if GridironMiss.query.filter_by(entry_id=entry.id, week_id=week.id).first():
                continue
            db.session.add(GridironMiss(entry_id=entry.id, week_id=week.id))
            db.session.flush()
            if not counts_for_season(week):
                continue  # recorded, but a trial week can never bench anyone
            total_misses = GridironMiss.query.filter_by(entry_id=entry.id).count()
            if total_misses > GRIDIRON_BENCH_AFTER_MISSES:
                entry.is_active = False
                entry.eliminated_week = week.number

    db.session.commit()


def ensure_missed_processed(week):
    """Lazily apply this week's missed-pick penalties once its deadline has
    passed. Runs at most once per week (the ``missed_processed`` flag), so it
    can be called freely from any page load. The Loser Pool waits until its
    Monday Night game is entered so the no-show auto-pick can be assigned."""
    if week is None or week.missed_processed or not deadline_passed(week):
        return
    if not counts_for_season(week):
        # Settle it so it stops showing as owed. Marked before the Loser MNF
        # check below, which would otherwise hold a preseason Loser week open
        # forever waiting for a game that only matters for an auto-pick nobody
        # is going to be charged for.
        #
        # Gridiron still runs: a sat-out preseason week is recorded as a miss
        # so it is forgiven, named, and followed by the makeup week. Drop Dead
        # and the Loser Pool are skipped entirely, as before.
        if week.pool == "gridiron":
            process_missed_picks(week)
        week.missed_processed = True
        db.session.commit()
        return
    if week.pool == "loser":
        mnf = (
            Game.query.filter_by(week_id=week.id, is_mnf=True)
            .order_by(Game.kickoff.desc())
            .first()
        )
        if not (mnf and mnf.away_team_id):
            return  # retry on a later load, once the MNF game exists
    process_missed_picks(week)
    enforce_dropdead_no_tie(week)
    week.missed_processed = True
    db.session.commit()


def due_weeks(season_year, pool=None):
    """Every week whose deadline has passed and whose misses are still
    unprocessed, oldest first.

    ``ensure_missed_processed`` only looks at the week it is handed, and the
    player routes only ever hand it the *current* week. So if the current week
    moved on before anyone loaded a page after the previous week's deadline --
    an admin pinning ``active_week`` forward, a quiet Tuesday, a site nobody
    opened -- that week's misses were never written: no 0-5, no Gridiron
    makeup, no Drop Dead no-show elimination. Nothing ever came back for it,
    because every caller had already moved past it.

    Oldest first matters: the Gridiron makeup week is the week straight after
    the first miss, so week N has to be settled before week N+1 is judged.
    """
    # Sorted in Python for the same reason as gridiron_first_miss_week:
    # ORDER BY Week.number would settle week 1 before Preseason Week 4, and
    # "oldest first" is the whole point of this ordering.
    weeks = sorted(
        Week.query.filter_by(season_year=season_year, missed_processed=False).all(),
        key=lambda w: week_sort_key(w) + (w.pool,),
    )
    if pool:
        weeks = [w for w in weeks if w.pool == pool]
    return [w for w in weeks if deadline_passed(w)]


def process_due_weeks(season_year, pool=None):
    """Catch up every week that is owed processing. Returns the weeks settled.

    Cheap enough to call from a page load: one query over a table holding a
    few dozen rows a season, and on the common path it finds nothing. A week
    that cannot be settled yet -- a Loser week whose Monday night game has not
    been entered -- is left alone, picked up on a later run, and does not block
    the others.
    """
    settled = []
    for week in due_weeks(season_year, pool):
        ensure_missed_processed(week)
        if week.missed_processed:
            settled.append(week)
    return settled


def dropdead_eliminated_for_no_pick(entry):
    """True if this entry's elimination was for failing to turn in a pick.

    The printed rules deny a buy-back in that case ("with the exception of
    an entrant who failed to turn in a pick"), so it has to be told apart
    from being eliminated by a losing pick. A no-show leaves no Pick row
    for the week it died in -- that absence is the whole signal.
    """
    if entry.eliminated_week is None:
        return False
    return not Pick.query.filter_by(entry_id=entry.id, pool="dropdead").join(Week).filter(
        Week.number == entry.eliminated_week,
        Week.season_year == entry.season_year,
    ).first()


def dropdead_buyback_available(entry, current_week):
    """May this entry buy back in right now?

    The buy-back is for the week *after* the one that knocked you out -- the
    elimination week is already played -- so it's offered only while that
    following week is current and still open for picks. Whether a given
    week's eliminations are eligible at all is the admin's per-week flag,
    and it lives on the week the entry died in.
    """
    if entry.is_active or not entry.eliminated_week or not current_week:
        return False
    if current_week.number != entry.eliminated_week + 1:
        return False
    if deadline_passed(current_week):
        return False
    if dropdead_eliminated_for_no_pick(entry):
        return False
    elim_week = Week.query.filter_by(
        season_year=entry.season_year, pool="dropdead", number=entry.eliminated_week
    ).first()
    return bool(elim_week and elim_week.buyback_open)


def _week_order(number):
    """Where a week NUMBER sits on the schedule.

    Preseason weeks are stored at PRESEASON_OFFSET + N so they never collide
    with the regular season, which means a raw numeric comparison puts them
    after week 18 when they actually come before week 1. Anything comparing
    two week numbers has to go through here. helpers.week_sort_key does the
    same job for a Week row.
    """
    return (0 if number > PRESEASON_OFFSET else 1, number)


def gridiron_frozen_after(entry):
    """The last week that still counts for a benched entry, or None if live.

    Benching ends the entry's season, so its record stops moving there rather
    than collecting another 0-5 every week through the end of the schedule.
    """
    if entry.pool != "gridiron" or entry.is_active:
        return None
    return entry.eliminated_week


def gridiron_week_counts(entry, week_number):
    """Whether a week contributes to this entry's record at all -- false for
    preseason weeks and for weeks after the entry was benched. Nothing else
    voids a Gridiron week now that the buy-back is gone."""
    if week_number is None:
        return False
    # Preseason weeks are stored offset past the regular-season numbers, so
    # the number alone identifies them -- no week row needed. Their picks DO
    # reach the record (commissioner's call, 2026-08-29): preseason standings
    # are shown for Gridiron and the Loser Pool. What preseason still cannot
    # do is PENALISE -- no empty-slot losses, no makeup week, no Drop Dead
    # elimination. Every one of those stays gated on counts_for_season().
    if week_number > PRESEASON_OFFSET:
        return True
    frozen = gridiron_frozen_after(entry)
    return frozen is None or week_number <= frozen


def gridiron_counted_picks(entry, through_week=None):
    """This entry's Gridiron picks from the weeks that still count."""
    picks = [p for p in entry.picks if gridiron_week_counts(entry, p.week.number)]
    if through_week is not None:
        picks = [p for p in picks
                 if _week_order(p.week.number) <= _week_order(through_week)]
    return picks


def gridiron_first_miss_week(entry):
    """Week number of this entry's first missed Gridiron week, or None.

    Weeks voided by a buy-back don't count, which is the whole point of the
    fee: an entry that paid to erase a missed week 1 still has its one makeup
    allowance banked for the first week it misses afterwards.
    """
    cache = _read_cache()
    ck = ("first_miss", entry.id)
    if cache is not None and ck in cache:
        return cache[ck]

    # Ordered in Python, not SQL. Preseason weeks are numbered 101+, so
    # ORDER BY Week.number put them AFTER week 18 -- and a preseason miss then
    # lost the race to any regular-season miss, which is the wrong week to
    # forgive and the wrong week to hang the makeup off.
    misses = (
        GridironMiss.query.join(Week, GridironMiss.week_id == Week.id)
        .filter(
            GridironMiss.entry_id == entry.id,
            Week.season_year == entry.season_year,
            Week.pool == "gridiron",
        )
        .all()
    )
    first = min(misses, key=lambda m: week_sort_key(m.week)) if misses else None
    result = first.week.number if first else None
    if cache is not None:
        cache[ck] = result
    return result


def gridiron_makeup_week(entry):
    """The one week this entry may pick 8 games instead of 5: the week after
    its *first* missed week.

    Rule 8 grants the makeup for a first failure only -- "A second and/or
    subsequent failure, or a failure for the last week will result in the loss
    of all games for that week" -- so later misses get no extra picks."""
    first_miss = gridiron_first_miss_week(entry)
    if first_miss is None:
        return None
    # The next week ON THE SCHEDULE, which is not always first_miss + 1.
    # Preseason weeks are numbered 101+, so the week after Preseason Week 4
    # (104) is Week 1 -- not a week 105 that will never exist. Getting this
    # wrong means the makeup allowance is granted for a week nobody can play.
    cache = _read_cache()
    ck = ("gridiron_week_numbers", entry.season_year)
    if cache is not None and ck in cache:
        numbers = cache[ck]
    else:
        numbers = sorted(
            (w.number for w in Week.query.filter_by(
                season_year=entry.season_year, pool="gridiron").all()),
            key=_week_order,
        )
        if cache is not None:
            cache[ck] = numbers
    if first_miss in numbers:
        i = numbers.index(first_miss)
        return numbers[i + 1] if i + 1 < len(numbers) else None
    return first_miss + 1


def gridiron_pick_limit(entry, week):
    """5 picks normally, 8 in the single makeup week after a first miss --
    with the 2-game penalty charged alongside, so the week is worth 10."""
    if week is None:
        return GRIDIRON_NORMAL_PICKS
    if gridiron_makeup_week(entry) != week.number:
        return GRIDIRON_NORMAL_PICKS
    return GRIDIRON_MAKEUP_PICKS


def gridiron_penalty_losses(entry, through_week=None):
    """The 2-game penalty attached to the makeup week (8 picks out of 10).

    Charged from the moment the league ENTERS the makeup week -- not when that
    week's row is created, and not when its deadline passes.

    Both other timings were wrong. Waiting for the deadline meant an entry read
    0-0-0 on the Thursday the makeup week opened and 0-2-0 at Saturday noon,
    while the pick page in front of them had been showing the 2 losses as slots
    the whole time. Charging on existence was worse: all 18 regular weeks are
    created at once, so the penalty would appear the instant the miss was
    recorded, weeks before anyone could play the week it belongs to.
    week_started() is the same boundary get_current_week() moves on.
    (Commissioner's call, 2026-08-30.)

    Charged whether or not the entry turned up. The 2 games are the price of
    the makeup week itself, not of using it, so an entry that sits the week
    out pays them on top of its 8 unfilled slots and finishes the week 0-10
    (commissioner's ruling, 2026-08-21).
    """
    makeup = gridiron_makeup_week(entry)
    if makeup is None:
        return 0
    if through_week is not None and _week_order(makeup) > _week_order(through_week):
        return 0
    if not gridiron_week_counts(entry, makeup):
        return 0
    week = Week.query.filter_by(
        season_year=entry.season_year, pool="gridiron", number=makeup
    ).first()
    if not week_started(week):
        return 0
    return GRIDIRON_MAKEUP_PENALTY_LOSSES


def gridiron_week_penalty_losses(entry, week_number):
    """The makeup penalty charged against one specific week.

    Same 2 losses gridiron_penalty_losses reports for the season -- this just
    attributes them to the week they belong to, so a per-week column adds up
    to the season total instead of being quietly two losses light.
    """
    if gridiron_makeup_week(entry) != week_number:
        return 0
    return gridiron_penalty_losses(entry)


def gridiron_penalty_slots(entry, week):
    """How many automatic-loss slots to show on this week's pick page.

    The makeup week is 8 picks out of 10: the 2 slots the entry can't fill
    are losses from the moment the week opens, so they're shown alongside the
    real picks rather than appearing out of nowhere in the standings later.

    Unlike gridiron_week_penalty_losses this doesn't wait for the deadline --
    the whole point is to show the player what they're playing with while
    they still have picks to make. The two are otherwise the same number: the
    penalty is charged on the makeup week whether or not the entry turns up.
    """
    if week is None or entry.pool != "gridiron":
        return 0
    if gridiron_makeup_week(entry) != week.number:
        return 0
    if not gridiron_week_counts(entry, week.number):
        return 0
    return GRIDIRON_MAKEUP_PENALTY_LOSSES


# The weekly picks report is always this many columns wide. Nearly every
# player picks 5 in a normal week, so widening the whole table to 10 for the
# one entry on a makeup or buy-back week wastes half the page on dashes.
# Those entries wrap onto a second line inside the same 5 columns instead.
GRIDIRON_GRID_COLUMNS = 5


def _gridiron_grid_slots(picks, limit, penalty, no_pick_slots, keep_spare=False,
                        free_slots=0):
    """One entry's week as a flat list of slots, in the order they're shown.

    Picks first, then the empty slots being charged as losses, then any
    allowance the entry had but isn't being charged for, then the makeup
    week's automatic penalty losses, which always sit at the end. Padded out
    to a whole number of rows so it can be laid out as a fixed-width grid.

    "Spare" slots only ever appear on a sat-out makeup week: the entry was
    owed 8 picks but the week is charged as a flat 0-5, so 3 of those slots
    are owed nothing. The player's report drops them -- an extra row of
    dashes tells them nothing -- while the admin editor keeps them, because a
    commissioner fixing the week still needs somewhere to add those picks.

    Spare and penalty slots can never both appear: the penalty is only
    charged when at least one pick was made, and a week with picks in it has
    every remaining slot charged as a loss.
    """
    slots = [{"kind": "pick", "pick": p} for p in picks]
    slots += [{"kind": "nopick"}] * no_pick_slots
    # A first miss is forgiven, but the week still has to look like a week
    # that was sat out rather than a week that never existed.
    slots += [{"kind": "freemiss"}] * free_slots
    spare = max(0, limit - len(picks) - no_pick_slots - free_slots)
    slots += [{"kind": "spare"}] * spare
    if not keep_spare:
        while slots and slots[-1]["kind"] == "spare":
            slots.pop()
    if penalty:
        while len(slots) < limit:
            slots.append({"kind": "empty"})
        slots += [{"kind": "penalty"}] * penalty

    width = GRIDIRON_GRID_COLUMNS
    if not slots:
        slots = [{"kind": "empty"}]
    while len(slots) % width:
        slots.append({"kind": "empty"})
    return [slots[i:i + width] for i in range(0, len(slots), width)]


def gridiron_picks_grid(week):
    """Per-player picks grid for a Gridiron week: username + up to N pick
    slots. Shared between the admin editor and the read-only player report.

    ``slot_rows`` is the report's view of the same data -- rows of
    GRIDIRON_GRID_COLUMNS slots each, so an 8- or 10-pick entry wraps onto a
    second line rather than stretching the table. The admin editor still uses
    the flat ``picks``/``limit`` pair, because its cells carry add/change
    buttons tied to a single slot index.
    """
    entries = (
        Entry.query.filter_by(pool="gridiron", season_year=week.season_year)
        .join(User)
        .order_by(name_order(User.username), Entry.id)
        .all()
    )
    picks_grid = []
    max_slots = 0
    for e in entries:
        eps = sorted(
            (p for p in e.picks if p.week_id == week.id and p.pool == "gridiron"),
            key=lambda p: p.id,
        )
        limit = gridiron_pick_limit(e, week)
        # The makeup week's 2 unpickable slots sit after the 8 real ones, so
        # the row shows all 10 games the week is worth.
        penalty = gridiron_penalty_slots(e, week)
        # Why this entry's allowance is bigger than 5, for the caption.
        if gridiron_makeup_week(e) == week.number:
            allowance = "makeup"
        else:
            allowance = None
        # How many empty slots are actually being charged as losses. Once the
        # deadline has passed a sat-out week is a flat 5 however big the
        # allowance was, so a missed 8-pick makeup week must not print 8 "No
        # pick" cells against a 0-5 in the standings.
        if deadline_passed(week):
            no_pick_slots = _gridiron_week_empty_losses(e, week)
        else:
            no_pick_slots = max(0, limit - len(eps))
        # A week sat out that cost nothing: the entry's first miss. Without
        # slots of its own the row would be all dashes, reading as though the
        # week had never been theirs to play.
        free_miss = (
            not eps
            and no_pick_slots == 0
            and deadline_passed(week)
            and GridironMiss.query.filter_by(entry_id=e.id, week_id=week.id).first()
            is not None
        )
        free_slots = limit if free_miss else 0
        max_slots = max(max_slots, limit + penalty, len(eps) + no_pick_slots + free_slots)
        picks_grid.append(
            {
                "entry": e,
                "picks": eps,
                "limit": limit,
                "penalty": penalty,
                "allowance": allowance,
                "no_pick_slots": no_pick_slots,
                "free_miss": free_miss,
                "slot_rows": _gridiron_grid_slots(
                    eps, limit, penalty, no_pick_slots, free_slots=free_slots
                ),
                # The admin editor keeps the slots the entry was owed but
                # isn't charged for -- a commissioner fixing a sat-out makeup
                # week still needs somewhere to put those picks.
                "admin_slot_rows": _gridiron_grid_slots(
                    eps, limit, penalty, no_pick_slots, keep_spare=True,
                    free_slots=free_slots,
                ),
            }
        )
    max_slots = max_slots or 5
    return picks_grid, max_slots


def _gridiron_week_empty_losses(entry, week):
    """After a week's deadline, each pick slot a Gridiron entry left empty
    counts as a loss. Capped at what was actually pickable that week (number
    of games x available markets), so an entry is never penalized for slots it
    couldn't fill. Zero while the deadline hasn't passed (picks still open).

    A week the entry sat out entirely costs what that week was worth to that
    entry, which is not the same figure every time:

    - **the first week they miss: nothing.** Rule 8 gives a first-time
      failure "the opportunity to pick eight (8) games the following week,
      with a two (2) game penalty" and stops there. The 2 games are the
      price of a first miss; the week itself is a wash. "The loss of all
      games for that week" is attached to "a second and/or subsequent
      failure", not to the first.
    - the makeup week: the full 8-pick allowance, with the 2-game penalty
      charged alongside it, so a blown makeup week lands at 0-10.
    - every other week: the flat GRIDIRON_MISS_PENALTY_LOSSES.

    Commissioner's rulings, 2026-08-21 and 2026-08-22:
      "If you fail to not pick your makeup games, all eight of those picks
       that you get to do plus the two penalty picks count as losses."
      "In that week four, since it was their very first offense, basically,
       nothing happens for them in the standings."
    """
    if week is None or not deadline_passed(week):
        return 0
    if not gridiron_week_counts(entry, week.number):
        return 0
    cache = _read_cache()
    gk = ("wk_available", week.id)
    if cache is not None and gk in cache:
        available = cache[gk]
    else:
        games = Game.query.filter_by(week_id=week.id, pool="gridiron").all()
        available = sum(1 + (1 if g.over_under is not None else 0) for g in games)
        if cache is not None:
            cache[gk] = available
    if available == 0:
        return 0
    made = sum(1 for p in entry.picks if p.week_id == week.id and p.pool == "gridiron")
    expected = min(gridiron_pick_limit(entry, week), available)
    if made == 0:
        if gridiron_makeup_week(entry) == week.number:
            return expected
        if gridiron_first_miss_week(entry) == week.number:
            return 0
        return min(GRIDIRON_MISS_PENALTY_LOSSES, available)
    return max(0, expected - made)


def _gridiron_empty_losses(entry, through_week=None):
    """Total empty-slot losses across the entry's locked Gridiron weeks."""
    cache = _read_cache()
    ck = ("gridiron_weeks", entry.season_year)
    if cache is not None and ck in cache:
        weeks = cache[ck]
    else:
        weeks = Week.query.filter_by(
            season_year=entry.season_year, pool="gridiron"
        ).all()
        if cache is not None:
            cache[ck] = weeks
    # Preseason weeks are charged here too. Every OTHER view of an empty slot
    # -- the "last week" column on the Master Standings, the weekly picks
    # grid, the pick page's own slot list -- calls _gridiron_week_empty_losses
    # directly and has never had this filter, so a preseason week the entry
    # sat out already read as 0-5 in all of them while the season Won/Lost
    # columns beside them read 0-0. Worse, it meant an entry that turned up
    # and went 0-5 finished BELOW one that never picked at all.
    # (Commissioner's call, 2026-08-30: a preseason week counts in the
    # standings exactly like a regular one.)
    #
    # What preseason still cannot do is PENALISE beyond the week itself: no
    # GridironMiss row, so no makeup week and no benching, and a sat-out
    # preseason week does not burn the one forgiven first miss. Those all stay
    # gated on counts_for_season() inside process_missed_picks().
    if through_week is not None:
        # Preseason weeks are numbered 101+, so a plain <= comparison would
        # drop them from every through-week view. They happened before week 1,
        # so they belong in all of them.
        weeks = [w for w in weeks if w.is_preseason or w.number <= through_week]
    return sum(_gridiron_week_empty_losses(entry, w) for w in weeks)


def _assign_ranks(items, key_func):
    """Standard competition ranking (1224): entries with an equal key share
    the same place, and the next distinct key's place is its 1-based
    position in the list (so 1, 1, 1, 4 -- not 1, 1, 1, 2)."""
    ranked = []
    prev_key = object()
    prev_rank = 0
    for i, item in enumerate(items, start=1):
        key = key_func(item)
        if key != prev_key:
            prev_rank = i
            prev_key = key
        ranked.append((prev_rank,) + (item if isinstance(item, tuple) else (item,)))
    return ranked


def standings_dropdead(season_year):
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    entries.sort(key=lambda e: (not e.is_active, -(e.eliminated_week or 999)))
    return _assign_ranks(entries, key_func=lambda e: (not e.is_active, e.eliminated_week))


def standings_loser(season_year):
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).all()
    totals = []
    for e in entries:
        # Preseason points count toward the displayed total, which also makes
        # this agree with the pick page's running total -- the two used to
        # disagree, one filtering and one not.
        total = sum(p.points or 0 for p in e.picks)
        totals.append((e, total))
    totals.sort(key=lambda t: -t[1])
    return _assign_ranks(totals, key_func=lambda t: t[1])


def standings_gridiron(season_year):
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    rows = []
    for e in entries:
        picks = gridiron_counted_picks(e)  # skips bought-back and post-bench weeks
        wins = sum(1 for p in picks if p.result == "win")
        losses = sum(1 for p in picks if p.result == "loss")
        losses += _gridiron_empty_losses(e)  # empty slots after deadline = losses
        losses += gridiron_penalty_losses(e)  # makeup week is 8 of 10
        pushes = sum(1 for p in picks if p.result == "push")
        rows.append((e, wins, losses, pushes))
    rows.sort(key=lambda r: (-r[1], r[2]))
    return _assign_ranks(rows, key_func=lambda r: (r[1], r[2]))


def dropdead_status_through_week(season_year, week_number):
    """Drop Dead status/pick as it stood after a given past week."""
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    rows = []
    for e in entries:
        # A buy-back always lands on the same week number as the elimination
        # it reversed (the route only allows same-week buy-backs), so if
        # eliminated_week still equals buyback_week, nothing has eliminated
        # this entry since -- it's been active from that week forward. If
        # eliminated_week has since moved past buyback_week, that's a later,
        # separate elimination and should show as "out" from that week on.
        bought_back_by_then = e.buyback_week is not None and e.buyback_week <= week_number
        revived = e.buyback_week is not None and e.eliminated_week == e.buyback_week
        eliminated_by_then = (
            not revived and e.eliminated_week is not None and e.eliminated_week <= week_number
        )
        week_pick = next((p for p in e.picks if p.week.number == week_number), None)
        rows.append(
            {
                "entry": e,
                "is_active": not eliminated_by_then,
                "eliminated_week": e.eliminated_week if eliminated_by_then else None,
                "week_pick": week_pick,
                "buyback_week": e.buyback_week if bought_back_by_then else None,
            }
        )
    rows.sort(key=lambda r: (not r["is_active"], -(r["eliminated_week"] or 999)))
    return rows


def loser_totals_through_week(season_year, week_number):
    """Loser Pool point totals accumulated through a given past week."""
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).all()
    rows = []
    for e in entries:
        # Preseason weeks are numbered 101+, so a plain <= comparison would
        # drop them from every through-week view. They happened before week 1,
        # so they belong in all of them.
        picks_through = [
            p for p in e.picks
            if p.week.is_preseason or p.week.number <= week_number
        ]
        total = sum(p.points or 0 for p in picks_through)
        week_pick = next((p for p in e.picks if p.week.number == week_number), None)
        rows.append((e, total, week_pick))
    rows.sort(key=lambda r: -r[1])
    return rows


def gridiron_record_through_week(season_year, week_number):
    """Gridiron win/loss/tie record accumulated through a given past week,
    plus the entry's actual picks (team/side + result) for that specific
    week, so a per-week view can show what was picked, not just the tally."""
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    rows = []
    for e in entries:
        picks_through = gridiron_counted_picks(e, week_number)
        wins = sum(1 for p in picks_through if p.result == "win")
        losses = sum(1 for p in picks_through if p.result == "loss")
        losses += _gridiron_empty_losses(e, week_number)  # empty slots after deadline = losses
        losses += gridiron_penalty_losses(e, week_number)  # makeup week is 8 of 10
        ties = sum(1 for p in picks_through if p.result == "push")

        week_picks = []
        for p in e.picks:
            if p.week.number != week_number:
                continue
            if p.market == "spread":
                label = p.game.home_team if p.side == "home" else p.game.away_team
            else:
                label = f"{p.side.capitalize()} {p.game.over_under} ({p.game.home_team})"
            week_picks.append({"label": label, "result": p.result})

        rows.append((e, wins, losses, ties, week_picks))
    rows.sort(key=lambda r: (-r[1], r[2]))
    return rows


def player_pick_history(season_year, user_id):
    """Every team/selection a given user has picked this season, across all
    three pools, with each pick's win/loss result (once its week is fully
    complete -- otherwise shown as pending)."""
    rows = []
    for pool_name in ("dropdead", "loser", "gridiron"):
        entries = Entry.query.filter_by(pool=pool_name, season_year=season_year, user_id=user_id).all()
        for e in entries:
            for p in sorted(e.picks, key=lambda p: p.week.number):
                if pool_name in ("dropdead", "loser"):
                    team_label = f"{p.team.city} {p.team.name}" if p.team else "—"
                elif p.market == "spread":
                    team_label = p.game.home_team if p.side == "home" else p.game.away_team
                else:
                    team_label = f"{p.side.capitalize()} {p.game.over_under} ({p.game.home_team})"

                result = p.result if p.result in ("win", "loss", "push") else "pending"
                rows.append(
                    {
                        "pool": pool_name,
                        "entry_label": e.label,
                        "week": p.week.number,
                        "team": team_label,
                        "result": result,
                        "points": p.points,
                    }
                )
            if pool_name == "gridiron":
                for w in Week.query.filter_by(season_year=season_year, pool="gridiron").order_by(Week.number).all():
                    empty = _gridiron_week_empty_losses(e, w)
                    if empty > 0:
                        rows.append(
                            {
                                "pool": pool_name,
                                "entry_label": e.label,
                                "week": w.number,
                                "team": ("NO PICK ×%d" % empty) if empty > 1 else "NO PICK",
                                "result": "loss",
                                "points": None,
                            }
                        )
                    # The makeup week's 2 unpickable slots, listed the same
                    # way so the history shows every game the record counts.
                    penalty = gridiron_week_penalty_losses(e, w.number)
                    if penalty > 0:
                        rows.append(
                            {
                                "pool": pool_name,
                                "entry_label": e.label,
                                "week": w.number,
                                "team": ("PENALTY LOSS ×%d" % penalty) if penalty > 1 else "PENALTY LOSS",
                                "result": "loss",
                                "points": None,
                            }
                        )
            elif pool_name == "dropdead" and e.eliminated_week is not None:
                # Elimination with no Pick row for that week means the entry
                # never submitted a pick that week (auto-eliminated for a
                # no-show, same as picking a loser) -- show it explicitly
                # rather than silently having no Drop Dead row at all.
                picked_weeks = {p.week.number for p in e.picks}
                if e.eliminated_week not in picked_weeks:
                    rows.append(
                        {
                            "pool": pool_name,
                            "entry_label": e.label,
                            "week": e.eliminated_week,
                            "team": "NO PICK SUBMITTED",
                            "result": "loss",
                            "points": None,
                        }
                    )
    rows.sort(key=lambda r: (r["pool"], r["week"]))
    return rows


def dropdead_matrix(season_year, week_numbers):
    """Every entry's pick for every unlocked week, side by side."""
    entries = Entry.query.filter_by(pool="dropdead", season_year=season_year).all()
    rows = []
    for e in entries:
        cells = {wn: next((p for p in e.picks if p.week.number == wn), None) for wn in week_numbers}
        rows.append({
            "entry": e,
            "cells": cells,
            # The week the fee brought this entry back for. buyback_week
            # holds the week they were eliminated in, and a buy-back always
            # returns them for the week after it -- so this is the week the
            # money actually bought.
            "bought_back_into": (e.buyback_week + 1) if e.buyback_week else None,
        })
    rows.sort(key=lambda r: (not r["entry"].is_active, -(r["entry"].eliminated_week or 999)))
    return rows


def loser_matrix(season_year, week_numbers):
    """Every entry's pick + running point total for every unlocked week."""
    entries = Entry.query.filter_by(pool="loser", season_year=season_year).all()
    rows = []
    for e in entries:
        cells = {wn: next((p for p in e.picks if p.week.number == wn), None) for wn in week_numbers}
        total = sum(p.points or 0 for p in e.picks)
        rows.append({"entry": e, "cells": cells, "total": total})
    rows.sort(key=lambda r: -r["total"])
    return rows


def gridiron_matrix(season_year, week_numbers):
    """Every entry's per-week W-L-T record for every unlocked week, plus a
    season total. A missed week shows as a 0-5 penalty cell."""
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    weeks_by_num = {
        w.number: w
        for w in Week.query.filter_by(season_year=season_year, pool="gridiron").all()
    }
    rows = []
    for e in entries:
        cells = {}
        missed = {m.week.number for m in GridironMiss.query.filter_by(entry_id=e.id).all()}
        for wn in week_numbers:
            week = weeks_by_num.get(wn)
            counts = gridiron_week_counts(e, wn)
            week_picks = [p for p in e.picks if p.week.number == wn] if counts else []
            empty = _gridiron_week_empty_losses(e, week)
            # The makeup week's 2-game penalty belongs in that week's cell,
            # not just in the season total, or the row doesn't add up.
            penalty = gridiron_week_penalty_losses(e, wn) if counts else 0
            # A first miss costs nothing but still happened: give it a cell
            # rather than a dash, or the row looks like the week was never
            # theirs.
            free_miss = counts and wn in missed and not week_picks and empty == 0
            if not week_picks and empty == 0 and penalty == 0 and not free_miss:
                cells[wn] = None
                continue
            cells[wn] = {
                "wins": sum(1 for p in week_picks if p.result == "win"),
                "losses": sum(1 for p in week_picks if p.result == "loss")
                + empty
                + penalty,
                "ties": sum(1 for p in week_picks if p.result == "push"),
                "missed": not week_picks and empty > 0,
                "free_miss": free_miss,
                "penalty": penalty,
            }
        picks = gridiron_counted_picks(e)
        wins = sum(1 for p in picks if p.result == "win")
        losses = sum(1 for p in picks if p.result == "loss") + _gridiron_empty_losses(e)
        losses += gridiron_penalty_losses(e)  # same total standings_gridiron shows
        ties = sum(1 for p in picks if p.result == "push")
        rows.append({
            "entry": e,
            "cells": cells,
            "wins": wins,
            "losses": losses,
            "ties": ties,
            # The week this entry first failed to get its picks in, which is
            # the one that cost it the makeup allowance. Later misses add
            # nothing here: the penalty is a once-per-season thing.
            "first_miss": gridiron_first_miss_week(e),
        })

    # Placed on the season record, not on the most recent week -- the pool is
    # won over 18 weeks. Standard competition ranking, so equal records share
    # a place and the next distinct record takes its 1-based position.
    rows.sort(key=lambda r: (-r["wins"], r["losses"]))
    prev_key, prev_rank = object(), 0
    for position, row in enumerate(rows, start=1):
        key = (row["wins"], row["losses"])
        if key != prev_key:
            prev_rank, prev_key = position, key
        row["rank"] = prev_rank
    return rows


def gridiron_week_records(season_year, week_number):
    """Each Gridiron entry's win-loss-push record for one week alone.

    Distinct from gridiron_record_through_week, which accumulates from the
    start of the season -- this is just the week named, for the "last week"
    column on the standings.

    Counts the same three things the season total does: scored picks, slots
    left empty once the deadline passed (a sat-out week is a flat 0-5 here
    too), and the makeup week's 2-game penalty. A week voided by a buy-back
    is left out entirely rather than reported as 0-0-0.
    """
    week = Week.query.filter_by(
        season_year=season_year, pool="gridiron", number=week_number
    ).first()
    if week is None:
        return {}

    picks_by_entry = {}
    for pick in Pick.query.filter_by(pool="gridiron", week_id=week.id).all():
        picks_by_entry.setdefault(pick.entry_id, []).append(pick)

    records = {}
    for entry in Entry.query.filter_by(pool="gridiron", season_year=season_year).all():
        if not gridiron_week_counts(entry, week_number):
            continue
        missed = (
            GridironMiss.query.filter_by(entry_id=entry.id, week_id=week.id).first()
            is not None
        )
        eps = picks_by_entry.get(entry.id, [])
        losses = sum(1 for p in eps if p.result == "loss")
        losses += _gridiron_week_empty_losses(entry, week)
        losses += gridiron_week_penalty_losses(entry, week_number)
        wins = sum(1 for p in eps if p.result == "win")
        ties = sum(1 for p in eps if p.result == "push")
        # 0-0-0 is a real result for a forgiven first miss, so keep it.
        if not eps and losses == 0 and not missed:
            continue
        records[entry.id] = (wins, losses, ties)
    return records


# ---------------------------------------------------------------------------
# The special awards
#
# Separate from the place money, which is 12% of paid entries and lives on the
# ordinary standings. These five are decided on their own terms:
#
#   1st Half Winner   leader through week 9
#   2nd Half Winner   leader over weeks 10-18
#   Last Place Award  fewest wins, but only among entrants who submitted all
#                     season with no penalties
#   Most Ties Award   most pushes
#   Most 0-5 Weeks    most 0-5 weeks, ignoring any week the entrant was
#                     penalised in
#
# Amounts are set by the Rules Committee each year and are deliberately not
# hardcoded here.
# ---------------------------------------------------------------------------

GRIDIRON_FIRST_HALF_WEEKS = (1, 9)
GRIDIRON_SECOND_HALF_WEEKS = (10, 18)


# The 2026-2027 sheet dropped the old "may not select both sides of the same
# game" rule (two losses plus a Last Place Award disqualification). Nothing
# checks for it any more: two picks on opposite sides of one game are simply
# two ordinary picks that cannot both win. If a future sheet brings it back,
# it belongs here, keyed on (week, game, market).


def gridiron_award_rows(season_year):
    """Every Gridiron entry with the numbers the special awards turn on."""
    entries = Entry.query.filter_by(pool="gridiron", season_year=season_year).all()
    weeks = (
        Week.query.filter_by(season_year=season_year, pool="gridiron")
        .order_by(Week.number)
        .all()
    )
    rows = []
    for entry in entries:
        missed = {
            m.week.number for m in GridironMiss.query.filter_by(entry_id=entry.id).all()
        }
        makeup = gridiron_makeup_week(entry)

        totals = {"wins": 0, "losses": 0, "ties": 0}
        halves = {"first": {"wins": 0, "losses": 0}, "second": {"wins": 0, "losses": 0}}
        zero_five_weeks = []

        for week in weeks:
            if not gridiron_week_counts(entry, week.number):
                continue
            picks = [
                p for p in entry.picks
                if p.week_id == week.id and p.pool == "gridiron"
            ]
            wins = sum(1 for p in picks if p.result == "win")
            losses = sum(1 for p in picks if p.result == "loss")
            losses += _gridiron_week_empty_losses(entry, week)
            losses += gridiron_week_penalty_losses(entry, week.number)
            ties = sum(1 for p in picks if p.result == "push")

            totals["wins"] += wins
            totals["losses"] += losses
            totals["ties"] += ties

            half = "first" if week.number <= GRIDIRON_FIRST_HALF_WEEKS[1] else "second"
            halves[half]["wins"] += wins
            halves[half]["losses"] += losses

            # "on any given penalized week an 0-5 record will not count toward
            # the entrant's 0-5 weeks for purposes of award qualification"
            penalised = week.number in missed or makeup == week.number
            # 0-5 means 0-5: a winless week carrying a push is 0-4-1, and the
            # award is not named for those. Empty slots charged as losses do
            # count, since the week's record still reads 0-5.
            went_oh_five = (
                wins == 0 and ties == 0 and losses == GRIDIRON_NORMAL_PICKS
            )
            if picks and went_oh_five and not penalised and week_is_complete(week):
                zero_five_weeks.append(week.number)

        # "an entrant must have submitted picks for the entire season, with no
        # penalties". Three things end that:
        #   - a missed week. Gridiron has no buy-back, so that is the only
        #     thing that ends it.
        rows.append({
            "entry": entry,
            "wins": totals["wins"],
            "losses": totals["losses"],
            "ties": totals["ties"],
            "first_half": halves["first"],
            "second_half": halves["second"],
            "zero_five_weeks": zero_five_weeks,
            "missed_weeks": sorted(missed),
            "last_place_eligible": not missed,
        })
    return rows


def _leaders(rows, key, best="max", require=lambda r: True):
    """Everyone tied at the top of `key`, so a shared award reads as shared."""
    candidates = [r for r in rows if require(r)]
    if not candidates:
        return []
    values = [key(r) for r in candidates]
    target = max(values) if best == "max" else min(values)
    return [r for r in candidates if key(r) == target]


def gridiron_awards(season_year):
    """The five special awards as they stand right now.

    Every award reports *all* entrants tied at the top rather than picking
    one: the printed rules break ties on the place awards by fewest losses,
    but for the Last Place Award they name the same figure the award is
    already decided on, so a genuine tie there has no stated tiebreak. Better
    to show it than to invent one.
    """
    rows = gridiron_award_rows(season_year)
    played = {
        w.number
        for w in Week.query.filter_by(season_year=season_year, pool="gridiron").all()
        if week_is_complete(w)
    }

    def leader_list(leaders, detail):
        """Flatten to what a template needs: who, and the number they won on."""
        return [{"entry": r["entry"], "detail": detail(r)} for r in leaders]

    def half(name, first, last):
        weeks_in = sorted(n for n in played if first <= n <= last)
        # Most wins, ties broken by fewest losses -- the place-award rule.
        leaders = _leaders(
            rows,
            key=lambda r: (r[name]["wins"], -r[name]["losses"]),
            require=lambda r: bool(weeks_in) and (r[name]["wins"] or r[name]["losses"]),
        )
        return {
            "weeks": (first, last),
            "weeks_played": weeks_in,
            "complete": last in played,
            "leaders": leader_list(
                leaders, lambda r: f"{r[name]['wins']}-{r[name]['losses']}"
            ),
        }

    eligible = [r for r in rows if r["last_place_eligible"]]
    return {
        "first_half": half("first_half", *GRIDIRON_FIRST_HALF_WEEKS),
        "second_half": half("second_half", *GRIDIRON_SECOND_HALF_WEEKS),
        "last_place": {
            "leaders": leader_list(
                _leaders(rows, key=lambda r: r["wins"], best="min",
                         require=lambda r: r["last_place_eligible"]),
                lambda r: f"{r['wins']}-{r['losses']}-{r['ties']}",
            ),
            "eligible": len(eligible),
            "disqualified": len(rows) - len(eligible),
        },
        "most_ties": {
            "leaders": leader_list(
                _leaders(rows, key=lambda r: r["ties"],
                         require=lambda r: r["ties"] > 0),
                lambda r: f"{r['ties']} tie" + ("" if r["ties"] == 1 else "s"),
            ),
        },
        "most_zero_five": {
            "leaders": leader_list(
                _leaders(rows, key=lambda r: len(r["zero_five_weeks"]),
                         require=lambda r: r["zero_five_weeks"]),
                lambda r: "{} week{} ({})".format(
                    len(r["zero_five_weeks"]),
                    "" if len(r["zero_five_weeks"]) == 1 else "s",
                    ", ".join(f"Wk {n}" for n in r["zero_five_weeks"]),
                ),
            ),
        },
        "rows": rows,
    }
