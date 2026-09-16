from database import get_connection
from game_data import build_champion_lookup, build_trait_data, build_trait_static_data


def _build_queue_filter(queue_id):
    """
    Helper: returns the SQL fragment and parameter for filtering by queue_id.
    If queue_id is None, we skip the filter entirely (i.e. "all modes").
    """
    if queue_id is None:
        return "", ()
    return "AND queue_id = ?", (queue_id,)


def average_placement(set_number: int = 18, queue_id: int = 1100):
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)
    cursor.execute(
        f"SELECT AVG(placement) FROM games WHERE set_number = ? {queue_sql}",
        (set_number,) + queue_params
    )
    result = cursor.fetchone()[0]
    conn.close()
    return result


def top4_rate(set_number: int = 18, queue_id: int = 1100):
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)

    cursor.execute(
        f"SELECT COUNT(*) FROM games WHERE set_number = ? {queue_sql}",
        (set_number,) + queue_params
    )
    total_games = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(*) FROM games WHERE set_number = ? {queue_sql} AND placement <= 4",
        (set_number,) + queue_params
    )
    top4_games = cursor.fetchone()[0]

    conn.close()

    if total_games == 0:
        return 0
    return (top4_games / total_games) * 100


def win_rate(set_number: int = 18, queue_id: int = 1100):
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)

    cursor.execute(
        f"SELECT COUNT(*) FROM games WHERE set_number = ? {queue_sql}",
        (set_number,) + queue_params
    )
    total_games = cursor.fetchone()[0]

    cursor.execute(
        f"SELECT COUNT(*) FROM games WHERE set_number = ? {queue_sql} AND placement = 1",
        (set_number,) + queue_params
    )
    wins = cursor.fetchone()[0]

    conn.close()

    if total_games == 0:
        return 0
    return (wins / total_games) * 100


def placement_breakdown(set_number: int = 18, queue_id: int = 1100):
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)
    cursor.execute(f"""
        SELECT placement, COUNT(*) 
        FROM games 
        WHERE set_number = ? {queue_sql}
        GROUP BY placement 
        ORDER BY placement
    """, (set_number,) + queue_params)
    results = dict(cursor.fetchall())
    conn.close()

    max_placement = 4 if queue_id == 1160 else 8
    return [(placement, results.get(placement, 0)) for placement in range(1, max_placement + 1)]


def most_played_units(limit: int = 10, set_number: int = 18, queue_id: int = 1100, sort_by: str = "frequency", min_games: int = 3):
    """
    sort_by="frequency" (default) ranks by times played, no games floor —
    unchanged from before. sort_by="performance" ranks by best avg
    placement instead, but only among units with at least min_games games
    — without that floor, a unit played once and won would always top the
    list, which says nothing meaningful about how good a pick it actually
    is.
    """
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)
    if sort_by == "performance":
        order_sql = "avg_placement ASC"
        having_sql = "HAVING COUNT(*) >= ?"
        having_params = (min_games,)
    else:
        order_sql = "times_played DESC"
        having_sql = ""
        having_params = ()

    cursor.execute(f"""
        SELECT units.character_id, COUNT(*) as times_played, AVG(games.placement) as avg_placement
        FROM units
        JOIN games ON units.match_id = games.match_id
        WHERE games.set_number = ? {queue_sql}
        GROUP BY units.character_id
        {having_sql}
        ORDER BY {order_sql}
        LIMIT ?
    """, (set_number,) + queue_params + having_params + (limit,))
    results = cursor.fetchall()
    conn.close()

    champion_lookup = build_champion_lookup(set_number)
    readable_results = []
    for character_id, times_played, avg_placement in results:
        name = champion_lookup.get(character_id, character_id)
        readable_results.append((character_id, name, times_played, avg_placement))

    return readable_results


