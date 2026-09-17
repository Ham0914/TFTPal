import os
import requests
from dotenv import load_dotenv
from game_data import build_champion_lookup, build_item_lookup, translate_items

# Load the RIOT_API_KEY from your .env file into the environment
load_dotenv()
API_KEY = os.getenv("RIOT_API_KEY")

# --- Fill these in with your own details ---
GAME_NAME = "Binu"
TAG_LINE = "Beans"
REGION = "europe"

HEADERS = {"X-Riot-Token": API_KEY}

# Riot scopes account/match lookups by continent ("cluster") and rank
# lookups by specific platform server — a player search needs both, picked
# together, since e.g. an NA player's account/match data lives under the
# "americas" cluster but their rank lives under the "na1" platform. metatft's
# rating-history endpoint (metatft_rating.py) also expects the platform-style
# code, not the cluster. This only covers the handful of regions the search
# UI exposes — easy to extend with more platform/cluster pairs later.
REGIONS = {
    "EUW": {"label": "EU West", "cluster": "europe", "platform": "euw1"},
    "EUNE": {"label": "EU Nordic & East", "cluster": "europe", "platform": "eun1"},
    "NA": {"label": "North America", "cluster": "americas", "platform": "na1"},
    "KR": {"label": "Korea", "cluster": "asia", "platform": "kr"},
}


def get_puuid(game_name: str, tag_line: str, region: str) -> str:
    url = f"https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()["puuid"]


def get_recent_match_ids(puuid: str, region: str, count: int = 5, start: int = 0) -> list:
    url = f"https://{region}.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids"
    params = {"count": count, "start": start}
    response = requests.get(url, headers=HEADERS, params=params)
    response.raise_for_status()
    return response.json()


def get_all_match_ids(puuid: str, region: str, page_size: int = 100) -> list:
    """
    Pages through the full match-id history for this puuid (oldest data
    Riot still retains, all the way to the most recent game) instead of
    just the most recent handful — a plain get_recent_match_ids(count=20)
    permanently missed everything past the 20 most-recent games, which
    meant a first-time search of a player with real history, or anyone
    who hadn't refreshed in a while, showed stats computed from an
    incomplete slice of their games. 100 is match-v1's own per-request
    max; a page shorter than that (including empty) means there's nothing
    left, which is how this loop knows when to stop.
    """
    all_ids = []
    start = 0
    while True:
        page = get_recent_match_ids(puuid, region, count=page_size, start=start)
        all_ids.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return all_ids


def get_match_details(match_id: str, region: str) -> dict:
    url = f"https://{region}.api.riotgames.com/tft/match/v1/matches/{match_id}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def get_my_participant_data(match_data: dict, my_puuid: str) -> dict:
    """
    Searches the list of 8 participants for the one matching our puuid.
    """
    for participant in match_data["info"]["participants"]:
        if participant["puuid"] == my_puuid:
            return participant
    raise ValueError("Could not find this player in the match data")

def print_summary(participant: dict):
    champion_lookup = build_champion_lookup()
    item_lookup = build_item_lookup()

    print(f"Placement: {participant['placement']}")
    print(f"Level: {participant['level']}")
    print(f"Gold left: {participant['gold_left']}")
    print(f"Last round reached: {participant['last_round']}")
    print()
    print("Units:")
    for unit in participant["units"]:
        name = champion_lookup.get(unit["character_id"], unit["character_id"])
        items_string = ", ".join(unit["itemNames"])
        readable_items = translate_items(items_string, item_lookup)
        star_level = unit["tier"]
        print(f"  {name} (★{star_level}) — items: {readable_items}")

# --- Main script logic (this is the ONLY entry point) ---
if __name__ == "__main__":
    from database import create_tables, save_match
    from rank import get_rank_for_match_queue
    import time

    create_tables()

    puuid = get_puuid(GAME_NAME, TAG_LINE, REGION)
    match_ids = get_all_match_ids(puuid, REGION)

    print(f"Found {len(match_ids)} total matches. Fetching and saving each...\n")

    # Ranked and Double Up have separate rank ladders — cache each queue's
    # current rank per run rather than re-fetching it for every match.
    rank_cache = {}

    def rank_for_queue(match_queue_id):
        if match_queue_id not in rank_cache:
            try:
                rank_cache[match_queue_id] = get_rank_for_match_queue(puuid, match_queue_id)
            except Exception as e:
                print(f"⚠️  Couldn't fetch current rank for queue {match_queue_id} ({e}) — saving without one.")
                rank_cache[match_queue_id] = None
        return rank_cache[match_queue_id]

    for match_id in match_ids:
        try:
            match_data = get_match_details(match_id, REGION)
            rank_info = rank_for_queue(match_data["info"]["queueId"])
            save_match(match_data, puuid, rank_info=rank_info)
        except Exception as e:
            print(f"⚠️  Failed to process {match_id}: {e}")
            continue
        time.sleep(1.2)  # be polite to Riot's rate limits — see note below

    print("\nDone. All matches processed.")


