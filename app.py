import random
import streamlit as st
from stats import average_placement, top4_rate, win_rate, placement_breakdown, most_played_units, match_history
from fetch_match import GAME_NAME, TAG_LINE
from database import get_game
from match_detail import placement_color
from match_detail import get_match_detail, time_ago, ordinal
from match_detail import get_metatft_champion_lookup, resolve_metatft_markup, clean_game_text
from game_data import build_champion_data, tex_path_to_url, build_companion_lookup, companion_icon_url
from metatft_rating import fetch_rating_history, short_rank_label
import altair as alt
import pandas as pd


@st.cache_data(ttl=600)
def _cached_rating_history(game_name: str, tag_line: str, queue_id: int = 1100) -> list:
    try:
        return fetch_rating_history(game_name, tag_line, queue_id=queue_id)
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

    .stApp {
        background-color: var(--tft-bg-deep);
    }
    .stApp::after {
        content: ""; position: fixed; inset: 0; z-index: -1;
        background: radial-gradient(ellipse at top, rgba(20,23,31,0.55) 0%, var(--tft-bg-deep) 70%);
    }

    h1, h2, h3 { letter-spacing: 0.5px; }

    .tft-divider {
        height: 2px; margin: 4px 0 18px 0; border: none;
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
        background: linear-gradient(180deg, var(--tft-bg-panel) 0%, #10131a 100%) !important;
        box-shadow: 0 2px 10px rgba(0,0,0,0.35);
    }
    details.tft-card summary { list-style: none; }
    details.tft-card summary::-webkit-details-marker { display: none; }
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
        transition: height 0.32s ease-in, opacity 0.2s ease-in, content-visibility 0.32s allow-discrete;
    }
    details.tft-card[open]::details-content {
        height: auto;
        opacity: 1;
        transition: height 0.4s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.25s ease-out, content-visibility 0.4s allow-discrete;
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

    #tft-hero-card {
        min-height: 250px; border-radius: 14px; overflow: hidden;
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
    </style>
""", unsafe_allow_html=True)

# --- Hero splash: a random Set 18 champion, refreshed each page load.
# Reused both for the banner and (very darkened) as the page backdrop. ---
_champion_data = build_champion_data()
_splash_candidates = [
    c for c in _champion_data.values()
    if c.get("cost", 0) >= 4 and c.get("icon") and c["icon"] != "None" and c.get("traits")
]
if "hero_champion" not in st.session_state:
    st.session_state["hero_champion"] = random.choice(_splash_candidates) if _splash_candidates else None
_hero = st.session_state["hero_champion"]
_hero_splash = tex_path_to_url(_hero["icon"]) if _hero else None

if _hero_splash:
    st.markdown(
        "<style>.stApp::before { content: \"\"; position: fixed; inset: 0; z-index: -1; "
        "background-image: url(" + _hero_splash + "); background-size: cover; "
        "background-position: center 20%; background-attachment: fixed; "
        "opacity: 0.14; filter: saturate(0.6); }</style>",
        unsafe_allow_html=True,
    )

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
avg_placement = average_placement(queue_id=queue_id)
top4 = top4_rate(queue_id=queue_id)
win_pct = win_rate(queue_id=queue_id)

history_for_card = match_history(queue_id=queue_id)

RANK_COLORS = {
    "IRON": "#8c7853", "BRONZE": "#a0673c", "SILVER": "#9fa8b0", "GOLD": "#d4af37",
    "PLATINUM": "#4fd1c5", "EMERALD": "#2ecc71", "DIAMOND": "#5aa9e6",
    "MASTER": "#b362ff", "GRANDMASTER": "#ff5c5c", "CHALLENGER": "#ffe680",
}

avatar_url = None
rank_html = "<span style='color:#888;'>Not tracked for this game</span>"
if history_for_card:
    latest_game = get_game(history_for_card[0][0])
    (_, _, _, _, _, _, _, _, rank_tier, rank_division, league_points, _,
     companion_content_id, _) = latest_game

    companion_info = build_companion_lookup().get(companion_content_id)
    if companion_info:
        avatar_url = companion_icon_url(companion_info["loadoutsIcon"])

    if rank_tier:
        rank_emblem = f"https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default/images/ranked-emblem/emblem-{rank_tier.lower()}.png"
        rank_color = RANK_COLORS.get(rank_tier.upper(), "#d4af37")
        rank_html = (
            "<img src='" + rank_emblem + "' style='width:26px;height:26px;vertical-align:-7px;margin-right:6px;' />"
            "<span style='color:" + rank_color + ";font-weight:700;'>" + rank_tier.title() + " " + (rank_division or "") + "</span>"
            "<span style='color:#aaa;margin-left:6px;'>" + str(league_points or 0) + " LP</span>"
        )

avatar_html = (
    "<img src='" + avatar_url + "' style='width:92px;height:92px;border-radius:50%;border:3px solid var(--tft-gold);object-fit:cover;object-position:center 30%;box-shadow:0 2px 12px rgba(0,0,0,0.6);' />"
    if avatar_url else
    "<div style='width:92px;height:92px;border-radius:50%;border:3px solid var(--tft-gold);background:var(--tft-bg-raised);'></div>"
)

def _stat_tile(value: str, label: str, value_color: str = None) -> str:
    color_style = f"color:{value_color};" if value_color else ""
    return "<div class='tft-stat-tile'><div class='big' style='" + color_style + "'>" + value + "</div><div class='label'>" + label + "</div></div>"

# LP change over the last 10 games in whichever ladder matches the
# current mode filter (Ranked/Double Up have separate ladders) — same
# numeric rating scale the progression chart uses, so a plain difference
# already correctly accounts for crossing tier boundaries.
_rating_queue_id, _rating_queue_label = _rating_queue_for_filter(queue_id)
recent_rating = _cached_rating_history(GAME_NAME, TAG_LINE, queue_id=_rating_queue_id)[-11:] if _rating_queue_id else []
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
    "<div style='position:absolute;inset:0;background:linear-gradient(100deg, rgba(10,12,16,0.94) 32%, rgba(10,12,16,0.6) 68%, rgba(10,12,16,0.3) 100%);'></div>"

    "<div style='position:absolute;top:26px;left:28px;'>"
    "<div style='font-size:30px;font-weight:800;color:#fff;letter-spacing:1px;text-shadow:0 2px 8px rgba(0,0,0,0.8);'>TFT COACH</div>"
    "<div style='font-size:13px;color:var(--tft-gold);letter-spacing:2px;text-transform:uppercase;'>Set 18 &middot; Enchanted Wilds</div>"
    "</div>"

    "<div style='position:absolute;top:26px;right:28px;display:flex;align-items:center;gap:14px;'>"
    "<div style='text-align:right;'>"
    "<div style='font-size:24px;'><b style='color:#fff;'>" + GAME_NAME + "</b><span style='color:#aaa;'>#" + TAG_LINE + "</span></div>"
    "<div style='margin-top:6px;font-size:16px;'>" + rank_html + "</div>"
    "</div>"
    + avatar_html +
    "</div>"

    "<div style='position:absolute;bottom:22px;left:50%;transform:translateX(-50%);display:flex;gap:12px;'>"
    + stats_html +
    "</div>"

    "</div>"
)
st.markdown(hero_card_html, unsafe_allow_html=True)

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

last_n_games = match_history(queue_id=queue_id)[:30]
grid_html = "<div style='display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;'>"
for match_id, placement, level, gold_left, last_round, game_datetime, _queue_id in last_n_games:
    bg, fg = GRID_PLACEMENT_COLORS.get(placement, GRID_DEFAULT_COLOR)
    grid_html += (
        "<div style='width:38px;height:38px;border-radius:6px;background:" + bg + ";color:" + fg + ";"
        "display:flex;align-items:center;justify-content:center;font-weight:800;font-size:15px;flex:0 0 auto;'>"
        + str(placement) + "</div>"
    )
grid_html += "</div>"

if last_n_games:
    with st.container(border=True):
        st.markdown("<div style='color:#999;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px;'>Last " + str(len(last_n_games)) + " Games</div>", unsafe_allow_html=True)
        st.markdown(grid_html, unsafe_allow_html=True)

# --- Placement bar chart + rank progression line chart, side by side ---
col_bar, col_line = st.columns(2)

breakdown = placement_breakdown(queue_id=queue_id)
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
        rating_history = _cached_rating_history(GAME_NAME, TAG_LINE, queue_id=_rating_queue_id) if _rating_queue_id else []
        if not _rating_queue_id:
            st.caption("Normal games have no ranked ladder.")
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
            st.caption("Rank history unavailable right now.")

# --- Most played units ---
st.subheader("Most Played Units")
st.markdown("<hr class='tft-divider' />", unsafe_allow_html=True)
units = most_played_units(queue_id=queue_id)
_metatft_champions_ref = get_metatft_champion_lookup(_champion_data)

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
    icon_html = "<div class='tft-tooltip-wrap' style='margin-right:10px;'>" + icon_html.replace("margin-right:10px;", "") + tooltip_content + "</div>"

    row_bg = "rgba(255,255,255,0.02)" if i % 2 else "transparent"
    rows_html += (
        "<div style='display:flex;align-items:center;padding:8px 12px;background:" + row_bg + ";border-radius:6px;'>"
        "<span style='color:#666;font-size:12px;width:20px;'>" + str(i + 1) + "</span>"
        + icon_html +
        "<span style='flex:1;color:#eee;font-weight:600;'>" + name + "</span>"
        "<span style='color:#999;font-size:13px;width:100px;text-align:right;'>" + str(times_played) + " games</span>"
        "<span style='color:" + placement_color(round(avg_place)) + ";font-size:13px;width:100px;text-align:right;font-weight:600;'>" + f"{avg_place:.2f}" + " avg</span>"
        "</div>"
    )

with st.container(border=True):
    st.markdown("<div style='padding:10px 0;'>" + rows_html + "</div>", unsafe_allow_html=True)

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
st.subheader("Match History")
st.markdown("<hr class='tft-divider' />", unsafe_allow_html=True)
history = match_history(queue_id=queue_id)

# Rendering every match's full HTML (preview + expanded content, tooltips
# included) on every single rerun — which Streamlit does on any widget
# interaction, including just switching game mode — was the actual source
# of the lag: with ~30 matches each carrying several KB of tooltip markup,
# that's a large payload to re-send and re-parse every time, regardless of
# which card (if any) is actually open. Paginating cuts that down sharply.
MATCHES_PER_PAGE = 15
if st.session_state.get("history_queue_id") != queue_id:
    st.session_state["history_queue_id"] = queue_id
    st.session_state["matches_shown"] = MATCHES_PER_PAGE
matches_shown = st.session_state["matches_shown"]
visible_history = history[:matches_shown]

STAR_COLORS = {1: "#cd7f32", 2: "#e8e8e8", 3: "#ffd700", 4: "#50c878"}
COST_COLORS = {1: "#a0a0a0", 2: "#1e9e4a", 3: "#3a7bd5", 4: "#a04dcf", 5: "#e0b040"}

all_cards_html = ""

for match_id, placement, level, gold_left, last_round, game_datetime, match_queue_id in visible_history:
    detail = get_match_detail(match_id, GAME_NAME, TAG_LINE)
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
            trait_img_small = "<div style='position:relative;width:22px;height:22px;border:1.5px solid " + trait['color'] + ";border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;flex:0 0 auto;'><img src='" + trait['icon_url'] + "' style='width:13px;height:13px;filter:grayscale(100%);display:block;' /><div style='position:absolute;inset:0;background:" + trait['color'] + ";mix-blend-mode:color;'></div></div>"
        else:
            trait_img = "<div style='width:32px;height:32px;border:4px solid " + trait['color'] + ";border-radius:50%;'></div>"
            trait_img_small = "<div style='width:22px;height:22px;border:3px solid " + trait['color'] + ";border-radius:50%;flex:0 0 auto;'></div>"

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
            portrait_img_small = "<img src='" + unit['portrait_url'] + "' style='width:36px;height:36px;border:1.5px solid " + cost_color + ";border-radius:5px;' />"
        else:
            portrait_img = "<div style='width:64px;height:64px;background:var(--tft-bg-raised);border-radius:6px;'></div>"
            portrait_img_small = "<div style='width:36px;height:36px;background:var(--tft-bg-raised);border:1.5px solid " + cost_color + ";border-radius:5px;'></div>"

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

    card = "<details class='tft-card' style='border: 1px solid " + color + "40; margin-bottom: 12px; padding: 0;'>"
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