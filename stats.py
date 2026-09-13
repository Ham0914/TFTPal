from database import get_connection
from game_data import build_champion_lookup


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


def most_played_units(limit: int = 10, set_number: int = 18, queue_id: int = 1100):
    conn = get_connection()
    cursor = conn.cursor()

    queue_sql, queue_params = _build_queue_filter(queue_id)
    cursor.execute(f"""
        SELECT units.character_id, COUNT(*) as times_played, AVG(games.placement) as avg_placement
        FROM units
        JOIN games ON units.match_id = games.match_id
        WHERE games.set_number = ? {queue_sql}
        GROUP BY units.character_id
        ORDER BY times_played DESC
        LIMIT ?
    """, (set_number,) + queue_params + (limit,))
    results = cursor.fetchall()
    conn.close()

    champion_lookup = build_champion_lookup(set_number)
    readable_results = []
    for character_id, times_played, avg_placement in results:
        name = champion_lookup.get(character_id, character_id)
        readable_results.append((name, times_played, avg_placement))

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
        SELECT match_id, placement, level, gold_left, last_round, game_datetime
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
    for name, times_played, avg_placement in most_played_units():
        print(f"  {name}: played {times_played}x, avg placement {avg_placement:.2f}")


