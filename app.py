import streamlit as st
from stats import average_placement, top4_rate, placement_breakdown, most_played_units, match_history
from fetch_match import GAME_NAME, TAG_LINE
from match_detail import placement_color
from match_detail import get_match_detail, format_short_date, time_ago, ordinal
import altair as alt
import pandas as pd

st.markdown("""
    <style>
    .tft-tooltip-wrap { position: relative; display: inline-block; }
    .tft-tooltip-content {
        visibility: hidden; opacity: 0;
        position: absolute; bottom: 105%; left: 50%;
        transform: translateX(-50%);
        background: #0e1015; border-radius: 8px;
        width: 280px; min-width: 280px; max-width: 280px;
        box-sizing: border-box;
        font-size: 12px; color: #ddd;
        z-index: 1000; transition: opacity 0.15s ease; pointer-events: none;
        overflow: hidden; text-align: left; line-height: 1.5;
        overflow-wrap: break-word; word-break: break-word;
    }    
    .tft-tooltip-wrap:hover .tft-tooltip-content { visibility: visible; opacity: 1; }
    </style>
""", unsafe_allow_html=True)

st.title("TFT Coach — Set 18 Stats")

# --- Game mode selector ---
# Maps a human-readable label to the actual queue_id (or None for "all modes")
QUEUE_OPTIONS = {
    "Ranked": 1100,
    "Double Up": 1160,
    "Normal": 1090,
    "All modes": None,
}
selected_label = st.selectbox("Game mode", options=list(QUEUE_OPTIONS.keys()))
queue_id = QUEUE_OPTIONS[selected_label]

# --- Headline numbers ---
avg_placement = average_placement(queue_id=queue_id)
top4 = top4_rate(queue_id=queue_id)

col1, col2 = st.columns(2)
col1.metric("Average Placement", f"{avg_placement:.2f}" if avg_placement is not None else "No games")
col2.metric("Top 4 Rate", f"{top4:.1f}%" if avg_placement is not None else "No games")

# --- Placement breakdown ---
st.subheader("Placement Breakdown")
breakdown = placement_breakdown(queue_id=queue_id)

def placement_tier_color(p):
    if p == 1:
        return "#d4af37"
    elif p <= 4:
        return "#3ba55c"
    else:
        return "#c0392b"

chart_df = pd.DataFrame({
    "Placement": [str(row[0]) for row in breakdown],
    "Games": [row[1] for row in breakdown],
    "Color": [placement_tier_color(row[0]) for row in breakdown],
})

chart = alt.Chart(chart_df).mark_bar().encode(
    x=alt.X("Placement:N", axis=alt.Axis(labelAngle=0)),
    y=alt.Y("Games:Q"),
    color=alt.Color("Color:N", scale=None, legend=None)
).properties(height=300)

st.altair_chart(chart, use_container_width=True)

# --- Most played units ---
st.subheader("Most Played Units")
units = most_played_units(queue_id=queue_id)
st.table({
    "Unit": [u[0] for u in units],
    "Times Played": [u[1] for u in units],
    "Avg Placement": [f"{u[2]:.2f}" for u in units],
})

# --- Match history (clickable) ---
st.subheader("Match History")
history = match_history(queue_id=queue_id)

from patch_tracker import latest_diff

st.subheader("Patch Changes (Traits)")
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

all_cards_html = ""

