import time
from database import create_tables, save_match, get_connection
from fetch_match import get_puuid, get_recent_match_ids, get_match_details, GAME_NAME, TAG_LINE, REGION
from rank import get_rank_for_match_queue
import requests

POLL_INTERVAL_SECONDS = 90  # how often to check for a new match


def get_saved_match_ids() -> set:
    """
    Returns the set of match_ids already stored, so we can tell
    which recent matches (if any) are genuinely new.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT match_id FROM games")
    ids = {row[0] for row in cursor.fetchall()}
    conn.close()
    return ids


def watch(puuid: str):
    print(f"Watching for new matches every {POLL_INTERVAL_SECONDS} seconds. Press Ctrl+C to stop.")

    while True:
        try:
            saved_ids = get_saved_match_ids()
            recent_ids = get_recent_match_ids(puuid, REGION, count=5)
            new_ids = [mid for mid in recent_ids if mid not in saved_ids]

            for match_id in new_ids:
                print(f"\nNew match detected: {match_id}")
                match_data = get_match_details(match_id, REGION)
                match_queue_id = match_data["info"]["queueId"]

                try:
                    rank_info = get_rank_for_match_queue(puuid, match_queue_id)
                except requests.exceptions.HTTPError as e:
                    print(f"⚠️  Couldn't fetch current rank ({e}) — saving this match without one.")
                    rank_info = None

                save_match(match_data, puuid, rank_info=rank_info)
                print(f"Saved with rank snapshot: {rank_info}")

        except requests.exceptions.HTTPError as e:
            print(f"\n⚠️  API error: {e}")
            print("If this is a 401, your Riot API key has likely expired (24h limit).")
            print("Regenerate it at developer.riotgames.com, update .env, and this will recover automatically.")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    create_tables()
    puuid = get_puuid(GAME_NAME, TAG_LINE, REGION)

    try:
        watch(puuid)
    except KeyboardInterrupt:
        print("\nStopped watching.")