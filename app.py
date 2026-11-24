import json
import os
import random
import time
import streamlit as st

# Hosted deployments keep the Riot key in Streamlit secrets; locally it comes
# from .env. Must run before fetch_match/rank read it at import time.
try:
    os.environ.setdefault("RIOT_API_KEY", st.secrets["RIOT_API_KEY"])
except (FileNotFoundError, KeyError):
    pass

from stats import average_placement, top4_rate, win_rate, placement_breakdown, most_played_units, most_played_traits, match_history
from fetch_match import GAME_NAME, TAG_LINE, REGIONS, get_puuid, get_all_match_ids, get_match_details
from rank import get_rank_for_match_queue
from database import get_game, get_connection, save_match, create_tables
from match_detail import placement_color
from match_detail import get_match_detail, time_ago, ordinal
from match_detail import get_metatft_champion_lookup, resolve_metatft_markup, clean_game_text
from match_detail import resolve_trait_breakpoints, trait_style_color, corrected_trait_style
from game_data import build_champion_data, tex_path_to_url, build_companion_lookup, companion_icon_url, mirrored_url
from game_data import build_trait_data, build_trait_static_data
from metatft_rating import fetch_rating_history, short_rank_label
import altair as alt
import pandas as pd
import streamlit.components.v1 as components
from team_builder import build_team_builder_html

st.set_page_config(layout="wide")


@st.cache_data(ttl=600)
def _cached_rating_history(game_name: str, tag_line: str, platform: str = "euw1", queue_id: int = 1100) -> list:
    try:
        return fetch_rating_history(game_name, tag_line, region=platform, queue_id=queue_id)
    except Exception:
        return []


def _rating_queue_for_filter(selected_queue_id):
    """
    Ranked and Double Up have separate LP ladders. Normal has no ladder at
    all, and "All modes" doesn't map to one specific ladder — for both of
    those, Ranked is shown as the meaningful default rather than nothing.
    Returns (queue_id_to_fetch, label) or (None, None) if there's truly no
    ladder to show (Normal).
    """
    if selected_queue_id == 1160:
        return 1160, "Double Up"
    if selected_queue_id == 1090:
        return None, None
    return 1100, "Ranked"


PUUID_CACHE_FILE = "puuid_cache.json"