for match_id, placement, level, gold_left, last_round, game_datetime in history:
    detail = get_match_detail(match_id, GAME_NAME, TAG_LINE)
    color = detail["placement_color"]
    label = f"{format_short_date(game_datetime)} &middot; {ordinal(placement)} Place &middot; Level {level} ({time_ago(game_datetime)})"

    left_html = "<div style='border-left: 6px solid " + color + "; padding-left: 10px;'>"

    top_row = "<div style='display:flex; align-items:center; gap:10px; margin-bottom:6px;'>"
    if detail["companion_icon"]:
        top_row += "<img src='" + detail["companion_icon"] + "' title='" + (detail["companion_name"] or "") + "' style='width:48px;height:48px;border-radius:50%;object-fit:cover;object-position:center 30%;border:2px solid " + color + ";' />"
    top_row += "<span style='font-size:22px;'>🏆 <span style='color:" + color + "; font-weight:bold;'>" + str(detail['placement']) + "</span></span>"
    top_row += "</div>"

    left_html += top_row
    left_html += "<h4>" + detail['player_name'] + "#" + detail['tag_line'] + "</h4>"
    left_html += "<p style='font-size:18px;'>🛡️ " + detail['rank_display'] + "</p>"
    left_html += "<p style='font-size:18px;'>🚩 " + detail['stage'] + "</p>"
    left_html += "<p style='font-size:18px;'>⚔️ " + str(detail['damage']) + "</p>"
    left_html += "<p style='font-size:18px;'>🪙 " + str(detail['board_value']) + "</p>"
    left_html += "<p style='font-size:14px; color:#aaa;'>" + detail['short_date'] + " (" + detail['time_ago'] + ")</p>"
    left_html += "</div>"

    traits_html = "<div style='display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px;'>"
    for trait in detail["traits"]:
        if trait["icon_url"]:
            trait_img = "<div style='position:relative;width:32px;height:32px;border:2px solid " + trait['color'] + ";border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;'><img src='" + trait['icon_url'] + "' style='width:20px;height:20px;filter:grayscale(100%);display:block;' /><div style='position:absolute;inset:0;background:" + trait['color'] + ";mix-blend-mode:color;'></div></div>"
        else:
            trait_img = "<div style='width:32px;height:32px;border:4px solid " + trait['color'] + ";border-radius:50%;'></div>"

        tt = trait.get("tooltip", {})
        tooltip_body = "<div style='padding:8px;'>"
        tooltip_body += "<div style='font-weight:bold;color:#fff;margin-bottom:4px;'>" + trait["name"] + "</div>"
        if tt.get("intro"):
            tooltip_body += "<div style='color:#bbb;margin-bottom:6px;'>" + tt["intro"] + "</div>"
        for bp in tt.get("breakpoints", []):
            bp_color = "#fff" if bp["reached"] else "#666"
            tooltip_body += "<div style='color:" + bp_color + ";margin-bottom:2px;'>" + bp["text"] + "</div>"
        tooltip_body += "</div>"

        traits_html += "<div class='tft-tooltip-wrap'>" + trait_img + "<div class='tft-tooltip-content' style='border:2px solid " + trait['color'] + ";'>" + tooltip_body + "</div></div>"
    traits_html += "</div>"

    units_html = "<div style='display:flex; flex-wrap:wrap; gap:12px;'>"
    for unit in detail["units"]:
        star_color = {1: "#cd7f32", 2: "#e8e8e8", 3: "#ffd700", 4: "#50c878"}.get(unit["star_level"], "#ffffff")
        cost_color = {1: "#a0a0a0", 2: "#1e9e4a", 3: "#3a7bd5", 4: "#a04dcf", 5: "#e0b040"}.get(unit["cost"], "#a0a0a0")

        if unit["portrait_url"]:
            portrait_img = "<img src='" + unit['portrait_url'] + "' style='width:64px;height:64px;border:2px solid " + cost_color + ";border-radius:6px;' />"
        else:
            portrait_img = "<div style='width:64px;height:64px;background:#333;border-radius:6px;'></div>"

        if unit["splash_url"]:
            header = "<div style='position:relative;height:130px;background-image:url(" + unit["splash_url"] + ");background-size:cover;background-position:center;display:flex;flex-direction:column;justify-content:flex-end;'>"
            header += "<div style='position:absolute;inset:0;background:linear-gradient(to bottom, rgba(0,0,0,0.15) 40%, rgba(14,16,21,0.92));'></div>"
        else:
            header = "<div style='position:relative;height:130px;background:#1a1d26;display:flex;flex-direction:column;justify-content:flex-end;'>"

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
        body = "<div style='padding:8px;border-top:1px solid #2e323d;'>"
        body += "<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;'>"
        body += "<span style='font-weight:bold;color:#fff;'>" + unit["ability_name"] + "</span>"
        body += "<span style='color:#4fc3f7;font-size:11px;'>" + mana_text + "</span></div>"
        body += "<div style='color:#bbb;'>" + unit["ability_desc"] + "</div></div>"

        portrait_html = "<div class='tft-tooltip-wrap'>" + portrait_img + "<div class='tft-tooltip-content' style='border:2px solid " + cost_color + ";'>" + header + body + "</div></div>"

        star_span = "<span style='color:" + star_color + "; font-size:12px;'>&#9733;</span>"
        stars_html = "<div style='display:flex; justify-content:center; margin-top:2px;'>" + (star_span * unit["star_level"]) + "</div>"

        item_imgs = ""
        for item in unit["item_details"]:
            if not item["icon_url"]:
                continue
            img_tag = "<img src='" + item["icon_url"] + "' style='width:18px;height:18px;border:1px solid #222;' />"

            header_row = "<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
            header_row += "<img src='" + item["icon_url"] + "' style='width:24px;height:24px;border-radius:4px;' />"
            header_row += "<span style='font-weight:bold;font-size:16px;color:#fff;'>" + item["name"] + "</span>"
            header_row += "</div>"

            desc_row = "<div style='color:#bbb;white-space:pre-line;'>" + item["description"] + "</div>"

            if item["component_icons"]:
                combo_row = "<div style='margin:10px 12px 0; padding:8px 0; border-top:1px solid #333; display:flex; align-items:center; gap:6px;'>"
                for i, comp_icon in enumerate(item["component_icons"]):
                    if i > 0:
                        combo_row += "<span style='color:#888;'>+</span>"
                    combo_row += "<img src='" + comp_icon + "' style='width:22px;height:22px;border-radius:4px;' />"
                combo_row += "</div>"
            else:
                combo_row = "<div style='margin:10px 12px 0; padding:8px 0; border-top:1px solid #333;'><span style='color:#e05a5a; font-weight:bold; font-style:italic;'>Not craftable</span></div>"
            tooltip_body = "<div style='padding:8px;'>" + header_row + desc_row + "</div>" + combo_row

            item_imgs += "<div class='tft-tooltip-wrap'>" + img_tag + "<div class='tft-tooltip-content' style='border:2px solid #555;'>" + tooltip_body + "</div></div>"

        items_html = "<div style='display:flex; justify-content:center; margin-top:2px;'>" + item_imgs + "</div>"

        units_html += "<div style='display:flex; flex-direction:column; align-items:center; width:70px;'>" + stars_html + portrait_html + items_html + "</div>"

    card = "<details style='border: 3px solid " + color + "; border-radius: 8px; margin-bottom: 10px; padding: 0; background: #14171f;'>"
    card += "<summary style='padding: 12px; cursor: pointer; font-size: 16px; color: white;'>" + label + "</summary>"
    card += "<div style='padding: 16px; border-top: 1px solid #333; display: flex; gap: 24px;'>"
    card += "<div style='flex: 1; min-width: 220px;'>" + left_html + "</div>"
    card += "<div style='flex: 3;'>" + traits_html + units_html + "</div>"
    card += "</div></details>"

    all_cards_html += card

st.markdown(all_cards_html, unsafe_allow_html=True)