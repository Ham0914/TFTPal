from fetch_match import get_puuid, GAME_NAME, TAG_LINE, REGION
from rank import get_current_rank

puuid = get_puuid(GAME_NAME, TAG_LINE, REGION)
rank = get_current_rank(puuid)
print(rank)