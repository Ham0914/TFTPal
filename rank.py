import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("RIOT_API_KEY")
HEADERS = {"X-Riot-Token": API_KEY}

PLATFORM = "euw1"  # platform routing — different from "europe" used for match data

# Double Up has its own separate ranked ladder from standard Ranked — Riot
# reports it under a different queueType, so using "RANKED_TFT" for every
# match regardless of which queue it was actually played in gives Double Up
# games the wrong (standard-queue) rank. Normal (1090) has no ranked ladder
# at all, so it's deliberately absent from this map.
QUEUE_ID_TO_RANKED_TYPE = {
    1100: "RANKED_TFT",
    1160: "RANKED_TFT_DOUBLE_UP",
}


def get_current_rank(puuid: str, queue_type: str = "RANKED_TFT", platform: str = PLATFORM) -> dict:
    """
    Returns your current standing in the given ranked queue as a dict, or
    None if you're currently unranked there (no entry for this queue yet).
    """
    url = f"https://{platform}.api.riotgames.com/tft/league/v1/by-puuid/{puuid}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    entries = response.json()

    for entry in entries:
        if entry["queueType"] == queue_type:
            return {
                "tier": entry["tier"],
                "division": entry["rank"],  # Riot calls the I/II/III/IV field "rank", confusingly
                "league_points": entry["leaguePoints"],
            }

    return None  # no ranked entry found — you're unranked in this queue


def get_rank_for_match_queue(puuid: str, match_queue_id: int, platform: str = PLATFORM) -> dict:
    """
    Looks up the right ranked ladder for whichever queue a match was
    actually played in. Returns None for queues with no ranked ladder
    (e.g. Normal) without making an API call.
    """
    queue_type = QUEUE_ID_TO_RANKED_TYPE.get(match_queue_id)
    if queue_type is None:
        return None
    return get_current_rank(puuid, queue_type=queue_type, platform=platform)