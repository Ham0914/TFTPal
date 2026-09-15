import sqlite3
import math

DB_FILE = "tft_coach.db"


def get_connection():
    """
    Opens a connection to our SQLite database file.
    SQLite creates the file automatically if it doesn't exist yet.
    """
    return sqlite3.connect(DB_FILE)


def create_tables():
    """
    Creates the games and units tables if they don't already exist.
    Safe to run every time the program starts — it won't wipe existing data.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS games (
            match_id TEXT PRIMARY KEY,
            placement INTEGER,
            level INTEGER,
            gold_left INTEGER,
            last_round INTEGER,
            set_number INTEGER,
            game_datetime INTEGER,
            queue_id INTEGER,
            rank_tier TEXT,
            rank_division TEXT,
            league_points INTEGER,
            total_damage INTEGER,
            companion_content_id TEXT,
            companion_item_id INTEGER
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            character_id TEXT,
            star_level INTEGER,
            items TEXT,
            FOREIGN KEY (match_id) REFERENCES games (match_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS traits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            trait_name TEXT,
            num_units INTEGER,
            style INTEGER,
            tier_current INTEGER,
            tier_total INTEGER,
            FOREIGN KEY (match_id) REFERENCES games (match_id)
        )
    """)

    conn.commit()  # saves the changes to the file
    conn.close()   # frees up the connection

def save_match(match_data: dict, my_puuid: str, rank_info: dict = None):
    """
    Takes a full match response, the player's puuid, and optionally
    a rank snapshot (tier/division/LP) taken around the time this
    match was detected. rank_info is None for backfilled old matches.
    """
    info = match_data["info"]
    participant = None
    for p in info["participants"]:
        if p["puuid"] == my_puuid:
            participant = p
            break

    if participant is None:
        raise ValueError("Could not find this player in the match data")

    match_id = match_data["metadata"]["match_id"]

    conn = get_connection()
    cursor = conn.cursor()

    placement = participant["placement"]
    if info["queueId"] == 1160:
        placement = math.ceil(placement / 2)

    # rank_info might be None (old backfilled matches) — pull out
    # each field safely, defaulting to None if not provided.
    rank_tier = rank_info["tier"] if rank_info else None
    rank_division = rank_info["division"] if rank_info else None
    league_points = rank_info["league_points"] if rank_info else None

    companion = participant.get("companion", {})

    cursor.execute("""
        INSERT OR IGNORE INTO games
        (match_id, placement, level, gold_left, last_round, set_number, game_datetime, queue_id,
         rank_tier, rank_division, league_points, total_damage, companion_content_id, companion_item_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        match_id,
        placement,
        participant["level"],
        participant["gold_left"],
        participant["last_round"],
        info["tft_set_number"],
        info["game_datetime"],
        info["queueId"],
        rank_tier,
        rank_division,
        league_points,
        participant["total_damage_to_players"],
        companion.get("content_ID"),
        companion.get("item_ID"),
    ))

    if cursor.rowcount == 0:
        if rank_info is not None:
            cursor.execute("""
                UPDATE games SET rank_tier = ?, rank_division = ?, league_points = ?
                WHERE match_id = ? AND rank_tier IS NULL
            """, (rank_tier, rank_division, league_points, match_id))
            if cursor.rowcount > 0:
                conn.commit()
                print(f"Match {match_id} already in database — backfilled rank ({rank_tier} {rank_division}, {league_points} LP).")
            else:
                print(f"Match {match_id} already in database, skipping.")
        else:
            print(f"Match {match_id} already in database, skipping.")
        conn.close()
        return

    for unit in participant["units"]:
        items_string = ", ".join(unit["itemNames"])
        cursor.execute("""
            INSERT INTO units (match_id, character_id, star_level, items)
            VALUES (?, ?, ?, ?)
        """, (
            match_id,
            unit["character_id"],
            unit["tier"],
            items_string,
        ))

    for trait in participant["traits"]:
        cursor.execute("""
            INSERT INTO traits (match_id, trait_name, num_units, style, tier_current, tier_total)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            match_id,
            trait["name"],
            trait["num_units"],
            trait["style"],
            trait["tier_current"],
            trait["tier_total"],
        ))    

    conn.commit()
    conn.close()

def get_units_for_match(match_id: str) -> list:
    """
    Returns every unit row for one specific match.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT character_id, star_level, items FROM units WHERE match_id = ?",
        (match_id,)
    )
    results = cursor.fetchall()
    conn.close()
    return results

def get_traits_for_match(match_id: str) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT trait_name, num_units, style, tier_current, tier_total FROM traits WHERE match_id = ?",
        (match_id,)
    )
    results = cursor.fetchall()
    conn.close()
    return results

def get_game(match_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT match_id, placement, level, gold_left, last_round, set_number,
               game_datetime, queue_id, rank_tier, rank_division, league_points, total_damage,
               companion_content_id, companion_item_id
        FROM games WHERE match_id = ?
    """, (match_id,))
    row = cursor.fetchone()
    conn.close()
    return row

if __name__ == "__main__":
    create_tables()
    print("Tables created (or already existed).")