def _load_puuid_cache() -> dict:
    try:
        with open(PUUID_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


@st.cache_resource
def _cached_puuid(game_name: str, tag_line: str, region: str) -> str:
    # PUUID never changes for a given Riot ID, so this is safe to cache for
    # the life of the server process rather than re-hitting the Account API
    # on every refresh-button click. Keyed on (game_name, tag_line, region),
    # so this naturally caches per-player correctly once search can pass in
    # someone other than the default account.
    # Also kept on disk, so already-saved matches still load when Riot's API
    # is unavailable (dev keys expire every 24h).
    cache_key = f"{game_name}#{tag_line}".lower()
    try:
        puuid = get_puuid(game_name, tag_line, region)
    except Exception:
        saved = _load_puuid_cache().get(cache_key)
        if saved is None:
            raise
        return saved
    saved_puuids = _load_puuid_cache()
    if saved_puuids.get(cache_key) != puuid:
        saved_puuids[cache_key] = puuid
        with open(PUUID_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(saved_puuids, f)
    return puuid


# The account this app defaults to on load and falls back to via "Back to
# my stats" — unrelated to whoever else gets searched in a given session.
DEFAULT_PLAYER = {"game_name": GAME_NAME, "tag_line": TAG_LINE, "region_key": "EUW"}


def _reroll_hero_backgrounds():
    """
    Clears the cached hero/background champion picks so the next rerun
    rolls fresh ones — called whenever the viewed player changes (search,
    or "Back to my stats") so the artwork feels tied to whoever you're
    looking at instead of staying fixed to whatever was rolled on first
    page load for the entire session.
    """
    st.session_state.pop("hero_champion", None)
    st.session_state.pop("bg_champion", None)


def _handle_player_search(name: str, tag: str, region_key: str) -> None:
    """
    Shared by both the Home page's hero search and the dashboard's own
    search box — one place owning "look this Riot ID up, switch to it,
    pull their matches, tell the user what happened" instead of two
    copies that could drift. On success this also routes to the stats
    page via query params — the mechanism that makes searching from Home
    land you on the dashboard.
    """
    name = name.strip()
    tag = tag.strip().lstrip("#")
    query = f"{name}#{tag}"
    if not name or not tag:
        _dismissible_alert("Enter both a name and a tag to search for.", "warning")
        return
    try:
        # Riot's account lookup is global (works under any cluster for any
        # player), so this succeeding proves nothing about whether the
        # REGION picked is right — only match/rank data is actually
        # region-scoped. Confirmed live: querying a EUW account's matches
        # under "americas" doesn't error, it just silently returns zero
        # matches. The real region check has to happen after fetching, below.
        found_puuid = get_puuid(name, tag, REGIONS[region_key]["cluster"])
    except Exception:
        _dismissible_alert(f"Couldn't find {query} on {region_key}.", "error")
        return
    st.session_state["viewed_player"] = {
        "game_name": name, "tag_line": tag,
        "region_key": region_key, "puuid": found_puuid,
    }
    _reroll_hero_backgrounds()
    with st.status(f"Fetching {name}'s match history...", expanded=True) as status:
        def _report_progress(saved_count):
            status.update(label=f"Fetching {name}'s match history — {saved_count} match(es) saved so far...")
        refresh_result = refresh_player_matches(_get_viewed_player(), on_progress=_report_progress)
        status.update(label="Match history fetched.", state="complete")
    if "error" in refresh_result:
        _dismissible_alert(f"Found {query}, but couldn't fetch their matches: " + refresh_result["error"], "error")
    elif refresh_result["new_matches"] == 0 and not match_history(found_puuid, queue_id=None):
        _dismissible_alert(f"No matches found for {query} on {region_key} — wrong region? Try another.", "warning")
    else:
        st.query_params["page"] = "stats"
        st.rerun()


def _get_viewed_player() -> dict:
    """
    Returns the player currently being viewed, resolved to a puuid and
    merged with that region's cluster/platform codes. On the very first
    call in a session, seeds from the URL's name/tag/region query params
    if present (shareable-link support — see the matching write side in
    the dashboard body, right after this is first called there) before
    falling back to DEFAULT_PLAYER (your own account).
    """
    if "viewed_player" not in st.session_state:
        qp_name = st.query_params.get("name")
        qp_tag = st.query_params.get("tag")
        qp_region = st.query_params.get("region")
        if qp_name and qp_tag and qp_region in REGIONS:
            try:
                # Resolved eagerly (not lazily below) so a stale or
                # mistyped shared link — a real risk here in a way it
                # isn't for search, since _handle_player_search already
                # validates a puuid before ever storing one — degrades to
                # your own stats instead of crashing the whole page the
                # first time anything downstream needs this puuid.
                candidate_puuid = _cached_puuid(qp_name, qp_tag, REGIONS[qp_region]["cluster"])
            except Exception:
                st.session_state["viewed_player"] = dict(DEFAULT_PLAYER)
                _dismissible_alert(f"Couldn't load {qp_name}#{qp_tag} from that link — showing your own stats instead.", "warning")
            else:
                st.session_state["viewed_player"] = {
                    "game_name": qp_name, "tag_line": qp_tag, "region_key": qp_region, "puuid": candidate_puuid,
                }
        else:
            st.session_state["viewed_player"] = dict(DEFAULT_PLAYER)
    player = st.session_state["viewed_player"]
    region = REGIONS[player["region_key"]]
    if "puuid" not in player:
        player["puuid"] = _cached_puuid(player["game_name"], player["tag_line"], region["cluster"])
    return {**player, **region}


def refresh_player_matches(player: dict, on_progress=None) -> dict:
    """
    Manual "catch me up" action for the Refresh button: checks the most
    recent matches on Riot's side, saves any not already in the database
    (with a rank snapshot, same as watcher.py does), and reports what
    happened so the button can show a real result instead of just
    disappearing. Returns {"new_matches": int} on success, or
    {"error": str} if something failed (e.g. an expired Riot API key) —
    deliberately caught here rather than left to crash the whole page,
    since this runs from a button click, not app startup.

    A first-time search can mean dozens of new matches, each paced at
    1.2s to respect Riot's rate limit (see below) — 45-60s isn't unusual,
    and that's a floor set by Riot's own key limits, not something this
    function can shortcut. `on_progress(saved_count)`, if given, is called
    after each match is saved so the caller can show real movement during
    that wait instead of a silent spinner — not a percentage, since the
    true total isn't known until the current-set boundary is actually hit
    (see below), and showing a fake/stuck-looking percentage would read
    as more broken than no progress bar at all.
    """
    try:
        create_tables()
        puuid = player["puuid"]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT match_id FROM games WHERE puuid = ?", (puuid,))
        saved_ids = {row[0] for row in cursor.fetchall()}
        conn.close()

        # Paginates through this player's match-id history (a plain
        # count=20 permanently missed everything older than that, which
        # meant a first-time search of a player with real history computed
        # stats from an incomplete slice of their games). Listing ids is
        # cheap regardless of history length (~1 API call per 100 games) —
        # the expensive part is the per-match detail fetch below, which is
        # bounded to the current set (see the break below), not run for
        # every id here.
        all_ids = get_all_match_ids(puuid, player["cluster"])
        new_ids = [mid for mid in all_ids if mid not in saved_ids]

        # match-v1 returns ids newest-first, and every match carries its
        # own real tft_set_number — so instead of guessing this set's
        # launch date (wrong in either direction: too early wastes time
        # re-fetching older sets nobody's asking to see, too late silently
        # drops real games), just fetch forward until a match turns out to
        # be from an older set, then stop. Self-correcting regardless of
        # when the current set actually started, and works the same for a
        # brand-new player's first full sync as for a routine top-up.
        CURRENT_SET = 18
        rank_cache = {}
        new_matches_saved = 0
        for match_id in new_ids:
            match_data = get_match_details(match_id, player["cluster"])
            if match_data["info"]["tft_set_number"] < CURRENT_SET:
                break
            match_queue_id = match_data["info"]["queueId"]
            if match_queue_id not in rank_cache:
                try:
                    rank_cache[match_queue_id] = get_rank_for_match_queue(puuid, match_queue_id, platform=player["platform"])
                except Exception:
                    rank_cache[match_queue_id] = None
            save_match(match_data, puuid, rank_info=rank_cache[match_queue_id])
            new_matches_saved += 1
            if on_progress is not None:
                on_progress(new_matches_saved)
            # A first-time search or a long-idle refresh can suddenly mean
            # dozens/hundreds of new matches instead of a handful — pace
            # requests the same way fetch_match.py's own full-sync does,
            # so a big backlog doesn't blow through Riot's rate limits.
            time.sleep(1.2)

        return {"new_matches": new_matches_saved}
    except Exception as e:
        return {"error": str(e)}


def _dismissible_alert(text: str, kind: str = "info", align: str = "left") -> None:
    """
    Like st.info/warning/success/error, but auto-fades after ~10s (see the
    .tft-alert-fade-a/-b CSS animations) — which native st.info etc. can't
    do reliably here. Confirmed live via DevTools, in three rounds:

    1. A CSS animation on native st.info/warning/etc.'s own
       [data-testid="stAlert"] only ever plays once: Streamlit patches that
       element's text in place across reruns rather than creating a fresh
       DOM node, so on a second click within 10.5s of the first, the "new"
       alert appeared already faded — the browser considered the animation
       (tied to that one persistent element) already finished.
    2. Assumed switching to a custom st.markdown div would fix it by
       forcing a full innerHTML replacement (destroying/recreating the
       node) — verified via a standalone innerHTML test that this DOES
       correctly restart a CSS animation in general. But Streamlit's own
       markdown renderer turned out not to work that way: per
       getAnimations()[0].currentTime, even a div with a fresh data-ts
       attribute (proving new HTML was sent) came back already finished —
       Streamlit is patching attributes/text on the SAME persistent node
       here too, not replacing it outright.
    3. Real fix: alternate which of two CSS classes (identical styling,
       different @keyframes NAME) gets applied each call. Verified via a
       standalone test that changing an element's animation-name restarts
       its animation from 0 even when it's the exact same DOM node — so
       this works regardless of whether Streamlit reuses the node.
    """
    colors = {
        "info": ("#5b9bd5", "rgba(44,141,224,0.12)"),
        "warning": ("var(--tft-gold)", "rgba(212,175,55,0.12)"),
        "success": ("#3ba55c", "rgba(59,165,92,0.12)"),
        "error": ("#e05a5a", "rgba(192,57,43,0.12)"),
    }
    color, bg = colors.get(kind, colors["info"])
    toggle = 1 - st.session_state.get("_alert_variant_toggle", 0)
    st.session_state["_alert_variant_toggle"] = toggle
    variant_class = "tft-alert-fade-a" if toggle == 0 else "tft-alert-fade-b"
    # display:inline-block (not the default block) so the pill shrinks to
    # fit short messages like "Up to date" instead of stretching to fill
    # whatever column/container it happens to sit in — max-width keeps
    # longer messages wrapping within that space rather than overflowing it.
    # The outer div's text-align controls which side of that space the now-
    # shrunk pill sits against (e.g. "right" to hug a button immediately to
    # its right, like the Match History refresh button).
    st.markdown(
        "<div style='text-align:" + align + ";'>"
        "<div class='" + variant_class + "' style='background:" + bg + ";color:" + color + ";"
        "border-radius:8px;padding:8px 14px;font-size:14px;margin-bottom:8px;"
        "display:inline-block;max-width:100%;box-sizing:border-box;text-align:left;'>" + text + "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def _riot_disclaimer_html() -> str:
    """
    The standard "not affiliated with Riot" disclaimer every third-party
    TFT/LoL site carries (Mobalytics, TFTactics.gg, etc. all have one) —
    added as a habit now rather than a launch-day scramble later. Shown
    on both Home and the dashboard, styled as quiet footer text.
    """
    return (
        "<div style='text-align:center;padding:28px 16px 12px;color:#666;font-size:11.5px;line-height:1.6;'>"
        "TFTPal isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games "
        "or anyone officially involved in producing or managing Riot Games properties. Riot Games and all "
        "associated properties are trademarks or registered trademarks of Riot Games, Inc."
        "</div>"
    )

# --- Icons (hand-drawn inline SVGs — no dedicated Riot/tactics.tools asset
# exists for generic dashboard concepts like "placement" or "damage", and a
# consistent thin gold-line set reads better than mixing found PNGs) ---
def svg_icon(path: str, size: int = 18, color: str = "#d4af37") -> str:
    return (
        f"<svg width='{size}' height='{size}' viewBox='0 0 24 24' fill='none' "
        f"stroke='{color}' stroke-width='1.6' stroke-linecap='round' stroke-linejoin='round' "
        f"style='vertical-align:-4px;margin-right:4px;'>{path}</svg>"
    )

ICON_TROPHY = "<path d='M8 21h8M12 17v4M7 4h10v4a5 5 0 0 1-10 0V4Z'/><path d='M7 5H4a1 1 0 0 0-1 1v1a4 4 0 0 0 4 4M17 5h3a1 1 0 0 1 1 1v1a4 4 0 0 1-4 4'/>"
ICON_SHIELD = "<path d='M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6l7-3Z'/>"
ICON_FLAG = "<path d='M5 21V4M5 4h13l-3 4 3 4H5'/>"
ICON_SWORD = "<path d='M14.5 3.5 20 9l-9 9-3-1-1-3 9-9Z'/><path d='M11 14l-6 6M4 20l-1-3 3 1'/>"
ICON_COIN = "<circle cx='12' cy='12' r='8'/><path d='M9.5 9.5c0-1 1-1.5 2.5-1.5s2.5.5 2.5 1.5-1 1.3-2.5 1.5-2.5.5-2.5 1.5 1 1.5 2.5 1.5 2.5-.5 2.5-1.5'/>"

st.markdown("""
    <style>
    :root {
        --tft-gold: #d4af37;
        --tft-gold-dim: #8a723a;
        --tft-bg-deep: #0a0c10;
        --tft-bg-panel: #14171f;
        --tft-bg-raised: #1a1d26;
        --tft-border: #2e323d;
    }

    /* No background-color on .stApp itself — verified live via DevTools that
       a solid background-color here paints UNDER any z-index:-1 child (the
       classic "negative z-index disappears behind the parent's own
       background" trap). That's why this hex/vignette layer AND the
       pre-existing hero-splash backdrop (previously its own separate
       .stApp::before) were both completely invisible before this fix — not
       just too subtle, literally never painted. The two are now combined
       into a single dynamically-built ::before (see the hero-splash section
       below) instead of two competing negative-z-index pseudo-elements, so
       there's only one stacking order to reason about. The actual layers
       (vignette, hex tile, optional splash) are built dynamically in Python
       right after the hero splash is chosen, below, since the hex lines need
       to be positioned ABOVE the splash-darkening veil in the same layer
       stack to stay visible as a texture over it — on separate pseudo-
       elements, the veil (nearly opaque, needed to darken a full-brightness
       splash to a tasteful level) completely hid the hex tile beneath it. */
    .stApp {
        background-color: transparent;
        /* Makes the --hero-vis named view-timeline (declared on
           #tft-hero-card below) visible to the compact sticky bar too, even
           though that bar lives elsewhere in the tree (a sibling markdown
           block, not a descendant of the card) — without this, a named
           view-timeline is only visible to the declaring element's own
           descendants per spec, and the compact bar's animation-timeline
           reference silently fails to resolve. Confirmed live via DevTools:
           opacity stayed frozen at the wrong value at every scroll position
           until this was added. */
        timeline-scope: --hero-vis;
    }

    /* padding-bottom needs !important — confirmed live via DevTools that
       Streamlit's own default h2/h3 padding-bottom (16px) was winning over
       a plain override here, and separately that .tft-divider's own
       margin (below) was being completely ignored the same way, for the
       same reason: Streamlit's built-in element styles load in a way that
       wins the cascade despite equal/lower specificity. Together these
       two defaults (16px heading padding + 32px hr margin-top) were the
       real source of the oversized gap between a heading/selector row and
       the divider line under it — tightened here without touching the
       flex gap Streamlit puts BETWEEN elements generally, which is what
       supplies the (intentionally kept) larger space ABOVE each heading. */
    h1, h2, h3 { letter-spacing: 0.5px; text-shadow: 0 2px 6px rgba(0,0,0,0.6); padding-bottom: 4px !important; }

    .tft-divider {
        height: 2px; margin: 2px 0 18px 0 !important; border: none;
        background: linear-gradient(90deg, var(--tft-gold) 0%, rgba(212,175,55,0.08) 70%, transparent 100%);
    }

    .tft-tooltip-wrap { position: relative; display: inline-block; }
    .tft-tooltip-content {
        visibility: hidden; opacity: 0;
        position: absolute; bottom: 105%; left: 50%;
        transform: translateX(-50%);
        background: var(--tft-bg-deep); border-radius: 8px;
        width: 280px; min-width: 280px; max-width: 280px;
        box-sizing: border-box;
        font-size: 12px; color: #ddd;
        z-index: 1000; transition: opacity 0.15s ease; pointer-events: none;
        overflow: hidden; text-align: left; line-height: 1.5;
        overflow-wrap: break-word; word-break: break-word;
        box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    }
    .tft-tooltip-wrap:hover .tft-tooltip-content { visibility: visible; opacity: 1; }

    details.tft-card {
        border-radius: 10px !important;
        /* Was a fully-opaque hex gradient (100% solid, blocking the backdrop
           entirely) — switched to the same semi-opaque rgba level as the
           st.container(border=True) boxes below, so every "card" on the
           page reads consistently rather than match history alone being
           fully solid while everything else was fully transparent. */
        background: linear-gradient(180deg, rgba(20,23,31,0.7) 0%, rgba(16,19,26,0.7) 100%) !important;
        box-shadow: 0 2px 10px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.04);
        /* --card-accent is set inline per-card (the match's placement
           color) so hover feedback reads as "this specific match lit up",
           not a generic one-size highlight — same idea as the border-left
           accent each card already carries. */
        transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
    }
    details.tft-card:hover {
        transform: translateY(-2px);
        border-color: var(--card-accent, var(--tft-gold)) !important;
        box-shadow:
            0 10px 26px -6px color-mix(in srgb, var(--card-accent, var(--tft-gold)) 40%, transparent),
            0 2px 12px rgba(0,0,0,0.4),
            inset 0 1px 0 rgba(255,255,255,0.06);
    }
    /* st.container(border=True) boxes (Last N Games, the two charts, Most
       Played Units) render as a plain stVerticalBlock with Streamlit's own
       default border and a fully transparent background — against the
       hextech backdrop they read as invisible outlines. There's no stable
       official data-testid for "this vertical block is the bordered-
       container kind" in this Streamlit version (no separate
       stVerticalBlockBorderWrapper like older versions had), so this
       targets the emotion-cache class Streamlit currently generates for it
       specifically — confirmed live via DevTools that all 4 bordered boxes
       on this page share this exact class and no unbordered block does.
       Fragile against a Streamlit version upgrade changing that generated
       class; if these boxes lose their fill after an upgrade, re-inspect
       and update the class name here. */
    div[data-testid="stVerticalBlock"].st-emotion-cache-1fpp8sd {
        background: rgba(20,23,31,0.7);
    }
    details.tft-card summary { list-style: none; }
    details.tft-card summary::-webkit-details-marker { display: none; }
    /* The summary's border-left accent stripe is drawn on its own child
       box, which doesn't automatically pick up the outer <details>'s
       rounded corners — left as a flat 90-degree edge, it visibly pokes
       past the parent's curve right where hover brightens the border,
       which is what made it read as "sharp" against everything else's
       rounded look. 9px (not 10px) so the stripe's curve sits concentric
       just inside the outer 1px border rather than slightly outside it.
       Closed, the summary IS the whole visible card, so both left corners
       round; open, the content panel below already rounds its own
       bottom-left corner (see the left_html wrapper below), so the
       summary's bottom-left corner squares off to butt against it instead
       of leaving a rounded notch. */
    /* border-radius transitions here use the exact same durations/easings
       as the height animation on ::details-content above (0.4s
       cubic-bezier expand, 0.32s ease-in collapse) so the corner unrounds/
       rounds in step with the panel opening and closing, instead of
       snapping instantly the moment [open] toggles. */
    details.tft-card summary { border-radius: 9px 0 0 9px; transition: border-radius 0.32s ease-in; }
    details.tft-card[open] summary { border-radius: 9px 0 0 0; transition: border-radius 0.4s cubic-bezier(0.22, 1, 0.36, 1); }
    .tft-card-chevron { transition: transform 0.15s ease; }
    details.tft-card[open] .tft-card-chevron { transform: rotate(180deg); }

    /* Smooth open/close, targeting the real mechanism this time. Modern
       Chromium (verified live via DevTools against this exact browser,
       Chrome/153 under this Brave install) implements <details> disclosure
       through an internal ::details-content pseudo-element — an anonymous
       box wrapping everything except <summary> — which toggles
       content-visibility and block-size on ITSELF when [open] changes.
       That box is invisible to normal CSS/DOM inspection unless targeted
       directly via ::details-content, which is why three earlier attempts
       styling our own .tft-card-content div (display override, then +
       content-visibility, then a grid-rows transition) all did nothing:
       none of them were touching the element actually doing the hiding.
       interpolate-size lets height animate between a length and "auto"
       instead of snapping; content-visibility gets allow-discrete so it
       flips at the right end of the transition instead of instantly.
       Confirmed via direct frame sampling (getBoundingClientRect over
       several rAF ticks) to ramp smoothly across repeated open/close
       cycles, not just the first.

       Easing is asymmetric on purpose, each verified the same way: a
       transition's timing-function is taken from whichever rule the
       element is transitioning TO, so the reveal (ease-out-ish, defined
       on [open]) and the collapse (plain ease-in, defined on the base
       rule) can differ. Plain "ease" on both directions measured with a
       visible slow-then-fast-then-slow wobble on open; a steeper custom
       ease-in tried for close (cubic-bezier(0.7,0,0.84,0)) measured
       worse — it held nearly flat for ~80% of the duration and then
       snapped shut in the last couple of frames. Standard "ease-in"
       measured as a smooth, steadily-accelerating collapse instead. */
    details.tft-card { interpolate-size: allow-keywords; }
    details.tft-card::details-content {
        height: 0;
        overflow: hidden;
        opacity: 0;
        transition: height 0.32s ease-in, opacity 0.2s ease-in, overflow 0.32s allow-discrete, content-visibility 0.32s allow-discrete;
    }
    /* overflow:visible here (not hidden) is the actual fix for unit/trait
       tooltips getting a flat clip where they crossed this box's top edge:
       tooltips are position:absolute descendants whose containing block
       (.tft-tooltip-wrap) lives inside this pseudo-element, so as long as
       it stays overflow:hidden they can never escape upward past it,
       however far below the card top the hovered icon sits. allow-discrete
       makes this switch to visible only once the expand transition
       finishes (not instantly on open), same reasoning as
       content-visibility above it — during the height 0->auto animation
       overflow needs to stay hidden or the not-yet-"grown-into" content
       would flash fully visible outside the still-small box. On close it
       switches back to hidden only once the collapse transition finishes,
       for the same reason in reverse. */
    details.tft-card[open]::details-content {
        height: auto;
        overflow: visible;
        opacity: 1;
        transition: height 0.4s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.25s ease-out, overflow 0.4s allow-discrete, content-visibility 0.4s allow-discrete;
    }

    /* Sidebar theme, and the game-mode selectbox that lives there */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, var(--tft-bg-panel) 0%, var(--tft-bg-deep) 100%);
        border-right: 1px solid var(--tft-border);
    }
    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
        background-color: var(--tft-bg-raised) !important;
        border: 1px solid rgba(212,175,55,0.35) !important;
        border-radius: 8px !important;
        font-weight: 600 !important;
        color: #fff !important;
        cursor: pointer !important;
    }
    div[data-testid="stSelectbox"] div[data-baseweb="select"],
    div[data-testid="stSelectbox"] div[data-baseweb="select"] *,
    div[data-testid="stSelectbox"] input {
        cursor: pointer !important;
    }
    div[data-testid="stSelectbox"] svg { fill: var(--tft-gold) !important; }

    /* The little "Press Enter to submit form" / "Press Enter to apply"
       hint Streamlit shows under a text input while it has unsaved
       changes — official stable testid, no fragile emotion-cache class
       needed here. */
    [data-testid="InputInstructions"] { display: none; }

    /* !important is needed because each nav item's color/border are set
       inline (per-item, active vs inactive) — a plain :hover class rule
       can't outrank an inline style's specificity without it. */
    .tft-nav-item { transition: color 0.15s ease, border-color 0.15s ease; }
    .tft-nav-item:hover { color: var(--tft-gold) !important; border-bottom-color: var(--tft-gold) !important; }

    /* Auto-dismiss for the search/refresh feedback messages — stays fully
       visible for ~10s, then fades over the last 0.5s. Pure CSS,
       deliberately not a JS setTimeout: plain <script> tags inside
       st.markdown(unsafe_allow_html=True) never execute (browsers don't
       run scripts inserted via innerHTML), and the real fix for that —
       st.components.v1.html's cross-frame DOM hack — was already tried
       for a different feature in this app and cost three rounds of blind
       iteration before being abandoned.
       TWO identical-looking classes, not one: confirmed live via DevTools
       (getAnimations()[0].currentTime) that Streamlit patches this element
       in place across reruns rather than replacing it — even attaching a
       constantly-changing data-ts attribute to prove fresh HTML was sent
       didn't help, the animation still came back already finished on a
       second trigger. What DOES reliably restart a CSS animation on a
       persistent, never-recreated node is changing the animation-name
       itself — verified with a standalone test. _dismissible_alert
       alternates the class between these two on every call for exactly
       that reason; don't collapse them back into one class + one
       @keyframes without re-verifying, it'll silently reintroduce the
       "second click shows nothing" bug. */
    .tft-alert-fade-a { animation: tft-alert-fade-a 10.5s ease forwards; }
    .tft-alert-fade-b { animation: tft-alert-fade-b 10.5s ease forwards; }
    @keyframes tft-alert-fade-a {
        0%, 95% { opacity: 1; }
        100% { opacity: 0; pointer-events: none; }
    }
    @keyframes tft-alert-fade-b {
        0%, 95% { opacity: 1; }
        100% { opacity: 0; pointer-events: none; }
    }

    #tft-hero-card {
        /* position:relative is load-bearing, not decorative: without it this
           element is position:static, so its position:absolute children
           (inset:0 overlay, top/left text blocks) fall back to the nearest
           positioned ancestor — none exists here, so they end up positioned
           against the viewport instead. overflow:hidden still clips their
           painted content down to this element's own rectangle either way
           (that clip doesn't require a matching containing block), which is
           why layout looked correct — but verified live via DevTools that
           the ROUNDED part of that clip does not reliably apply when the
           containing block differs from the overflow:hidden ancestor,
           leaving square corners on the background/overlay despite the
           rectangle being right. Same element for both fixes it outright. */
        position: relative;
        min-height: 278px; border-radius: 14px; overflow: hidden;
        /* "Floating" look: a soft, deep, offset-down shadow reads as the
           card being lifted off the page (the farther/softer a shadow, the
           higher something looks like it's floating), plus a hairline
           highlight along the very top edge for a subtle raised-bevel cue —
           the same trick under most "elevated card" design systems. Both
           are plain box-shadow entries so overflow:hidden (which only clips
           this element's own painted content, never its shadow) doesn't
           affect them. */
        box-shadow:
            0 28px 48px -16px rgba(0,0,0,0.65),
            0 10px 20px rgba(0,0,0,0.4),
            inset 0 1px 0 rgba(255,255,255,0.07);
        /* Named view-timeline tracking this card's own scroll position —
           consumed by #tft-hero-compact below to know when to reveal
           itself. axis:block = vertical scroll, which is what we want to
           track regardless of the element's horizontal position. */
        view-timeline-name: --hero-vis;
        view-timeline-axis: block;
    }

    /* The shortened sticky bar shown once the full hero card has scrolled
       out of view. position:fixed (not sticky) deliberately — sticky only
       stays pinned while its own immediate parent is still scrolling past,
       and Streamlit wraps every st.markdown() output in a wrapper that
       hugs its content tightly (no extra height for a sticky element to
       "travel" within), confirmed live via DevTools (opacity/position froze
       after ~100px of scroll instead of staying pinned for the whole page).
       Fixed positioning has no such constraint. Visibility is driven purely
       by the --hero-vis scroll-linked animation below — no JS. */
    #tft-hero-compact {
        position: fixed; top: 60px; left: 0; right: 0; z-index: 999000;
        opacity: 0; pointer-events: none;
        animation-name: tft-hero-compact-reveal;
        animation-duration: 1ms; /* actual "duration" comes from animation-range below, not real time */
        animation-fill-mode: both;
        animation-timing-function: linear;
        animation-timeline: --hero-vis;
        /* Reveal while the original card is exiting the top of the
           viewport, finishing well before it's fully gone so there's no
           gap where neither version is visible. Tuned live against real
           scrolling — a wider range (exit 0%/60%) left a longer stretch
           where both versions were simultaneously mid-fade and visibly
           overlapping; this is snappier. */
        animation-range: exit 0% exit 35%;
    }
    @keyframes tft-hero-compact-reveal {
        0% { opacity: 0; transform: translateY(-8px); }
        99% { pointer-events: none; }
        100% { opacity: 1; transform: translateY(0); pointer-events: auto; }
    }

    .tft-stat-tile {
        background: rgba(212,175,55,0.08); border: 1px solid rgba(212,175,55,0.25);
        border-radius: 10px; padding: 8px 6px; text-align: center; white-space: nowrap;
        width: 108px; box-sizing: border-box;
    }
    .tft-stat-tile .big { font-size: 20px; font-weight: 800; color: var(--tft-gold); white-space: nowrap; }
    .tft-stat-tile .label { font-size: 10px; color: #999; text-transform: uppercase; letter-spacing: 0.5px; white-space: nowrap; }

    .tft-mini-stat {
        background: rgba(212,175,55,0.06); border: 1px solid rgba(212,175,55,0.18);
        border-radius: 8px; padding: 8px 12px;
    }
    .tft-mini-stat .label {
        font-size: 10px; color: #888; text-transform: uppercase; letter-spacing: 0.5px;
        display: flex; align-items: center; gap: 5px;
    }
    .tft-mini-stat .value { font-size: 18px; font-weight: 700; color: #fff; margin-top: 3px; }

    /* Home page hero — same elevated-card language as #tft-hero-card
       (rounded, clipped, deep floating shadow) but without that element's
       view-timeline machinery, which exists purely to drive the dashboard's
       scroll-triggered compact bar. Home is a single hero screen with
       nothing to scroll past, so none of that applies here. */
    .tft-home-hero {
        position: relative; min-height: 620px; border-radius: 18px; overflow: hidden;
        box-shadow:
            0 28px 48px -16px rgba(0,0,0,0.65),
            0 10px 20px rgba(0,0,0,0.4),
            inset 0 1px 0 rgba(255,255,255,0.07);
        display: flex; align-items: center; justify-content: center;
    }
    /* st.container(key="home_search") emits a stable "st-key-home_search"
       class on its wrapper div (a real Streamlit feature, not a fragile
       emotion-cache guess like the sidebar-fill rule above) — used here to
       scope these bigger input styles to Home's hero search only, without
       touching the dashboard's own same-shaped search box. */
    .st-key-home_search input[type="text"] {
        font-size: 20px !important; padding: 14px 16px !important; height: auto !important;
    }
    .st-key-home_search div[data-baseweb="select"] > div {
        font-size: 18px !important; padding-top: 6px !important; padding-bottom: 6px !important;
    }
    .st-key-home_search button {
        font-size: 18px !important; padding: 14px 0 !important; height: 100% !important;
    }
    /* Pulls the search row (a normal Streamlit block, rendered AFTER the
       hero card markdown — Streamlit widgets can't be nested inside raw
       HTML from st.markdown, only placed as siblings) up so it visually
       sits inside the hero image's lower portion instead of below the
       card. The hero has no background of its own at this y-position
       other than the splash image + dark gradient overlay showing through
       from beneath, since this container paints on top in normal sibling
       order — exact pixel value tuned live via DevTools, not guessed. */
    .st-key-home_search_outer { margin-top: -170px; position: relative; z-index: 2; }
    </style>
""", unsafe_allow_html=True)

# --- Hero splash: a random Set 18 champion, refreshed each page load.
# Used for the player card AND the compact scroll bar (same champion in
# both, so the identity feels continuous as you scroll) — the full-page
# backdrop deliberately gets an INDEPENDENT random pick (bg_champion,
# below) so the two don't just show the same picture twice.
#
# The actual mark of a high-quality asset here is the "splash_centered"
# filename Riot ships (a polished, cropped-for-UI splash) — confirmed by
# inspecting the raw data that this exists across EVERY cost tier
# (1-cost through 5-cost all have it on 80-95% of their roster), not just
# 4-5 costs. The old `cost >= 4` gate was standing in for "looks premium"
# but was actually just discarding ~40 perfectly good splash images from
# cheaper champions for no real quality reason — dropping it nearly
# triples the usable pool (23 unique images before -> 55 now).
# Deduping by the resolved icon path (not just by champion entry) matters
# because cosmetic skin variants of the same base champion — e.g. Lux's 8
# emblem forms — are separate entries that all point at one shared image;
# without this a single splash would be ~8x overrepresented at random.
_champion_data = build_champion_data()
_seen_splash_icons = set()
_splash_candidates = []
for _c in _champion_data.values():
    _icon = _c.get("icon")
    if not _c.get("traits") or not _icon or _icon == "None" or "splash_centered" not in _icon:
        continue
    if _icon in _seen_splash_icons:
        continue
    _seen_splash_icons.add(_icon)
    _splash_candidates.append(_c)
if "hero_champion" not in st.session_state:
    st.session_state["hero_champion"] = random.choice(_splash_candidates) if _splash_candidates else None
_hero = st.session_state["hero_champion"]
_hero_splash = tex_path_to_url(_hero["icon"]) if _hero else None

# Independent random pick for the full-page backdrop — excludes whichever
# champion the card/compact bar already picked so the two are never the
# same picture (falls back to allowing a repeat only if there's just one
# candidate to choose from at all).
if "bg_champion" not in st.session_state:
    _bg_candidates = [c for c in _splash_candidates if c is not _hero] or _splash_candidates
    st.session_state["bg_champion"] = random.choice(_bg_candidates) if _bg_candidates else None
_bg_hero = st.session_state["bg_champion"]
_bg_splash = tex_path_to_url(_bg_hero["icon"]) if _bg_hero else None

# Page backdrop: vignette + a pure-CSS hex tile, always on, plus a darkened
# splash image when a hero was chosen. All in one layer stack (not split
# across pseudo-elements) so the hex lines can sit ABOVE the splash-darkening
# veil and stay visible as a texture over it — tried on separate elements
# first, and a veil dark enough to tame a full-brightness splash to a tasteful
# level (verified live: needs ~0.82 alpha) fully hid a hex tile stacked
# beneath it. Line alpha is much lower than it needs to be against a flat
# dark base (0.06/0.045 there) because it gains a lot of extra visual
# contrast sitting above a bright, uneven splash image instead — verified
# live side by side, 0.06/0.045 read as a loud, busy cube pattern once the
# splash was underneath it. Each tuple is (image, size, position); "fixed"
# attachment on all layers matches the old splash-only behavior.
_HEX_LINE = "rgba(212,175,55,0.025)"
_HEX_LINE_DIM = "rgba(212,175,55,0.018)"
# Hex tile layers temporarily removed at the user's request, to see the
# backdrop as just the darkened splash image with no texture overlay. The
# six-gradient honeycomb tile is still built the same way (see the
# _HEX_LINE/_HEX_LINE_DIM constants above and git history for the exact
# layers) — reintroduce by adding them back into _bg_layers below the
# vignette if the plain-image look doesn't win out.
_bg_layers = [
    # Flat wash on top of everything else in this stack — added after the
    # user found the textured backdrop made body text harder to read than
    # the old flat background. Darkens the vignette and splash uniformly
    # (site-wide, not just inside cards) without erasing them; tuned live
    # side by side against the un-washed version before picking 0.35 as
    # visibly more readable while still showing the image through.
    ("linear-gradient(rgba(6,7,10,0.35), rgba(6,7,10,0.35))", "100% 100%", "0 0"),
    ("radial-gradient(ellipse at top, rgba(20,23,31,0.55) 0%, transparent 70%)", "100% 100%", "0 0"),
]
if _bg_splash:
    _bg_layers.append(("linear-gradient(rgba(10,12,16,0.82), rgba(10,12,16,0.82))", "cover", "center 20%"))
    _bg_layers.append(('url("' + _bg_splash + '")', "cover", "center 20%"))
    _before_extra = "filter: saturate(0.6);"
else:
    _before_extra = ""

_before_bg_image = ",\n            ".join(layer[0] for layer in _bg_layers)
_before_bg_size = ", ".join(layer[1] for layer in _bg_layers)
_before_bg_position = ", ".join(layer[2] for layer in _bg_layers)

st.markdown(
    "<style>.stApp::before { content: \"\"; position: fixed; inset: 0; z-index: -1; "
    "background-color: var(--tft-bg-deep); "
    "background-image: " + _before_bg_image + "; "
    "background-size: " + _before_bg_size + "; "
    "background-position: " + _before_bg_position + "; "
    "background-attachment: fixed; "
    + _before_extra + " }</style>",
    unsafe_allow_html=True,
)

# --- Site routing. st.query_params is URL-backed (not just session_state),
# so a clicked <a href="?page=..."> nav link, a direct bookmark/refresh of
# "?page=stats", and a Python-triggered st.query_params["page"]="stats"
# after a successful search all funnel through the exact same mechanism —
# no separate page variable to keep in sync with three different triggers. ---
current_page = st.query_params.get("page", "home")
if current_page not in ("home", "stats", "builder"):
    current_page = "home"

NAV_ITEMS = ["Home", "Player Stats", "Team Builder", "Leaderboards", "About"]
# Only Home/Player Stats/Team Builder have anywhere to go — Leaderboards/About
# stay inert exactly as before, since no such pages exist yet.
NAV_PAGE_LINKS = {"Home": "home", "Player Stats": "stats", "Team Builder": "builder"}
NAV_ACTIVE_ITEM = {"home": "Home", "stats": "Player Stats", "builder": "Team Builder"}[current_page]

def _nav_html(font_size: int = 13, gap: int = 22) -> str:
    items_html = ""
    for item in NAV_ITEMS:
        is_active = item == NAV_ACTIVE_ITEM
        color = "var(--tft-gold)" if is_active else "rgba(255,255,255,0.65)"
        weight = "700" if is_active else "500"
        # Inactive items get a transparent border-bottom (not "none") so
        # the hover state can swap it to gold without the text shifting —
        # same 2px reserved either way, just invisible until hovered.
        underline = "border-bottom:2px solid var(--tft-gold);" if is_active else "border-bottom:2px solid transparent;"
        shared_style = (
            "color:" + color + ";font-weight:" + weight + ";font-size:" + str(font_size) + "px;"
            "letter-spacing:0.5px;white-space:nowrap;padding-bottom:4px;" + underline
        )
        target_page = NAV_PAGE_LINKS.get(item)
        if target_page:
            # target='_self' keeps the navigation in the same tab/frame —
            # a plain relative href already would here, but explicit
            # against any embedding context this app might run inside.
            items_html += (
                "<a href='?page=" + target_page + "' target='_self' class='tft-nav-item' "
                "style='text-decoration:none;cursor:pointer;" + shared_style + "'>" + item + "</a>"
            )
        else:
            items_html += (
                "<span class='tft-nav-item' style='cursor:default;" + shared_style + "'>" + item + "</span>"
            )
    return "<div style='display:flex;gap:" + str(gap) + "px;align-items:center;'>" + items_html + "</div>"

if current_page == "home":
    _home_hero_bg = (
        "background-image:url(" + _hero_splash + ");background-size:cover;background-position:center 20%;"
        if _hero_splash else "background:var(--tft-bg-panel);"
    )
    home_hero_html = (
        "<div class='tft-home-hero' style='" + _home_hero_bg + "'>"
        "<div style='position:absolute;inset:0;background:linear-gradient(180deg, rgba(6,7,10,0.5) 0%, rgba(6,7,10,0.72) 55%, rgba(6,7,10,0.94) 100%);'></div>"
        "<div style='position:absolute;top:0;left:0;right:0;padding:22px 28px;display:flex;justify-content:center;background:linear-gradient(rgba(6,7,10,0.45), transparent);'>"
        + _nav_html() +
        "</div>"
        "<div style='position:relative;text-align:center;padding:0 24px;max-width:760px;'>"
        "<div style='font-size:58px;font-weight:800;color:#fff;letter-spacing:1px;text-shadow:0 4px 18px rgba(0,0,0,0.8);'>TFTPal</div>"
        "<div style='font-size:15px;color:var(--tft-gold);letter-spacing:3px;text-transform:uppercase;margin-top:8px;'>Set 18 &middot; Enchanted Wilds</div>"
        "<div style='font-size:18px;color:#ddd;margin-top:26px;line-height:1.6;'>Search any Riot ID for live match history, rank progress, and trait performance &mdash; yours or anyone else's.</div>"
        "</div>"
        "</div>"
    )
    st.markdown(home_hero_html, unsafe_allow_html=True)

    home_search_outer = st.container(key="home_search_outer")
    with home_search_outer:
        _, home_search_center, _ = st.columns([1, 5, 1])
    with home_search_center:
        with st.container(key="home_search"):
            with st.form("home_search_form", clear_on_submit=False, border=False):
                hs_col_name, hs_col_hash, hs_col_tag, hs_col_region, hs_col_button = st.columns(
                    [2.2, 0.25, 1.2, 2.4, 1.2], vertical_alignment="center"
                )
                with hs_col_name:
                    home_name_input = st.text_input(
                        "Riot ID name", placeholder="Name", label_visibility="collapsed", key="home_name",
                    )
                with hs_col_hash:
                    # Same 8px-low offset measured here as on the dashboard's
                    # smaller search box (app.py's other _handle_player_search
                    # caller) despite this input being visibly bigger —
                    # confirmed live via DevTools this gap is fixed regardless
                    # of input font-size, so the same translateY(-8px)
                    # correction applies unchanged.
                    st.markdown(
                        "<div style='display:flex;align-items:center;justify-content:center;height:50px;"
                        "color:#888;font-size:24px;font-weight:600;transform:translateY(-8px);'>#</div>",
                        unsafe_allow_html=True,
                    )
                with hs_col_tag:
                    home_tag_input = st.text_input(
                        "Riot ID tag", placeholder="TAG", label_visibility="collapsed", key="home_tag",
                    )
                with hs_col_region:
                    home_region_key = st.selectbox(
                        "Region", options=list(REGIONS.keys()),
                        format_func=lambda k: REGIONS[k]["label"],
                        label_visibility="collapsed", key="home_region",
                    )
                with hs_col_button:
                    home_search_submitted = st.form_submit_button("Search", use_container_width=True)
            if home_search_submitted:
                _handle_player_search(home_name_input, home_tag_input, home_region_key)

        if st.button("View my stats →", key="home_view_my_stats", use_container_width=True):
            st.query_params["page"] = "stats"
            st.rerun()

    st.markdown(_riot_disclaimer_html(), unsafe_allow_html=True)
    st.stop()

if current_page == "builder":
    st.markdown(
        "<div style='display:flex;align-items:center;justify-content:center;padding:14px 28px;margin-bottom:12px;"
        "background:var(--tft-bg-panel);border:1px solid var(--tft-border);border-radius:12px;'>"
        + _nav_html(font_size=12, gap=18) +
        "</div>",
        unsafe_allow_html=True,
    )
    components.html(build_team_builder_html(18), height=780, scrolling=False)
    st.stop()

# --- Game mode filter lives in the sidebar — kept out of the hero/player
# card entirely, always visible, never competes for space with it. ---
QUEUE_OPTIONS = {
    "Ranked": 1100,
    "Double Up": 1160,
    "Normal": 1090,
    "All modes": None,
}
QUEUE_ID_LABELS = {v: k for k, v in QUEUE_OPTIONS.items() if v is not None}

# The actual selectbox widget renders lower on the page (next to Placement
# Breakdown), but its value is needed up here for the player card and
# match history. Streamlit persists widget values in session_state across
# reruns, so reading it by key before the widget itself is declared still
# picks up the current selection correctly.
if "game_mode_select" not in st.session_state:
    st.session_state["game_mode_select"] = "Ranked"
selected_label = st.session_state["game_mode_select"]
queue_id = QUEUE_OPTIONS[selected_label]

# --- Hero banner + player card, merged into one component ---
player = _get_viewed_player()
# Keeps the address bar in sync with whoever's actually being viewed —
# including your own default account, so every visit to this page has a
# complete, bookmarkable/shareable URL, not just ones reached via search.
# Query params (not a path like /player/EUW/Name-Tag) because Streamlit
# is a single-endpoint app with no native path routing; this is the
# practical equivalent within that constraint. A reverse proxy in front
# of a real deployment could still rewrite a pretty path down to this
# query string later, if this ever moves off localhost.
st.query_params["region"] = player["region_key"]
st.query_params["name"] = player["game_name"]
st.query_params["tag"] = player["tag_line"]
avg_placement = average_placement(player["puuid"], queue_id=queue_id)
top4 = top4_rate(player["puuid"], queue_id=queue_id)
win_pct = win_rate(player["puuid"], queue_id=queue_id)

history_for_card = match_history(player["puuid"], queue_id=queue_id)

RANK_COLORS = {
    "IRON": "#8c7853", "BRONZE": "#a0673c", "SILVER": "#9fa8b0", "GOLD": "#d4af37",
    "PLATINUM": "#4fd1c5", "EMERALD": "#2ecc71", "DIAMOND": "#5aa9e6",
    "MASTER": "#b362ff", "GRANDMASTER": "#ff5c5c", "CHALLENGER": "#ffe680",
}

# Verified with Pillow (scanned for non-transparent, non-near-white pixels):
# each tier's ranked-emblem PNG is a small crest sitting in a mostly-empty
# canvas, and neither the canvas size nor the crest's position/size within it
# is consistent across tiers (most are 1280x720, Platinum/Emerald are
# 2560x1440; the crest itself ranges from ~195x117 to ~579x429). Displaying
# the raw image at icon size — or even with object-fit:cover — showed only a
# tiny, mostly-empty-looking sliver, since the crest occupies well under a
# third of the canvas either way. Values are
# (canvas_w, canvas_h, crest_x, crest_y, crest_w, crest_h) per tier, used to
# crop tightly to just the crest via a scaled, offset CSS background-image
# instead of the <img> itself.
RANK_EMBLEM_CROP = {
    "IRON": (1280, 720, 544, 298, 195, 117),
    "BRONZE": (1280, 720, 524, 276, 233, 153),
    "SILVER": (1280, 720, 512, 248, 255, 179),
    "GOLD": (1280, 720, 512, 238, 255, 215),
    "PLATINUM": (2560, 1440, 1018, 448, 525, 443),
    "EMERALD": (2560, 1440, 992, 460, 579, 429),
    "DIAMOND": (1280, 720, 486, 264, 311, 185),
    "MASTER": (1280, 720, 492, 238, 297, 215),
    "GRANDMASTER": (1280, 720, 486, 242, 309, 221),
    "CHALLENGER": (1280, 720, 482, 220, 319, 239),
}

def _rank_emblem_html(rank_tier: str, height: int) -> str:
    url = mirrored_url("https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-" + rank_tier.lower() + ".png")
    crop = RANK_EMBLEM_CROP.get(rank_tier.upper())
    if not crop:
        return "<img src='" + url + "' style='width:" + str(height) + "px;height:" + str(height) + "px;vertical-align:-" + str(round(height * 0.27)) + "px;margin-right:6px;' />"
    canvas_w, canvas_h, crest_x, crest_y, crest_w, crest_h = crop
    scale = height / crest_h
    width = round(crest_w * scale)
    return (
        "<span style='display:inline-block;width:" + str(width) + "px;height:" + str(height) + "px;"
        "background-image:url(" + url + ");background-size:" + str(round(canvas_w * scale)) + "px " + str(round(canvas_h * scale)) + "px;"
        "background-position:-" + str(round(crest_x * scale)) + "px -" + str(round(crest_y * scale)) + "px;"
        "vertical-align:-" + str(round(height * 0.27)) + "px;margin-right:6px;'></span>"
    )

avatar_url = None

def _rank_html(icon_height: int) -> str:
    if not rank_tier:
        return "<span style='color:#888;'>Not tracked for this game</span>"
    rank_color = RANK_COLORS.get(rank_tier.upper(), "#d4af37")
    return (
        _rank_emblem_html(rank_tier, icon_height)
        + "<span style='color:" + rank_color + ";font-weight:700;'>" + rank_tier.title() + " " + (rank_division or "") + "</span>"
        + "<span style='color:#aaa;margin-left:6px;'>" + str(league_points or 0) + " LP</span>"
    )

rank_tier = rank_division = league_points = None
if history_for_card:
    latest_game = get_game(history_for_card[0][0], player["puuid"])
    (_, _, _, _, _, _, _, _, rank_tier, rank_division, league_points, _,
     companion_content_id, _) = latest_game

    companion_info = build_companion_lookup().get(companion_content_id)
    if companion_info:
        avatar_url = companion_icon_url(companion_info["loadoutsIcon"])

def _avatar_html(size: int) -> str:
    if avatar_url:
        return (
            "<img src='" + avatar_url + "' style='width:" + str(size) + "px;height:" + str(size) + "px;"
            "border-radius:50%;border:3px solid var(--tft-gold);object-fit:cover;object-position:center 30%;"
            "box-shadow:0 2px 12px rgba(0,0,0,0.6);' />"
        )
    return (
        "<div style='width:" + str(size) + "px;height:" + str(size) + "px;border-radius:50%;"
        "border:3px solid var(--tft-gold);background:var(--tft-bg-raised);'></div>"
    )

avatar_html = _avatar_html(92)

def _stat_tile(value: str, label: str, value_color: str = None) -> str:
    color_style = f"color:{value_color};" if value_color else ""
    return "<div class='tft-stat-tile'><div class='big' style='" + color_style + "'>" + value + "</div><div class='label'>" + label + "</div></div>"

# LP change over the last 10 games in whichever ladder matches the
# current mode filter (Ranked/Double Up have separate ladders) — same
# numeric rating scale the progression chart uses, so a plain difference
# already correctly accounts for crossing tier boundaries.
_rating_queue_id, _rating_queue_label = _rating_queue_for_filter(queue_id)
recent_rating = _cached_rating_history(player["game_name"], player["tag_line"], platform=player["platform"], queue_id=_rating_queue_id)[-11:] if _rating_queue_id else []
if len(recent_rating) >= 2:
    lp_change = recent_rating[-1]["rating_numeric"] - recent_rating[0]["rating_numeric"]
    if lp_change > 0:
        lp_change_value, lp_change_color = f"▲ {lp_change}", "#3ba55c"
    elif lp_change < 0:
        lp_change_value, lp_change_color = f"▼ {abs(lp_change)}", "#c0392b"
    else:
        lp_change_value, lp_change_color = "— 0", "#999"
else:
    lp_change_value, lp_change_color = "—", None

stats_html = (
    _stat_tile(str(len(history_for_card)), "Games Played")
    + _stat_tile(f"{avg_placement:.2f}" if avg_placement is not None else "—", "Avg Place")
    + _stat_tile(f"{top4:.1f}%" if avg_placement is not None else "—", "Top 4")
    + _stat_tile(f"{win_pct:.1f}%" if avg_placement is not None else "—", "Win Rate")
    + _stat_tile(lp_change_value, "Last 10 Games", lp_change_color)
)

if _hero_splash:
    hero_bg_style = "background-image:url(" + _hero_splash + ");background-size:cover;background-position:center 20%;"
else:
    hero_bg_style = "background:var(--tft-bg-panel);"

hero_card_html = (
    "<div id='tft-hero-card' style='" + hero_bg_style +
    "border:1px solid var(--tft-border);margin-bottom:24px;'>"
    "<div style='position:absolute;inset:0;border-radius:inherit;background:linear-gradient(100deg, rgba(10,12,16,0.94) 32%, rgba(10,12,16,0.6) 68%, rgba(10,12,16,0.3) 100%);'></div>"

    "<div style='position:absolute;top:0;left:0;right:0;padding:12px 28px;display:flex;justify-content:center;background:linear-gradient(rgba(6,7,10,0.55), transparent);'>"
    + _nav_html() +
    "</div>"

    "<div style='position:absolute;top:54px;left:28px;'>"
    "<div style='font-size:30px;font-weight:800;color:#fff;letter-spacing:1px;text-shadow:0 2px 8px rgba(0,0,0,0.8);'>TFTPal</div>"
    "<div style='font-size:13px;color:var(--tft-gold);letter-spacing:2px;text-transform:uppercase;'>Set 18 &middot; Enchanted Wilds</div>"
    "</div>"

    "<div style='position:absolute;top:54px;right:28px;display:flex;align-items:center;gap:14px;'>"
    "<div style='text-align:right;'>"
    "<div style='font-size:24px;'><b style='color:#fff;'>" + player["game_name"] + "</b><span style='color:#aaa;'>#" + player["tag_line"] + "</span></div>"
    "<div style='margin-top:6px;font-size:16px;display:flex;align-items:center;'>" + _rank_html(38) + "</div>"
    "</div>"
    + avatar_html +
    "</div>"

    "<div style='position:absolute;bottom:22px;left:50%;transform:translateX(-50%);display:flex;gap:12px;'>"
    + stats_html +
    "</div>"

    "</div>"
)
st.markdown(hero_card_html, unsafe_allow_html=True)

# Shortened sticky version of the card above (title + name/rank/avatar,
# no stat tiles) — hidden until the full card scrolls out of view, then
# pinned to the top of the page. See the #tft-hero-compact / --hero-vis
# CSS (in the main stylesheet block) for how the reveal itself works;
# this just builds the smaller reused content.
hero_compact_html = (
    "<div id='tft-hero-compact' style='" + hero_bg_style +
    "border-bottom:1px solid var(--tft-border);'>"
    "<div style='position:absolute;inset:0;background:linear-gradient(100deg, rgba(10,12,16,0.96) 40%, rgba(10,12,16,0.75) 68%, rgba(10,12,16,0.55) 100%);'></div>"

    # Grid (not flex space-between) so the nav stays perfectly centered
    # regardless of how wide the title vs. identity blocks on either side
    # are — a middle flex child can't do that without extra wrapper markup.
    "<div style='position:relative;display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:12px 28px;'>"
    "<div>"
    "<div style='font-size:19px;font-weight:800;color:#fff;letter-spacing:0.5px;text-shadow:0 2px 8px rgba(0,0,0,0.8);'>TFTPal</div>"
    "<div style='font-size:11px;color:var(--tft-gold);letter-spacing:1.5px;text-transform:uppercase;'>Set 18 &middot; Enchanted Wilds</div>"
    "</div>"

    + _nav_html(font_size=12, gap=18) +

    "<div style='display:flex;align-items:center;gap:12px;justify-self:end;'>"
    "<div style='text-align:right;'>"
    "<div style='font-size:17px;'><b style='color:#fff;'>" + player["game_name"] + "</b><span style='color:#aaa;'>#" + player["tag_line"] + "</span></div>"
    "<div style='margin-top:2px;font-size:13px;display:flex;align-items:center;justify-content:flex-end;'>" + _rank_html(26) + "</div>"
    "</div>"
    + _avatar_html(44) +
    "</div>"

    "</div>"
)
st.markdown(hero_compact_html, unsafe_allow_html=True)

# --- Player search (any Riot ID, any of the regions in REGIONS) + a real,
# working manual refresh for whichever player is currently being viewed. ---
col_search, col_refresh = st.columns([5, 1.3], vertical_alignment="top")
with col_search:
    with st.form("player_search_form", clear_on_submit=False, border=False):
        search_col_name, search_col_hash, search_col_tag, search_col_region, search_col_button = st.columns(
            [2.6, 0.25, 1.5, 1.4, 1], vertical_alignment="center"
        )
        with search_col_name:
            search_name_input = st.text_input(
                "Riot ID name", placeholder="Name", label_visibility="collapsed",
            )
        with search_col_hash:
            # Greyed-out "#" sitting between the two fields — signals the
            # hashtag is implied/prefilled rather than something to type,
            # without needing the user to know Riot ID syntax up front.
            st.markdown(
                # flex-centering alone still measured 8px low (confirmed
                # live via DevTools getBoundingClientRect: the text_input
                # boxes center at y=466, this plain markdown div's block
                # centered at y=474) — text_input's actual <input> sits
                # lower within its own widget wrapper than a bare div does
                # within its column, likely from the collapsed label still
                # reserving a sliver of space above it. translateY corrects
                # for that measured gap directly rather than guessing again.
                "<div style='display:flex;align-items:center;justify-content:center;height:42px;color:#666;font-size:20px;font-weight:600;transform:translateY(-8px);'>#</div>",
                unsafe_allow_html=True,
            )
        with search_col_tag:
            search_tag_input = st.text_input(
                "Riot ID tag", placeholder="TAG", label_visibility="collapsed",
            )
        with search_col_region:
            search_region_key = st.selectbox(
                "Region", options=list(REGIONS.keys()),
                format_func=lambda k: REGIONS[k]["label"],
                label_visibility="collapsed",
            )
        with search_col_button:
            search_submitted = st.form_submit_button("Search", use_container_width=True)
    if search_submitted:
        _handle_player_search(search_name_input, search_tag_input, search_region_key)

with col_refresh:
    if st.button("Refresh Stats", use_container_width=True, key="refresh_stats_top"):
        with st.status("Checking for new matches...", expanded=True) as status:
            def _report_progress(saved_count):
                status.update(label=f"Checking for new matches — {saved_count} saved so far...")
            result = refresh_player_matches(player, on_progress=_report_progress)
            status.update(label="Done checking for new matches.", state="complete")
        if "error" in result:
            _dismissible_alert("Couldn't refresh: " + result["error"], "error")
        elif result["new_matches"] > 0:
            _dismissible_alert(f"Fetched {result['new_matches']} new match(es).", "success")
            st.rerun()
        else:
            _dismissible_alert("Up to date", "info")

if (player["game_name"] != DEFAULT_PLAYER["game_name"] or player["tag_line"] != DEFAULT_PLAYER["tag_line"]
        or player["region_key"] != DEFAULT_PLAYER["region_key"]):
    back_col_label, back_col_button = st.columns([5, 1.3], vertical_alignment="center")
    with back_col_label:
        st.caption(f"Viewing {player['game_name']}#{player['tag_line']} · {player['region_key']}")
    with back_col_button:
        if st.button("← Back to my stats", use_container_width=True, key="back_to_my_stats"):
            st.session_state["viewed_player"] = dict(DEFAULT_PLAYER)
            _reroll_hero_backgrounds()
            st.rerun()

# --- Placement breakdown ---
col_heading, col_mode = st.columns([3, 1], vertical_alignment="bottom")
with col_heading:
    st.subheader("Placement Breakdown")
with col_mode:
    st.markdown("<div style='color:var(--tft-gold);font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;text-align:right;'>Game Mode</div>", unsafe_allow_html=True)
    st.selectbox("Game mode", options=list(QUEUE_OPTIONS.keys()), key="game_mode_select", label_visibility="collapsed")
st.markdown("<hr class='tft-divider' />", unsafe_allow_html=True)

# --- Last 30 games, as a grid of placement squares (most recent first) ---
GRID_PLACEMENT_COLORS = {
    1: ("#d4af37", "#1a1a1a"),
    2: ("#b9bfc7", "#1a1a1a"),
    3: ("#a0673c", "#fff"),
}
GRID_DEFAULT_COLOR = ("#2e323d", "#ccc")

LAST_GRID_GAMES = 20
last_n_games = match_history(player["puuid"], queue_id=queue_id)[:LAST_GRID_GAMES]
# 20 equal columns so the squares span the full panel width at any window size
grid_html = "<div style='display:grid;grid-template-columns:repeat(" + str(LAST_GRID_GAMES) + ",minmax(0,1fr));gap:8px;margin-bottom:16px;'>"
for match_id, placement, level, gold_left, last_round, game_datetime, _queue_id in last_n_games:
    bg, fg = GRID_PLACEMENT_COLORS.get(placement, GRID_DEFAULT_COLOR)
    grid_html += (
        "<div style='height:64px;border-radius:8px;background:" + bg + ";color:" + fg + ";"
        "display:flex;align-items:center;justify-content:center;font-weight:800;font-size:22px;'>"
        + str(placement) + "</div>"
    )
grid_html += "</div>"

if last_n_games:
    with st.container(border=True):
        st.markdown("<div style='color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;'>Last " + str(len(last_n_games)) + " Games</div>", unsafe_allow_html=True)
        st.markdown(grid_html, unsafe_allow_html=True)

def _chart_placeholder_html(text: str) -> str:
    # 336px, not the chart's declared height=320 — measured live via
    # DevTools that Altair/Vega-Lite renders taller than the declared plot
    # height once axis tick labels and the axis title are added underneath
    # it, so matching 320 exactly left this box 16px shorter than its
    # sibling chart box.
    return (
        "<div style='height:336px;display:flex;align-items:center;justify-content:center;"
        "color:#888;font-size:14px;text-align:center;'>" + text + "</div>"
    )

# --- Placement bar chart + rank progression line chart, side by side ---
col_bar, col_line = st.columns(2)

breakdown = placement_breakdown(player["puuid"], queue_id=queue_id)
chart_df = pd.DataFrame({
    "Placement": [str(row[0]) for row in breakdown],
    "Games": [row[1] for row in breakdown],
    "Color": [placement_color(row[0]) for row in breakdown],
})
bar_chart = alt.Chart(chart_df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
    x=alt.X("Placement:N", axis=alt.Axis(labelAngle=0, labelColor="#ccc", titleColor="#ccc", domainColor="#444", tickColor="#444")),
    y=alt.Y("Games:Q", axis=alt.Axis(labelColor="#ccc", titleColor="#ccc", domainColor="#444", tickColor="#444", gridColor="#22252e")),
    color=alt.Color("Color:N", scale=None, legend=None)
).properties(height=320, background="transparent").configure_view(strokeWidth=0)

with col_bar:
    with st.container(border=True):
        st.markdown("<div style='color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;'>Placements (" + selected_label + ")</div>", unsafe_allow_html=True)
        st.altair_chart(bar_chart, use_container_width=True)

with col_line:
    with st.container(border=True):
        if _rating_queue_id:
            st.markdown("<div style='color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;'>" + _rating_queue_label + " LP Progression &middot; This Set</div>", unsafe_allow_html=True)
        else:
            st.markdown("<div style='color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;'>LP Progression</div>", unsafe_allow_html=True)
        rating_history = _cached_rating_history(player["game_name"], player["tag_line"], platform=player["platform"], queue_id=_rating_queue_id) if _rating_queue_id else []
        if not _rating_queue_id:
            st.markdown(_chart_placeholder_html("Normal games have no ranked ladder."), unsafe_allow_html=True)
        elif rating_history:
            rating_values = [r["rating_numeric"] for r in rating_history]
            rating_df = pd.DataFrame({
                "Game": list(range(1, len(rating_history) + 1)),
                "Rating": rating_values,
                "Label": [r["rating_text"] for r in rating_history],
            })

            # Custom tick every 100 points (one full division) labeled with the
            # shortened rank (e.g. "E4" for Emerald IV) instead of a raw number,
            # since the underlying scale (tier*400 + division*100 + LP) isn't
            # meaningful to read directly.
            tick_start = (min(rating_values) // 100) * 100
            tick_end = ((max(rating_values) // 100) + 1) * 100
            tick_values = list(range(tick_start, tick_end + 1, 100))
            label_expr = " : ".join(
                f"datum.value === {t} ? '{short_rank_label(t)}'" for t in tick_values
            ) + " : ''"

            line_chart = alt.Chart(rating_df).mark_line(color="#d4af37", point=alt.OverlayMarkDef(color="#d4af37", size=25)).encode(
                x=alt.X("Game:Q", axis=alt.Axis(labelColor="#ccc", titleColor="#ccc", domainColor="#444", tickColor="#444")),
                y=alt.Y("Rating:Q", title="Rank",
                    scale=alt.Scale(domain=[tick_start, tick_end], zero=False, nice=False),
                    axis=alt.Axis(
                        values=tick_values, labelExpr=label_expr, labelOverlap=False, labelFontSize=10,
                        labelColor="#ccc", titleColor="#ccc", domainColor="#444", tickColor="#444", gridColor="#22252e",
                    )),
                tooltip=["Label:N"],
            ).properties(height=320, background="transparent").configure_view(strokeWidth=0)
            st.altair_chart(line_chart, use_container_width=True)
        else:
            st.markdown(_chart_placeholder_html("Rank history unavailable right now."), unsafe_allow_html=True)

# --- Most played units + traits, side by side ---
col_mp_heading, col_mp_sort = st.columns([3, 1], vertical_alignment="bottom")
with col_mp_heading:
    # Reads session_state from BEFORE the selectbox below (re-)declares it
    # this run — same pattern as game_mode_select elsewhere in this file:
    # Streamlit restores session_state at the top of a rerun, so this
    # already reflects whatever was picked last, not last run's stale value.
    st.subheader("Best Performing" if st.session_state.get("most_played_sort") == "Best Performing" else "Most Played")
with col_mp_sort:
    st.markdown("<div style='color:var(--tft-gold);font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;text-align:right;'>Rank By</div>", unsafe_allow_html=True)
    st.selectbox("Rank by", options=["Most Played", "Best Performing"], key="most_played_sort", label_visibility="collapsed")
st.markdown("<hr class='tft-divider' />", unsafe_allow_html=True)

_sort_by = "performance" if st.session_state["most_played_sort"] == "Best Performing" else "frequency"
units = most_played_units(player["puuid"], queue_id=queue_id, sort_by=_sort_by)
_metatft_champions_ref = get_metatft_champion_lookup(_champion_data)

col_units, col_traits = st.columns(2)

rows_html = ""
for i, (character_id, name, times_played, avg_place) in enumerate(units):
    champ = _champion_data.get(character_id)
    icon_url = tex_path_to_url(champ["squareIcon"]) if champ and champ.get("squareIcon") else None
    icon_html = (
        "<img src='" + icon_url + "' style='width:32px;height:32px;border-radius:5px;vertical-align:middle;margin-right:10px;' />"
        if icon_url else
        "<div style='width:32px;height:32px;border-radius:5px;background:var(--tft-bg-raised);display:inline-block;vertical-align:middle;margin-right:10px;'></div>"
    )

    # This is an aggregate reference row (no specific game/star-level
    # behind it), so the tooltip just shows the ability at its base (star
    # 1) values — a quick "what does this unit even do" reminder rather
    # than a per-game recap.
    ability_name, ability_desc = "", ""
    if champ:
        ability = champ.get("ability") or {}
        ability_name = ability.get("name", "")
        metatft_champ = _metatft_champions_ref.get(character_id)
        metatft_ability = (metatft_champ or {}).get("ability")
        if metatft_ability and metatft_ability.get("desc"):
            ability_desc = resolve_metatft_markup(
                metatft_ability["desc"], metatft_ability.get("attributeValues", {}),
                (metatft_champ or {}).get("curveValues", {}), star_level=1,
            )
        elif ability.get("desc"):
            ability_desc = clean_game_text(ability["desc"])

    tooltip_content = (
        "<div class='tft-tooltip-content' style='border:2px solid var(--tft-gold-dim);'><div style='padding:8px;'>"
        "<div style='font-weight:bold;color:#fff;margin-bottom:4px;'>" + name + "</div>"
        + ("<div style='color:var(--tft-gold);font-size:12px;font-weight:600;margin-bottom:4px;'>" + ability_name + "</div>" if ability_name else "")
        + ("<div style='color:#bbb;'>" + ability_desc + "</div>" if ability_desc else "<div style='color:#666;font-style:italic;'>No ability data available.</div>")
        + "</div></div>"
    )
    icon_html = "<div class='tft-tooltip-wrap' style='margin-right:6px;'>" + icon_html.replace("margin-right:10px;", "") + tooltip_content + "</div>"

    # Column widths below are deliberately tight — these rows now live in a
    # half-width column (Units/Traits side by side), not the full page
    # width the original single-column design had. Measured live via
    # DevTools (canvas text measurement + scrollWidth checks) rather than
    # guessed: the old 100px "games"/"avg" columns alone left so little
    # room that names truncated to a single letter, and even after
    # shrinking those, a few longer trait names (e.g. "Emerald Aspect (1)")
    # still clipped their breakpoint number until padding/margins were
    # trimmed too and the name got an explicit smaller font-size.
    row_bg = "rgba(255,255,255,0.02)" if i % 2 else "transparent"
    rows_html += (
        "<div style='display:flex;align-items:center;padding:8px 8px;background:" + row_bg + ";border-radius:6px;'>"
        "<span style='color:#666;font-size:12px;width:16px;flex:0 0 auto;'>" + str(i + 1) + "</span>"
        + icon_html +
        "<span style='flex:1;min-width:0;font-size:14px;color:#eee;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>" + name + "</span>"
        "<span style='color:#999;font-size:13px;width:58px;flex:0 0 auto;text-align:right;'>" + str(times_played) + " games</span>"
        "<span style='color:" + placement_color(round(avg_place)) + ";font-size:13px;width:56px;flex:0 0 auto;text-align:right;font-weight:600;'>" + f"{avg_place:.2f}" + " avg</span>"
        "</div>"
    )

with col_units:
    with st.container(border=True):
        st.markdown("<div style='color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin:2px 0 4px;'>Units</div>", unsafe_allow_html=True)
        st.markdown("<div style='padding:10px 0;'>" + rows_html + "</div>", unsafe_allow_html=True)

# Trait rows mirror the unit rows above (same layout, same per-row hover
# tooltip pattern) so the two lists read as one consistent "Most Played"
# component split into two columns — but each row here is a specific
# (trait, breakpoint) pair, e.g. "Brawler (4)" ranked separately from
# "Brawler (2)", rather than one lumped-together "Brawler" entry, so a
# trait that plays very differently at different breakpoints doesn't hide
# that. Icon and name are tinted with that breakpoint's tier color (bronze/
# silver/gold/prismatic) — the same colored-badge technique already used
# for trait icons in the match history cards, reused here for consistency.
traits_played = most_played_traits(player["puuid"], queue_id=queue_id, sort_by=_sort_by)
trait_data_ref = build_trait_data()
trait_static_ref = build_trait_static_data()

trait_rows_html = ""
for i, (trait_api_name, name, min_units, style, times_active, avg_place) in enumerate(traits_played):
    trait_info = trait_data_ref.get(trait_api_name)
    icon_url = tex_path_to_url(trait_info["icon"]) if trait_info and trait_info.get("icon") else None
    color = trait_style_color(style)
    label = name + (" (" + str(min_units) + ")" if min_units else "")

    if icon_url:
        icon_html = (
            "<div style='position:relative;width:32px;height:32px;border-radius:50%;border:2px solid " + color + ";"
            "overflow:hidden;display:flex;align-items:center;justify-content:center;flex:0 0 auto;margin-right:10px;'>"
            "<img src='" + icon_url + "' style='width:20px;height:20px;filter:grayscale(100%);display:block;' />"
            "<div style='position:absolute;inset:0;background:" + color + ";mix-blend-mode:color;'></div>"
            "</div>"
        )
    else:
        icon_html = "<div style='width:32px;height:32px;border-radius:50%;border:3px solid " + color + ";flex:0 0 auto;margin-right:10px;'></div>"

    # Unlike the aggregate unit-ability tooltip, this row IS tied to one
    # specific breakpoint, so passing that breakpoint's real min_units lets
    # resolve_trait_breakpoints correctly mark it (and any lower ones) as
    # reached instead of showing everything dimmed.
    tt = resolve_trait_breakpoints(trait_static_ref.get(trait_api_name), min_units or 0)
    tooltip_content = (
        "<div class='tft-tooltip-content' style='border:2px solid " + color + ";'><div style='padding:8px;'>"
        "<div style='font-weight:bold;color:#fff;margin-bottom:4px;'>" + label + "</div>"
        + ("<div style='color:#bbb;margin-bottom:6px;'>" + tt["intro"] + "</div>" if tt.get("intro") else "")
        + "".join("<div style='color:" + ("#fff" if bp["reached"] else "#666") + ";margin-bottom:2px;'>" + bp["text"] + "</div>" for bp in tt.get("breakpoints", []))
        + ("" if tt.get("breakpoints") or tt.get("intro") else "<div style='color:#666;font-style:italic;'>No trait data available.</div>")
        + "</div></div>"
    )
    icon_html = "<div class='tft-tooltip-wrap' style='margin-right:6px;'>" + icon_html.replace("margin-right:10px;", "") + tooltip_content + "</div>"

    # Same tight-column reasoning as the units row above — measured live,
    # not guessed (see the comment there for how these numbers were found).
    row_bg = "rgba(255,255,255,0.02)" if i % 2 else "transparent"
    trait_rows_html += (
        "<div style='display:flex;align-items:center;padding:8px 8px;background:" + row_bg + ";border-radius:6px;'>"
        "<span style='color:#666;font-size:12px;width:16px;flex:0 0 auto;'>" + str(i + 1) + "</span>"
        + icon_html +
        "<span style='flex:1;min-width:0;font-size:14px;color:" + color + ";font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>" + label + "</span>"
        "<span style='color:#999;font-size:13px;width:58px;flex:0 0 auto;text-align:right;'>" + str(times_active) + " games</span>"
        "<span style='color:" + placement_color(round(avg_place)) + ";font-size:13px;width:56px;flex:0 0 auto;text-align:right;font-weight:600;'>" + f"{avg_place:.2f}" + " avg</span>"
        "</div>"
    )

with col_traits:
    with st.container(border=True):
        st.markdown("<div style='color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin:2px 0 4px;'>Traits</div>", unsafe_allow_html=True)
        st.markdown("<div style='padding:10px 0;'>" + trait_rows_html + "</div>", unsafe_allow_html=True)

# --- Patch changes ---
from patch_tracker import latest_diff

st.subheader("Patch Changes (Traits)")
st.markdown("<hr class='tft-divider' />", unsafe_allow_html=True)
changes, old_name, new_name = latest_diff()

if changes is None:
    st.caption("Need at least two snapshots to compare. Run refresh_data.py after each patch.")
elif not changes:
    st.caption(f"No trait changes between {old_name} and {new_name}.")
else:
    st.caption(f"Comparing {old_name} → {new_name}")
    for trait_id, items in changes.items():
        with st.expander(f"{trait_id} ({len(items)} change(s))"):
            for c in items:
                st.write(f"**{c['field']}**: `{c['before']}` → `{c['after']}`")

# --- Match history (clickable) ---
# The alert gets its own column between the heading and the button rather
# than being stacked under the button inside col_mh_refresh — with
# vertical_alignment="bottom", extra content stacked under the button made
# that column taller than col_mh_heading, and "bottom" aligns every column
# to the tallest one's bottom edge, which visibly pushed the button (now
# sitting above the alert instead of at the column's bottom) upward for as
# long as the alert stayed in the DOM. A side-by-side column never changes
# height, so the button no longer moves at all.
col_mh_heading, col_mh_alert, col_mh_refresh = st.columns([3.3, 2.7, 1.3], vertical_alignment="center")
with col_mh_heading:
    st.subheader("Match History")
with col_mh_refresh:
    if st.button("Refresh Stats", use_container_width=True, key="refresh_stats_bottom"):
        with st.status("Checking for new matches...", expanded=True) as status:
            def _report_progress(saved_count):
                status.update(label=f"Checking for new matches — {saved_count} saved so far...")
            result = refresh_player_matches(player, on_progress=_report_progress)
            status.update(label="Done checking for new matches.", state="complete")
        if "error" in result:
            with col_mh_alert:
                _dismissible_alert("Couldn't refresh: " + result["error"], "error", align="right")
        elif result["new_matches"] > 0:
            with col_mh_alert:
                _dismissible_alert(f"Fetched {result['new_matches']} new match(es).", "success", align="right")
            st.rerun()
        else:
            with col_mh_alert:
                _dismissible_alert("Up to date", "info", align="right")
st.markdown("<hr class='tft-divider' />", unsafe_allow_html=True)
history = match_history(player["puuid"], queue_id=queue_id)

# Rendering every match's full HTML (preview + expanded content, tooltips
# included) on every single rerun — which Streamlit does on any widget
# interaction, including just switching game mode — was the actual source
# of the lag: with ~30 matches each carrying several KB of tooltip markup,
# that's a large payload to re-send and re-parse every time, regardless of
# which card (if any) is actually open. Paginating cuts that down sharply.
MATCHES_PER_PAGE = 20
_history_reset_key = (player["puuid"], queue_id)
if st.session_state.get("history_queue_id") != _history_reset_key:
    st.session_state["history_queue_id"] = _history_reset_key
    st.session_state["matches_shown"] = MATCHES_PER_PAGE
matches_shown = st.session_state["matches_shown"]
visible_history = history[:matches_shown]

STAR_COLORS = {1: "#cd7f32", 2: "#e8e8e8", 3: "#ffd700", 4: "#50c878"}
COST_COLORS = {1: "#a0a0a0", 2: "#1e9e4a", 3: "#3a7bd5", 4: "#a04dcf", 5: "#e0b040"}

all_cards_html = ""

for match_id, placement, level, gold_left, last_round, game_datetime, match_queue_id in visible_history:
    detail = get_match_detail(match_id, player["puuid"], player["game_name"], player["tag_line"])
    color = detail["placement_color"]
    mode_label = QUEUE_ID_LABELS.get(match_queue_id, "TFT")

    # Placement, avatar, and date/time already appear in the collapsed
    # preview row above (in the <summary>) — repeating them here in the
    # expanded panel just duplicated the same facts in a different style,
    # which was the "not cohesive" seam between preview and expanded view.
    # This panel now only adds what the preview doesn't already show.
    def _mini_stat(icon_path, label, value):
        return (
            "<div class='tft-mini-stat'>"
            "<div class='label'>" + svg_icon(icon_path, size=13) + label + "</div>"
            "<div class='value'>" + value + "</div>"
            "</div>"
        )

    left_html = "<div style='padding: 16px;'>"
    left_html += "<div style='font-size:14px;color:#999;margin-bottom:14px;'>" + detail['player_name'] + "<span style='color:#666;'>#" + detail['tag_line'] + "</span></div>"
    left_html += "<div style='display:flex;flex-direction:column;gap:8px;'>"
    left_html += _mini_stat(ICON_SHIELD, "Rank", detail['rank_display'])
    left_html += _mini_stat(ICON_FLAG, "Stage", detail['stage'])
    left_html += _mini_stat(ICON_SWORD, "Damage", str(detail['damage']))
    left_html += _mini_stat(ICON_COIN, "Board Value", str(detail['board_value']))
    left_html += "</div></div>"

    traits_html = "<div style='display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px;'>"
    preview_traits_html = ""
    for trait in detail["traits"]:
        if trait["icon_url"]:
            trait_img = "<div style='position:relative;width:32px;height:32px;border:2px solid " + trait['color'] + ";border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;'><img src='" + trait['icon_url'] + "' style='width:20px;height:20px;filter:grayscale(100%);display:block;' /><div style='position:absolute;inset:0;background:" + trait['color'] + ";mix-blend-mode:color;'></div></div>"
            trait_img_small = "<div style='position:relative;width:30px;height:30px;border:2px solid " + trait['color'] + ";border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;flex:0 0 auto;'><img src='" + trait['icon_url'] + "' style='width:18px;height:18px;filter:grayscale(100%);display:block;' /><div style='position:absolute;inset:0;background:" + trait['color'] + ";mix-blend-mode:color;'></div></div>"
        else:
            trait_img = "<div style='width:32px;height:32px;border:4px solid " + trait['color'] + ";border-radius:50%;'></div>"
            trait_img_small = "<div style='width:30px;height:30px;border:3px solid " + trait['color'] + ";border-radius:50%;flex:0 0 auto;'></div>"

        tt = trait.get("tooltip", {})
        tooltip_body = "<div style='padding:8px;'>"
        tooltip_body += "<div style='font-weight:bold;color:#fff;margin-bottom:4px;'>" + trait["name"] + "</div>"
        if tt.get("intro"):
            tooltip_body += "<div style='color:#bbb;margin-bottom:6px;'>" + tt["intro"] + "</div>"
        for bp in tt.get("breakpoints", []):
            bp_color = "#fff" if bp["reached"] else "#666"
            tooltip_body += "<div style='color:" + bp_color + ";margin-bottom:2px;'>" + bp["text"] + "</div>"

        roster = trait.get("roster") or []
        if roster:
            roster_row = "<div style='display:flex;flex-wrap:wrap;gap:4px;margin-top:8px;padding-top:8px;border-top:1px solid var(--tft-border);'>"
            for unit in roster:
                if not unit["icon_url"]:
                    continue
                unit_style = "width:32px;height:32px;border-radius:5px;border:1px solid #222;"
                unit_style += "" if unit["used_this_match"] else "filter:grayscale(100%);opacity:0.4;"
                roster_row += "<img src='" + unit["icon_url"] + "' title='" + unit["name"] + "' style='" + unit_style + "' />"
            roster_row += "</div>"
            tooltip_body += roster_row

        tooltip_body += "</div>"

        tooltip_content = "<div class='tft-tooltip-content' style='border:2px solid " + trait['color'] + ";'>" + tooltip_body + "</div>"
        traits_html += "<div class='tft-tooltip-wrap'>" + trait_img + tooltip_content + "</div>"
        # Preview only shows traits that are actually active — skips the
        # "#555555" inactive-grey ones the full expanded view still lists.
        if trait['color'] != "#555555":
            preview_traits_html += "<div class='tft-tooltip-wrap'>" + trait_img_small + tooltip_content + "</div>"
    traits_html += "</div>"

    units_html = "<div style='display:flex; flex-wrap:wrap; gap:12px;'>"
    preview_units_html = ""
    for unit in detail["units"]:
        star_color = STAR_COLORS.get(unit["star_level"], "#ffffff")
        cost_color = COST_COLORS.get(unit["cost"], "#a0a0a0")

        if unit["portrait_url"]:
            portrait_img = "<img src='" + unit['portrait_url'] + "' style='width:64px;height:64px;border:2px solid " + cost_color + ";border-radius:6px;' />"
            portrait_img_small = "<img src='" + unit['portrait_url'] + "' style='width:52px;height:52px;object-fit:cover;border:2px solid " + cost_color + ";border-radius:6px;' />"
        else:
            portrait_img = "<div style='width:64px;height:64px;background:var(--tft-bg-raised);border-radius:6px;'></div>"
            portrait_img_small = "<div style='width:52px;height:52px;background:var(--tft-bg-raised);border:2px solid " + cost_color + ";border-radius:6px;'></div>"

        if unit["splash_url"]:
            header = "<div style='position:relative;height:130px;background-image:url(" + unit["splash_url"] + ");background-size:cover;background-position:center;display:flex;flex-direction:column;justify-content:flex-end;'>"
            header += "<div style='position:absolute;inset:0;background:linear-gradient(to bottom, rgba(0,0,0,0.15) 40%, rgba(14,16,21,0.92));'></div>"
        else:
            header = "<div style='position:relative;height:130px;background:var(--tft-bg-raised);display:flex;flex-direction:column;justify-content:flex-end;'>"

        header += "<div style='position:relative;padding:8px;'>"
        header += "<div style='font-size:20px;font-weight:bold;color:" + cost_color + ";margin-bottom:4px;text-shadow: -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000, 0 0 8px rgba(0,0,0,0.8);'>" + unit["name"] + "</div>"

        traits_line = ""
        for t in unit["unit_traits"]:
            if t["icon_url"]:
                traits_line += "<span style='display:inline-flex;align-items:center;gap:4px;'><img src='" + t["icon_url"] + "' style='width:14px;height:14px;filter:brightness(0) invert(1);' />" + t["name"] + "</span>"
            else:
                traits_line += "<span>" + t["name"] + "</span>"
        header += "<div style='font-size:11px;color:#eee;display:flex;flex-wrap:wrap;gap:4px 8px;'>" + traits_line + "</div>"
        header += "</div></div>"

        if unit["max_mana"]:
            mana_icon = "<img src='https://ap.tft.tools/img/general/mana.png?w=14' style='width:12px;height:12px;vertical-align:-2px;' /> "
            mana_text = mana_icon + str(unit["initial_mana"]) + " / " + str(unit["max_mana"])
        else:
            mana_text = ""
        body = "<div style='padding:8px;border-top:1px solid var(--tft-border);'>"
        body += "<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;'>"
        body += "<span style='font-weight:bold;color:#fff;'>" + unit["ability_name"] + "</span>"
        body += "<span style='color:#4fc3f7;font-size:11px;'>" + mana_text + "</span></div>"
        body += "<div style='color:#bbb;'>" + unit["ability_desc"] + "</div></div>"

        unit_tooltip_content = "<div class='tft-tooltip-content' style='border:2px solid " + cost_color + ";'>" + header + body + "</div>"
        portrait_html = "<div class='tft-tooltip-wrap'>" + portrait_img + unit_tooltip_content + "</div>"
        portrait_html_small = "<div class='tft-tooltip-wrap'>" + portrait_img_small + unit_tooltip_content + "</div>"

        star_span = "<span style='color:" + star_color + "; font-size:12px;'>&#9733;</span>"
        stars_html = "<div style='display:flex; justify-content:center; margin-top:2px;'>" + (star_span * unit["star_level"]) + "</div>"
        star_span_small = "<span style='color:" + star_color + "; font-size:8px;'>&#9733;</span>"
        stars_html_small = "<div style='text-align:center;'>" + (star_span_small * unit["star_level"]) + "</div>"

        item_imgs = ""
        item_imgs_small = ""
        for item in unit["item_details"]:
            if not item["icon_url"]:
                continue
            img_tag = "<img src='" + item["icon_url"] + "' style='width:18px;height:18px;border:1px solid var(--tft-border);' />"
            img_tag_small = "<img src='" + item["icon_url"] + "' style='width:10px;height:10px;border-radius:2px;' />"

            header_row = "<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
            header_row += "<img src='" + item["icon_url"] + "' style='width:24px;height:24px;border-radius:4px;' />"
            header_row += "<span style='font-weight:bold;font-size:16px;color:#fff;'>" + item["name"] + "</span>"
            header_row += "</div>"

            desc_row = "<div style='color:#bbb;white-space:pre-line;'>" + item["description"] + "</div>"

            if item["component_icons"]:
                combo_row = "<div style='margin:10px 12px 0; padding:8px 0; border-top:1px solid var(--tft-border); display:flex; align-items:center; gap:6px;'>"
                for i, comp_icon in enumerate(item["component_icons"]):
                    if i > 0:
                        combo_row += "<span style='color:#888;'>+</span>"
                    combo_row += "<img src='" + comp_icon + "' style='width:22px;height:22px;border-radius:4px;' />"
                combo_row += "</div>"
            else:
                combo_row = "<div style='margin:10px 12px 0; padding:8px 0; border-top:1px solid var(--tft-border);'><span style='color:#e05a5a; font-weight:bold; font-style:italic;'>Not craftable</span></div>"
            item_tooltip_content = "<div class='tft-tooltip-content' style='border:2px solid var(--tft-gold-dim);'>" + "<div style='padding:8px;'>" + header_row + desc_row + "</div>" + combo_row + "</div>"

            item_imgs += "<div class='tft-tooltip-wrap'>" + img_tag + item_tooltip_content + "</div>"
            item_imgs_small += "<div class='tft-tooltip-wrap'>" + img_tag_small + item_tooltip_content + "</div>"

        items_html = "<div style='display:flex; justify-content:center; margin-top:2px;'>" + item_imgs + "</div>"
        items_html_small = "<div style='display:flex; justify-content:center; gap:1px; min-height:10px;'>" + item_imgs_small + "</div>"

        units_html += "<div style='display:flex; flex-direction:column; align-items:center; width:70px;'>" + stars_html + portrait_html + items_html + "</div>"
        preview_units_html += "<div style='display:flex;flex-direction:column;align-items:center;gap:1px;flex:0 0 auto;'>" + stars_html_small + portrait_html_small + items_html_small + "</div>"

    companion_preview_html = (
        "<div style='position:relative;flex:0 0 auto;'>"
        "<img src='" + detail["companion_icon"] + "' style='width:52px;height:52px;border-radius:50%;object-fit:cover;object-position:center 30%;border:2px solid " + color + ";' />"
        "<div style='position:absolute;bottom:-3px;right:-5px;background:var(--tft-bg-panel);border:1px solid var(--tft-gold);border-radius:4px;font-size:10px;font-weight:700;color:#fff;padding:0 4px;line-height:1.4;'>" + str(level) + "</div>"
        "</div>"
    ) if detail["companion_icon"] else ""

    summary_html = (
        "<div style='display:flex;align-items:center;gap:16px;'>"
        + companion_preview_html +
        "<div style='min-width:70px;flex:0 0 auto;'>"
        "<div style='font-size:24px;font-weight:800;color:" + color + ";line-height:1;'>" + ordinal(placement) + "</div>"
        "<div style='font-size:11px;color:#888;white-space:nowrap;margin-top:3px;'>" + mode_label + "</div>"
        "<div style='font-size:11px;color:#666;white-space:nowrap;'>" + time_ago(game_datetime) + "</div>"
        "</div>"
        "<div style='display:flex;flex-direction:column;gap:5px;min-width:0;flex:1;'>"
        "<div style='display:flex;flex-wrap:wrap;gap:4px;'>" + preview_traits_html + "</div>"
        "<div style='display:flex;flex-wrap:wrap;gap:6px;padding-bottom:2px;'>" + preview_units_html + "</div>"
        "</div>"
        "<div class='tft-card-chevron' style='flex:0 0 auto;color:#888;font-size:16px;'>&#9660;</div>"
        "</div>"
    )

    card = "<details class='tft-card' style='border: 1px solid " + color + "40; margin-bottom: 14px; padding: 0; --card-accent: " + color + ";'>"
    card += "<summary style='padding: 14px 16px; cursor: pointer; font-size: 16px; color: white; border-left: 4px solid " + color + ";'>" + summary_html + "</summary>"
    card += "<div class='tft-card-content' style='border-top: 1px solid var(--tft-border); display: flex;'>"
    card += "<div style='flex: 1; min-width: 220px; background: rgba(212,175,55,0.05); border-radius: 0 0 0 10px;'>" + left_html + "</div>"
    card += "<div style='flex: 3; padding: 16px 16px 16px 24px;'>" + traits_html + units_html + "</div>"
    card += "</div></details>"

    all_cards_html += card

st.markdown(all_cards_html, unsafe_allow_html=True)

if matches_shown < len(history):
    remaining = len(history) - matches_shown
    if st.button(f"Show {min(MATCHES_PER_PAGE, remaining)} more matches ({remaining} remaining)"):
        st.session_state["matches_shown"] += MATCHES_PER_PAGE
        st.rerun()

st.markdown(_riot_disclaimer_html(), unsafe_allow_html=True)