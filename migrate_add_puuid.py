"""
One-off migration: adds a puuid column to games/units/traits (needed for
multi-player search — see database.py's create_tables for why) and backfills
every existing row with your own account's puuid, since this DB has only
ever held your data up to now. Safe to re-run: skips straight to "already
migrated" if games.puuid already exists.

games' primary key changes from match_id alone to (match_id, puuid), which
SQLite can't do via ALTER TABLE — so games specifically gets rebuilt
(create new table with the new key, copy rows across, swap names) rather
than just having a column added like units/traits.
"""
import shutil
import sqlite3

from database import DB_FILE, get_connection
from fetch_match import GAME_NAME, TAG_LINE, REGION, get_puuid


def _already_migrated(conn: sqlite3.Connection) -> bool:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    return "puuid" in columns


def migrate():
    conn = get_connection()

    if _already_migrated(conn):
        print("Already migrated — games.puuid exists. Nothing to do.")
        conn.close()
        return

    backup_path = DB_FILE + ".bak"
    conn.close()  # release the file handle before copying it
    shutil.copy2(DB_FILE, backup_path)
    print(f"Backed up {DB_FILE} to {backup_path}")

    conn = get_connection()
    cursor = conn.cursor()

    my_puuid = get_puuid(GAME_NAME, TAG_LINE, REGION)
    print(f"Resolved your puuid for {GAME_NAME}#{TAG_LINE}: {my_puuid}")

    cursor.execute("ALTER TABLE units ADD COLUMN puuid TEXT")
    cursor.execute("ALTER TABLE traits ADD COLUMN puuid TEXT")
    cursor.execute("ALTER TABLE games ADD COLUMN puuid TEXT")

    cursor.execute("UPDATE games SET puuid = ? WHERE puuid IS NULL", (my_puuid,))
    games_updated = cursor.rowcount
    cursor.execute("UPDATE units SET puuid = ? WHERE puuid IS NULL", (my_puuid,))
    units_updated = cursor.rowcount
    cursor.execute("UPDATE traits SET puuid = ? WHERE puuid IS NULL", (my_puuid,))
    traits_updated = cursor.rowcount
    print(f"Backfilled puuid on {games_updated} games, {units_updated} units, {traits_updated} traits rows.")

    # games needs its primary key changed (match_id alone -> (match_id,
    # puuid)) so a second tracked player sharing a match_id no longer
    # collides — SQLite has no ALTER TABLE for that, hence the rebuild.
    cursor.execute("""
        CREATE TABLE games_new (
            match_id TEXT,
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
            companion_item_id INTEGER,
            puuid TEXT,
            PRIMARY KEY (match_id, puuid)
        )
    """)
    cursor.execute("""
        INSERT INTO games_new
        SELECT match_id, placement, level, gold_left, last_round, set_number, game_datetime,
               queue_id, rank_tier, rank_division, league_points, total_damage,
               companion_content_id, companion_item_id, puuid
        FROM games
    """)
    # rowcount on an INSERT ... SELECT isn't reliable across sqlite3
    # driver versions — count the new table directly instead.
    row_count = cursor.execute("SELECT COUNT(*) FROM games_new").fetchone()[0]
    cursor.execute("DROP TABLE games")
    cursor.execute("ALTER TABLE games_new RENAME TO games")
    print(f"Rebuilt games with composite (match_id, puuid) primary key — {row_count} rows carried over.")

    conn.commit()
    conn.close()
    print("Migration complete.")


if __name__ == "__main__":
    migrate()
