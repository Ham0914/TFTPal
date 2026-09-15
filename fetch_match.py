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


def get_puuid(game_name: str, tag_line: str, region: str) -> str:
    url = f"https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()["puuid"]


def get_recent_match_ids(puuid: str, region: str, count: int = 5) -> list:
    url = f"https://{region}.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids"
    params = {"count": count}
    response = requests.get(url, headers=HEADERS, params=params)
    response.raise_for_status()
    return response.json()


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
    match_ids = get_recent_match_ids(puuid, REGION, count=30)

    print(f"Found {len(match_ids)} recent matches. Fetching and saving each...\n")

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