def most_played_traits(limit: int = 10, set_number: int = 18, queue_id: int = 1100, sort_by: str = "frequency", min_games: int = 3):
    """
    Mirrors most_played_units, but for traits, broken out by which specific
    breakpoint tier was reached each game — not just "was this trait active
    at all". Hitting 4 Brawlers plays very differently (and usually
    performs differently) from hitting 2, so each ranks as its own row and
    competes independently for a top-10 spot, rather than being lumped into
    one "Brawler" bucket that hides which tier actually did the work.
    Excludes tier_current = 0 (no bonus reached) the same way the old
    style != 0 filter did — they should always agree, both are kept as a
    defensive double-check rather than assuming. Also excludes "unique"
    traits (tier_total == 1, i.e. the trait only ever has one possible
    tier) — same definition match_detail.py uses to give them their own
    orange styling instead of the normal bronze/silver/gold ladder, since
    they don't really have a "breakpoint" to rank by in the first place.

    sort_by="performance" ranks by best avg placement instead of frequency,
    same min_games floor and reasoning as most_played_units.
    """
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)
    if sort_by == "performance":
        order_sql = "avg_placement ASC"
        having_sql = "HAVING COUNT(*) >= ?"
        having_params = (min_games,)
    else:
        order_sql = "times_active DESC"
        having_sql = ""
        having_params = ()

    cursor.execute(f"""
        SELECT traits.trait_name, traits.tier_current, traits.style,
               COUNT(*) as times_active, AVG(games.placement) as avg_placement
        FROM traits
        JOIN games ON traits.match_id = games.match_id
        WHERE games.set_number = ? {queue_sql} AND traits.style != 0 AND traits.tier_current > 0
              AND traits.tier_total > 1
        GROUP BY traits.trait_name, traits.tier_current, traits.style
        {having_sql}
        ORDER BY {order_sql}
        LIMIT ?
    """, (set_number,) + queue_params + having_params + (limit,))
    results = cursor.fetchall()
    conn.close()

    trait_data = build_trait_data(set_number)
    trait_static_data = build_trait_static_data(f"TFTSet{set_number}")
    readable_results = []
    for trait_name, tier_current, style, times_active, avg_placement in results:
        trait_info = trait_data.get(trait_name)
        name = trait_info["name"] if trait_info else trait_name

        # tier_current is a 1-indexed breakpoint number (1st bonus tier
        # reached, 2nd, ...) — look up the actual unit-count threshold for
        # that specific breakpoint from Riot's trait tooltip data, so the
        # displayed number is the trait's real design threshold (e.g. "4")
        # rather than whatever exact num_units a given game happened to
        # have while sitting in that tier.
        breakpoints = (trait_static_data.get(trait_name) or {}).get("conditional_trait_sets", [])
        min_units = breakpoints[tier_current - 1]["min_units"] if 0 < tier_current <= len(breakpoints) else None

        readable_results.append((trait_name, name, min_units, style, times_active, avg_placement))

    return readable_results


def match_history(set_number: int = 18, queue_id: int = 1100):
    """
    Returns a list of all saved games, most recent first, as raw tuples.
    Date formatting now happens in match_detail.py instead.
    """
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)
    cursor.execute(f"""
        SELECT match_id, placement, level, gold_left, last_round, game_datetime, queue_id
        FROM games
        WHERE set_number = ? {queue_sql}
        ORDER BY game_datetime DESC
    """, (set_number,) + queue_params)
    results = cursor.fetchall()
    conn.close()

    return results

if __name__ == "__main__":
    print(f"Average placement: {average_placement():.2f}")
    print(f"Top 4 rate: {top4_rate():.1f}%")

    print("\nPlacement breakdown:")
    for placement, count in placement_breakdown():
        print(f"  {placement}: {count} game(s)")

    print("\nMost played units:")
    for character_id, name, times_played, avg_placement in most_played_units():
        print(f"  {name}: played {times_played}x, avg placement {avg_placement:.2f}")


