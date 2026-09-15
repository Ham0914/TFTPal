"""
One-off backfill: our own database only has rank snapshots going forward
from whenever watcher.py/fetch_match.py first started attaching them —
older matches all got stamped with today's current rank, which is wrong
for match history display. metatft.com has been tracking this account's
rank after (most of) its games for a while, so we use their public
rating_changes history (see metatft_rating.py) to reconstruct a much
closer approximation of "what was my rank right after this game" for
each already-recorded match, matched by nearest timestamp.

Our own game count and metatft's don't necessarily match exactly (they
may have missed a game, or started tracking later/earlier), so this is
a best-effort nearest-timestamp match, not a guaranteed exact one.
"""
import re
from datetime import datetime, timezone
from database import get_connection
from fetch_match import GAME_NAME, TAG_LINE
from metatft_rating import fetch_rating_history

RATING_TEXT_RE = re.compile(r"^([A-Z]+)\s*([IVX]*)\s*(\d+)\s*LP$")


def parse_rating_text(rating_text: str):
    m = RATING_TEXT_RE.match(rating_text)
    if not m:
        return None, None, None
    tier, division, lp = m.groups()
    return tier, (division or None), int(lp)


def iso_to_epoch_ms(iso_timestamp: str) -> int:
    dt = datetime.fromisoformat(iso_timestamp).replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def backfill(queue_id: int = 1100):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT match_id, game_datetime FROM games WHERE queue_id = ? ORDER BY game_datetime",
        (queue_id,),
    )
    our_games = cursor.fetchall()

    rating_changes = fetch_rating_history(GAME_NAME, TAG_LINE, queue_id=queue_id)
    if not rating_changes:
        print("No metatft rating history available — nothing to backfill.")
        conn.close()
        return

    metatft_points = [(iso_to_epoch_ms(r["created_timestamp"]), r["rating_text"]) for r in rating_changes]

    updated = 0
    for match_id, game_datetime in our_games:
        nearest_text = min(metatft_points, key=lambda p: abs(p[0] - game_datetime))[1]
        tier, division, lp = parse_rating_text(nearest_text)
        if tier is None:
            continue
        cursor.execute(
            "UPDATE games SET rank_tier = ?, rank_division = ?, league_points = ? WHERE match_id = ?",
            (tier, division, lp, match_id),
        )
        updated += 1

    conn.commit()
    conn.close()
    print(f"Backfilled {updated}/{len(our_games)} queue {queue_id} games using {len(rating_changes)} metatft rank snapshots.")


if __name__ == "__main__":
    backfill(queue_id=1100)
