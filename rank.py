import os
import requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("RIOT_API_KEY")
HEADERS = {"X-Riot-Token": API_KEY}

PLATFORM = "euw1"  # platform routing — different from "europe" used for match data


def get_current_rank(puuid: str, platform: str = PLATFORM) -> dict:
    """
    Returns your current Ranked TFT standing as a dict, or None if
    you're currently unranked (no entry for this queue yet).
    """
    url = f"https://{platform}.api.riotgames.com/tft/league/v1/by-puuid/{puuid}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    entries = response.json()

    for entry in entries:
        if entry["queueType"] == "RANKED_TFT":
            return {
                "tier": entry["tier"],
                "division": entry["rank"],  # Riot calls the I/II/III/IV field "rank", confusingly
                "league_points": entry["leaguePoints"],
            }

    return None  # no ranked entry found — you're unranked