from datetime import datetime
import re
from database import get_game, get_units_for_match, get_traits_for_match
from game_analysis import stage_round, board_value
from game_data import (
    build_champion_data, build_item_data, build_trait_data,
    build_companion_lookup, companion_icon_url, tex_path_to_url,
    build_trait_static_data,
)
from metatft_data import build_metatft_champion_lookup, build_metatft_item_lookup, build_metatft_item_name_lookup

def format_short_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%d %b")

_metatft_champion_cache = None
_metatft_item_cache = None
_metatft_item_name_cache = None


def get_metatft_item_lookup() -> dict:
    global _metatft_item_cache
    if _metatft_item_cache is None:
        _metatft_item_cache = build_metatft_item_lookup()
    return _metatft_item_cache

def get_metatft_item_name_lookup() -> dict:
    global _metatft_item_name_cache
    if _metatft_item_name_cache is None:
        _metatft_item_name_cache = build_metatft_item_name_lookup()
    return _metatft_item_name_cache

def get_metatft_champion_lookup(champion_data: dict) -> dict:
    global _metatft_champion_cache
    if _metatft_champion_cache is None:
        _metatft_champion_cache = build_metatft_champion_lookup(champion_data)
    return _metatft_champion_cache

def time_ago(timestamp_ms: int) -> str:
    then = datetime.fromtimestamp(timestamp_ms / 1000)
    seconds = (datetime.now() - then).total_seconds()

    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    else:
        return f"{int(seconds // 86400)}d ago"

def strip_markup_tags(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'<br\s*/?>', '<br>', text, flags=re.IGNORECASE)
    text = re.sub(r'</?(?!br)[a-zA-Z][^>]*>', '', text)
    text = text.replace('\r', '').replace('\\n', ' ')
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

def clean_game_text(text: str) -> str:
    """
    For champion ability text — scaling values can't be resolved,
    so placeholders become '?'.
    """
    if not text:
        return ""
    text = re.sub(r'@[^@]+@', '?', text)
    text = re.sub(r'%i:[^%]+%', '', text)
    return strip_markup_tags(text)

STAT_ICON_URLS = {
    "AD": "https://ap.tft.tools/img/general/ad.png?w=14",
    "AP": "https://ap.tft.tools/img/general/ap.png?w=14",
    "Health": "https://ap.tft.tools/img/general/hp.png?w=14",
    "Armor": "https://ap.tft.tools/img/general/armor.png?w=14",
    "Magic Resist": "https://ap.tft.tools/img/general/mr.png?w=14",
    "Attack Speed": "https://ap.tft.tools/img/general/as.png?w=14",
    "Ability Power": "https://ap.tft.tools/img/general/ap.png?w=14",
    "Attack Damage": "https://ap.tft.tools/img/general/ad.png?w=14",
    "Mana Regen": "https://ap.tft.tools/img/general/mana_regen.png?w=14",
    "Durability": "https://ap.tft.tools/img/general/dr.png?w=14",
    "Crit Chance": "https://ap.tft.tools/img/general/crit_chance.png?w=14",
    "Crit Damage": "https://ap.tft.tools/img/general/crit_damage.png?w=14",
    "Critical Strike Chance": "https://ap.tft.tools/img/general/crit_chance.png?w=14",
}

SCALE_TOKEN_ICONS = {
    "scaleAD": STAT_ICON_URLS["AD"],
    "scaleAP": STAT_ICON_URLS["AP"],
    "scaleAS": STAT_ICON_URLS["Attack Speed"],
    "scaleArmor": STAT_ICON_URLS["Armor"],
    "scaleMR": STAT_ICON_URLS["Magic Resist"],
    "scaleHealth": STAT_ICON_URLS["Health"],
    "scaleManaRegen": STAT_ICON_URLS["Mana Regen"],
    "scaleDR": STAT_ICON_URLS["Durability"],
    "scaleCrit": STAT_ICON_URLS["Crit Chance"],
    "scaleCritMult": STAT_ICON_URLS["Crit Damage"],
}


def render_scale_icon(token: str) -> str:
    url = SCALE_TOKEN_ICONS.get(token)
    if not url:
        return ""
    return f"<img src='{url}' style='width:14px;height:14px;vertical-align:-2px;' />"

def insert_stat_icons(text: str) -> str:
    """
    Replaces stat name mentions with a small inline icon + the name,
    e.g. 'AD' -> '<img .../> AD'. Longer names are matched first so
    'Magic Resist' isn't partially caught by a shorter overlapping term.
    """
    for name in sorted(STAT_ICON_URLS, key=len, reverse=True):
        icon_html = f"<img src='{STAT_ICON_URLS[name]}' style='width:14px;height:14px;vertical-align:-2px;' /> {name}"
        text = re.sub(rf'\b{re.escape(name)}\b', icon_html, text)
    return text

def insert_damage_type_icons(text: str) -> str:
    """
    'physical damage' implies AD scaling, 'magic damage' implies AP —
    standard TFT convention. Inserts the relevant icon right before
    the number that precedes each phrase.
    """
    def make_replacer(stat_name):
        icon_html = f"<img src='{STAT_ICON_URLS[stat_name]}' style='width:14px;height:14px;vertical-align:-2px;' /> "
        return lambda m: icon_html + m.group(0)

    text = re.sub(r'[\d.]+(?:%?/[\d.]+)*%?\s+physical damage', make_replacer("AD"), text)
    text = re.sub(r'[\d.]+(?:%?/[\d.]+)*%?\s+magic damage', make_replacer("AP"), text)
    return text

def format_constant_value(name: str, value) -> str:
    if isinstance(value, float):
        rounded = round(value, 2)
        if rounded == int(rounded):
            return str(int(rounded))
        return str(rounded)
    return str(value)


def resolve_trait_breakpoints(trait_static: dict, num_units_player: int) -> dict:
    if not trait_static:
        return {"intro": "", "breakpoints": []}

    text = trait_static.get("tooltip_text", "")
    parts = text.split("<row>")
    row_texts = [p.split("</row>")[0] for p in parts[1:]]

    innate_constants = {}
    for group in trait_static.get("innate_trait_sets", []):
        for c in group.get("constants", []):
            innate_constants[c["name"]] = c["value"]

    def substitute_intro_token(m):
        expr = m.group(1)
        if "*" in expr:
            const_name, multiplier = expr.split("*", 1)
            base = innate_constants.get(const_name.strip())
            if base is None:
                return m.group(0)
            return format_constant_value(const_name, base * float(multiplier))
        base = innate_constants.get(expr)
        if base is None:
            return m.group(0)
        return format_constant_value(expr, base)

    intro_raw = re.sub(r'@([^@]+)@', substitute_intro_token, parts[0])
    scale_tokens_intro = {}
    def stash_intro_icon(m):
        placeholder = f"§ICON{len(scale_tokens_intro)}§"
        scale_tokens_intro[placeholder] = render_scale_icon(m.group(1))
        return placeholder
    intro_raw = re.sub(r'%i:([^%]+)%', stash_intro_icon, intro_raw)
    intro = strip_markup_tags(intro_raw)
    for placeholder, icon_html in scale_tokens_intro.items():
        intro = intro.replace(placeholder, icon_html)
    intro = insert_damage_type_icons(intro)
    intro = insert_stat_icons(intro)

    breakpoints = trait_static.get("conditional_trait_sets", [])
    resolved = []
    for i, bp in enumerate(breakpoints):
        row_text = row_texts[i] if i < len(row_texts) else ""
        row_text = row_text.replace("<br>", " ")
        row_text = row_text.replace("@MinUnits@", str(bp.get("min_units", "")))

        constants_by_name = dict(innate_constants)
        for c in bp.get("constants", []):
            constants_by_name[c["name"]] = c["value"]

        def substitute_token(match):
            expr = match.group(1)
            if "*" in expr:
                const_name, multiplier = expr.split("*", 1)
                base = constants_by_name.get(const_name.strip())
                if base is None:
                    return match.group(0)
                return format_constant_value(const_name, base * float(multiplier))
            base = constants_by_name.get(expr)
            if base is None:
                return match.group(0)
            return format_constant_value(expr, base)

        row_text = re.sub(r'@([^@]+)@', substitute_token, row_text)
        scale_tokens = {}
        def stash_icon(m):
            placeholder = f"§ICON{len(scale_tokens)}§"
            scale_tokens[placeholder] = render_scale_icon(m.group(1))
            return placeholder
        row_text = re.sub(r'%i:([^%]+)%', stash_icon, row_text)

        clean_text = strip_markup_tags(row_text)
        for placeholder, icon_html in scale_tokens.items():
            clean_text = clean_text.replace(placeholder, icon_html)
        clean_text = insert_damage_type_icons(clean_text)
        clean_text = insert_stat_icons(clean_text)

        resolved.append({
            "text": clean_text,
            "reached": num_units_player >= bp.get("min_units", 0),
        })

    return {"intro": intro, "breakpoints": resolved}

def build_trait_name_lookup(set_number: int = 18) -> dict:
    """Maps a trait's display name -> its full data, for champion tooltips."""
    from game_data import build_trait_data
    return {t["name"]: t for t in build_trait_data(set_number).values()}

def placement_color(placement: int) -> str:
    if placement == 1:
        return "#d4af37"  # gold
    elif placement <= 4:
        return "#3ba55c"  # green
    else:
        return "#c0392b"  # red

def ordinal(n: int) -> str:
    """
    Converts a number into its ordinal form: 1 -> '1st', 2 -> '2nd',
    3 -> '3rd', 4 -> '4th', 11 -> '11th', etc.
    """
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"

def trait_style_color(style: int) -> str:
    return {0: "#555555", 1: "#cd7f32", 2: "#c0c0c0", 3: "#ffd700", 4: "#e066ff"}.get(style, "#555555")    

def corrected_trait_style(trait_name: str, num_units: int, style: int) -> int:
    """
    Riot's raw 'style' value marks some small traits as prismatic (4) even
    at a modest unit count. This is an unverified heuristic, not confirmed
    against Riot's actual tier definitions — treat traits with 6 or fewer
    units as gold instead when marked prismatic, since experience suggests
    this set hasn't produced a real prismatic below that count.

    DA_Riftbeast18 is a known exception: even at 10 units, it's genuinely
    gold, not prismatic, so it's hardcoded here regardless of unit count.
    """
    if style != 4:
        return style
    if trait_name == "DA_Riftbeast18":
        return 3
    if num_units <= 6:
        return 3
    return style

METATFT_ICON_URLS = {
    "ad": STAT_ICON_URLS["AD"],
    "ap": STAT_ICON_URLS["AP"],
    "health": STAT_ICON_URLS["Health"],
    "armor": STAT_ICON_URLS["Armor"],
    "mr": STAT_ICON_URLS["Magic Resist"],
    "as": STAT_ICON_URLS["Attack Speed"],
    "manaregen": STAT_ICON_URLS["Mana Regen"],
    "dura": STAT_ICON_URLS["Durability"],
    "critchance": "https://ap.tft.tools/img/general/crit_chance.png?w=14",
    "critdmg": "https://ap.tft.tools/img/general/crit_damage.png?w=14",
    "coin": "https://ap.tft.tools/img/general/gold.png?w=14",
    # omnivamp / damageamp: no confirmed ap.tft.tools asset for these —
    # rendered as plain text (no icon) rather than guessing a URL.
}

METATFT_PERCENT_FORMATS = {"p", "percent"}
METATFT_PERCENT_MINUS_ONE_FORMATS = {"percentminusone"}
METATFT_INVERTED_PERCENT_FORMATS = {"invertedpercent"}


def _metatft_icon_html(icon_attr: str) -> str:
    """
    metatft names every icon a value needs directly in its markup
    (e.g. icon="icon.AD, icon.AP" for a value that scales off both) —
    this replaces the old insert_stat_icons/insert_damage_type_icons
    text-guessing heuristics with exact attribution.
    """
    html = ""
    for token in (icon_attr or "").split(","):
        token = token.strip().lower()
        if token.startswith("icon."):
            token = token[len("icon."):]
        url = METATFT_ICON_URLS.get(token)
        if url:
            html += f"<img src='{url}' style='width:14px;height:14px;vertical-align:-2px;' /> "
    return html

METATFT_STYLE_ICON_FALLBACK = {
    "colorphysical": "ad",
    "colormagic": "ap",
    "colorhealth": "health",
}


def _metatft_style_fallback_icon(attrs: str) -> str:
    """
    Some values carry a style="colorX" attribute but no icon= at all —
    for the unambiguous color names (physical/magic/health always mean
    AD/AP/Health in this game's convention) fall back to that. Deliberately
    skips "colorStat" (could mean AS, AP, or others depending on the row —
    guessing here is exactly the bug this migration replaced) and
    "colorRadiant" (a highlight color for Radiant-item bonuses, not a
    stat type — correctly has no icon).
    """
    style_m = re.search(r'style="(color\w+)"', attrs, re.IGNORECASE)
    if not style_m:
        return ""
    token = METATFT_STYLE_ICON_FALLBACK.get(style_m.group(1).lower())
    return _metatft_icon_html(f"icon.{token}") if token else ""

# Last-resort fallback for values with neither icon= nor a usable style=
# (the "colorStat" case — could mean AS, AP, AD, etc. depending on the
# specific value). TFT's own writing convention always spells out the
# stat name in plain words right after a resolved number, so — verified
# individually across every real case in the current dataset before
# adding each entry here — peeking at just the next few words is safe
# for these specific phrases. Deliberately excludes "Shield" (checked:
# genuinely varies AP/Health/both depending on the unit, e.g. Taric's
# is Health-only) and bare "damage" (ambiguous without a physical/magic
# qualifier) rather than guess.
METATFT_LOOKAHEAD_ICON_PATTERNS = [
    (re.compile(r'^\s*Armor and Magic Resist', re.IGNORECASE), "icon.armor, icon.mr"),
    (re.compile(r'^\s*(?:and\s+)?Attack Speed', re.IGNORECASE), "icon.as"),
    (re.compile(r'^\s*(?:and\s+)?Attack Damage', re.IGNORECASE), "icon.ad"),
    (re.compile(r'^\s*(?:and\s+)?Ability Power', re.IGNORECASE), "icon.ap"),
    (re.compile(r'^\s*(?:max )?Health\b', re.IGNORECASE), "icon.health"),
    (re.compile(r'^\s*Durability', re.IGNORECASE), "icon.dura"),
    (re.compile(r'^\s*physical damage', re.IGNORECASE), "icon.ad"),
    (re.compile(r'^\s*magic damage', re.IGNORECASE), "icon.ap"),
    (re.compile(r'^\s*Critical Strike Chance', re.IGNORECASE), "icon.critchance"),
]


def _metatft_lookahead_icon(following_text: str) -> str:
    for pattern, icon_tokens in METATFT_LOOKAHEAD_ICON_PATTERNS:
        if pattern.match(following_text):
            return _metatft_icon_html(icon_tokens)
    return ""

def _format_metatft_number(value, fmt: str) -> str:
    fmt_key = (fmt or "").strip().lower()
    if fmt_key in METATFT_PERCENT_MINUS_ONE_FORMATS:
        value = (value - 1) * 100
    elif fmt_key in METATFT_INVERTED_PERCENT_FORMATS:
        value = (1 - value) * 100
    elif fmt_key in METATFT_PERCENT_FORMATS:
        value = value * 100
    text = format_constant_value("", value)
    if fmt_key in METATFT_PERCENT_FORMATS | METATFT_PERCENT_MINUS_ONE_FORMATS | METATFT_INVERTED_PERCENT_FORMATS:
        text += "%"
    return text

def _resolve_metatft_curve_value(curve_values: dict, row: str, star_level: int):
    """
    curve_values entries are [star_threshold, value] pairs (e.g.
    [[1, 0.25], [4, 0.25]]) — pick the value at the highest threshold
    not exceeding star_level, falling back to the lowest entry.
    """
    entries = curve_values.get(row)
    if not entries:
        return None
    entries = sorted(entries, key=lambda e: e[0])
    chosen = entries[0][1]
    for threshold, value in entries:
        if threshold <= star_level:
            chosen = value
        else:
            break
    return chosen

def resolve_metatft_markup(
    desc: str,
    attribute_values: dict,
    curve_values: dict,
    star_level: int = 1,
    riftbeast_active: bool = False,
) -> str:
    """
    Resolves metatft's description markup into display HTML. Unlike the
    old tactics.tools pipeline, icon attribution and glossary/reminder
    text are explicit in the markup itself (<TFTAttribute icon="...">,
    <Rules>...</>) rather than guessed from the resolved plain text.
    """
    if not desc:
        return ""

    text = desc

    def replace_attribute(m):
        attrs = m.group(1)
        icon_m = re.search(r'icon="([^"]*)"', attrs)
        attr_id_m = re.search(r'attributeI[Dd]="([^"]*)"', attrs)
        fmt_m = re.search(r'format="([^"]*)"', attrs)
        icon_html = _metatft_icon_html(icon_m.group(1)) if icon_m else _metatft_style_fallback_icon(attrs)
        if not icon_html:
            icon_html = _metatft_lookahead_icon(m.string[m.end():m.end() + 60])

        if not attr_id_m:
            return icon_html.strip()

        values = attribute_values.get(attr_id_m.group(1))
        if not values:
            return icon_html.strip()

        value = values[min(star_level - 1, len(values) - 1)]
        number_text = _format_metatft_number(value, fmt_m.group(1) if fmt_m else "")
        return f"{icon_html}{number_text}"

    # TFTAttribute tags are sometimes truly self-closing ("... />") and
    # sometimes opened-then-immediately-closed with a separate generic
    # "</>" marker — both forms appear in real data. Handled as two
    # separate passes (rather than one regex with an optional trailing
    # "</>") because an optional group would also swallow an unrelated
    # "</>" that belongs to an *enclosing* tag (e.g. a TFTConditionalStyle
    # wrapping a self-closed attribute as its last piece of content) —
    # confirmed this happens in real data. Splitting into two exact-match
    # passes means the self-closing form never touches text after its own
    # "/>", leaving any outer tag's closer alone for that tag's own pass.
    text = re.sub(r'<TFTAttribute([^>]*)/>', replace_attribute, text)
    text = re.sub(r'<TFTAttribute([^>]*)></>', replace_attribute, text)

    def replace_curve(m):
        attrs = m.group(1)
        row_m = re.search(r'row="([^"]*)"', attrs)
        fmt_m = re.search(r'format="([^"]*)"', attrs)
        icon_m = re.search(r'icon="([^"]*)"', attrs)
        icon_html = _metatft_icon_html(icon_m.group(1)) if icon_m else _metatft_style_fallback_icon(attrs)
        if not icon_html:
            icon_html = _metatft_lookahead_icon(m.string[m.end():m.end() + 60])
        if not row_m:
            return icon_html.strip()
        value = _resolve_metatft_curve_value(curve_values, row_m.group(1), star_level)
        if value is None:
            return m.group(0)
        number_text = _format_metatft_number(value, fmt_m.group(1) if fmt_m else "")
        return f"{icon_html}{number_text}"

    text = re.sub(r'<TFTCurveTable([^>]*)/>', replace_curve, text)
    text = re.sub(r'<TFTCurveTable([^>]*)></>', replace_curve, text)

    # Normalize the game's own paragraph breaks before adding our own tags.
    text = text.replace('\r', '')
    text = re.sub(r'\n{2,}', '<br><br>', text)
    text = re.sub(r'\n', '<br>', text)

    # Keyword tags close with the same generic "</>" marker as everything
    # else here — strip the tags but keep the highlighted word itself.
    text = re.sub(r'<[Kk]eyword>(.*?)</>', r'\1', text, flags=re.DOTALL)

    # colorRadiant/colorMagic/etc. appear as real child tags in some item
    # text (not just as style="..." attributes on TFTAttribute) — strip
    # them, no distinct styling for v1.
    text = re.sub(r'<color\w*>(.*?)</>', r'\1', text, flags=re.DOTALL | re.IGNORECASE)

    # Reminder/glossary text is explicitly tagged, e.g. <Rules>Wound:
    # Reduces healing received.</> — no more "was this word used earlier
    # as a bare word" heuristic needed. The source text already puts its
    # own paragraph break (\r\n\r\n, normalized to <br><br> above) before
    # a Rules block, so don't add another one here — that double-counts
    # and leaves a much bigger gap than intended.
    text = re.sub(
        r'<[Rr]ules>(.*?)</>',
        lambda m: f"<span style='color:#888; font-style:italic;'>{m.group(1)}</span>",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r'<[Bb]right>(.*?)</>', lambda m: f"<b>{m.group(1)}</b>", text, flags=re.DOTALL)
    text = re.sub(r'<dim>(.*?)</>', lambda m: f"<span style='color:#888;'>{m.group(1)}</span>", text, flags=re.DOTALL)

    def replace_conditional(m):
        attrs, inner = m.group(1), m.group(2)
        tag_m = re.search(r'tag="([^"]*)"', attrs)
        is_riftbeast_tag = tag_m is not None and "Riftbeast" in tag_m.group(1)
        active = riftbeast_active if is_riftbeast_tag else True
        return inner if active else f"<span style='color:#888;'>{inner}</span>"

    text = re.sub(r'<TFTConditionalStyle([^>]*)>(.*?)</>', replace_conditional, text, flags=re.DOTALL)

    # metatft isn't fully resolved for every entry — an unresolved shared
    # tooltip-snippet reference like "{Augment.Variant.HeartofSteel.Tooltip}"
    # can leak through as a literal curly-brace placeholder. Drop it rather
    # than show it, same as the old tactics.tools pipeline did for its own
    # template artifacts.
    text = re.sub(r'\{[^}]*\}', '', text)

    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

def get_match_detail(match_id: str, puuid: str, game_name: str, tag_line: str) -> dict:
    game_row = get_game(match_id, puuid)
    (_, placement, level, gold_left, last_round, set_number,
     game_datetime, queue_id, rank_tier, rank_division, league_points, total_damage,
     companion_content_id, companion_item_id) = game_row

    raw_units = get_units_for_match(match_id, puuid)
    raw_traits = get_traits_for_match(match_id, puuid)
    trait_data = build_trait_data(set_number)
    champion_data = build_champion_data(set_number)
    used_character_ids = {character_id for character_id, star_level, items_string in raw_units}

    traits_display = []

    def trait_sort_key(t):
        _, _, style, _, tier_total = t
        is_active = style != 0
        is_unique_active = tier_total == 1 and is_active
        return (not is_active, not is_unique_active, -style)  # unique traits first, then by style descending

    trait_static_lookup = build_trait_static_data()

    for trait_name, num_units, style, tier_current, tier_total in sorted(raw_traits, key=trait_sort_key):
        trait_info = trait_data.get(trait_name)
        name = trait_info["name"] if trait_info else trait_name
        icon_url = tex_path_to_url(trait_info["icon"]) if trait_info and trait_info.get("icon") else None
        display_style = corrected_trait_style(trait_name, num_units, style)
        is_unique = tier_total == 1 and style != 0
        if is_unique:
            color = "#ff8c00"  # bright orange for unique traits
        else:
            display_style = corrected_trait_style(trait_name, num_units, style)
            color = trait_style_color(display_style)

        tooltip = resolve_trait_breakpoints(trait_static_lookup.get(trait_name), num_units)

        roster = []
        if trait_info:
            trait_display_name = trait_info["name"]
            for cid, champ in champion_data.items():
                if trait_display_name in (champ.get("traits") or []):
                    roster.append({
                        "name": champ.get("name", cid),
                        "icon_url": tex_path_to_url(champ["squareIcon"]) if champ.get("squareIcon") else None,
                        "used_this_match": cid in used_character_ids,
                    })
            roster.sort(key=lambda u: (not u["used_this_match"], u["name"]))

        traits_display.append({
            "name": name,
            "icon_url": icon_url,
            "color": color,
            "num_units": num_units,
            "tooltip": tooltip,
            "roster": roster,
        })
    item_data = build_item_data()
    metatft_items = get_metatft_item_lookup()
    metatft_item_names = get_metatft_item_name_lookup()

    trait_by_name = build_trait_name_lookup(set_number)
    metatft_champions = get_metatft_champion_lookup(champion_data)
    riftbeast_active = any(t[0] == "DA_Riftbeast18" and t[2] != 0 for t in raw_traits)

    units_display = []
    for character_id, star_level, items_string in raw_units:
        champ = champion_data.get(character_id)
        name = champ["name"] if champ else character_id
        portrait_url = tex_path_to_url(champ["squareIcon"]) if champ and champ.get("squareIcon") else None
        cost = champ["cost"] if champ else 1

        item_ids = items_string.split(", ") if items_string else []
        item_details = []
        for item_id in item_ids:
            item = item_data.get(item_id)
            if not item:
                continue
            icon_url = tex_path_to_url(item["icon"]) if item.get("icon") else None
            item_name = item["name"]

            metatft_item = metatft_items.get(item_id) or metatft_item_names.get(item_name.lower())
            desc = ""
            if metatft_item and metatft_item.get("desc"):
                desc = resolve_metatft_markup(
                    metatft_item["desc"],
                    metatft_item.get("attributeValues", {}),
                    metatft_item.get("curveValues", {}),
                )

            composition = item.get("composition") or []
            component_icons = []
            for comp_id in composition:
                comp_item = item_data.get(comp_id)
                if comp_item and comp_item.get("icon"):
                    component_icons.append(tex_path_to_url(comp_item["icon"]))

            item_details.append({
                "name": item_name,
                "icon_url": icon_url,
                "description": desc,
                "component_icons": component_icons,
            })

        ability = champ.get("ability") if champ else None
        ability_name = ability["name"] if ability else ""

        metatft_champ = metatft_champions.get(character_id)
        metatft_ability = metatft_champ.get("ability") if metatft_champ else None
        if metatft_ability and metatft_ability.get("desc"):
            ability_desc = resolve_metatft_markup(
                metatft_ability["desc"],
                metatft_ability.get("attributeValues", {}),
                metatft_champ.get("curveValues", {}),
                star_level,
                riftbeast_active,
            )
        else:
            ability_desc = clean_game_text(ability["desc"]) if ability else ""

        stats = champ.get("stats", {}) if champ else {}
        initial_mana = int(stats.get("initialMana") or 0)
        max_mana = int(stats.get("mana") or 0)

        splash_path = None
        splash_is_fallback = True
        if champ:
            if champ.get("icon") and champ["icon"] != "None":
                splash_path = champ["icon"]
                splash_is_fallback = False
            else:
                for key in ("squareIcon", "tileIcon"):
                    candidate = champ.get(key)
                    if candidate and candidate != "None":
                        splash_path = candidate
                        break
        splash_url = tex_path_to_url(splash_path) if splash_path else None

        unit_traits = []
        for tname in (champ.get("traits") or []):
            tinfo = trait_by_name.get(tname)
            unit_traits.append({
                "name": tname,
                "icon_url": tex_path_to_url(tinfo["icon"]) if tinfo and tinfo.get("icon") else None,
            })

        units_display.append({
            "name": name,
            "star_level": star_level,
            "portrait_url": portrait_url,
            "item_details": item_details,
            "cost": cost,
            "ability_name": ability_name,
            "ability_desc": ability_desc,
            "initial_mana": initial_mana,
            "max_mana": max_mana,
            "splash_url": splash_url,
            "unit_traits": unit_traits,
            "splash_is_fallback": splash_is_fallback,
        })

    value = board_value(raw_units, set_number)

    companion_lookup = build_companion_lookup()
    companion_info = companion_lookup.get(companion_content_id)
    companion_icon = companion_icon_url(companion_info["loadoutsIcon"]) if companion_info else None
    companion_name = companion_info["name"] if companion_info else None

    return {
        "player_name": game_name,
        "tag_line": tag_line,
        "placement": ordinal(placement),
        "placement_color": placement_color(placement),
        "stage": stage_round(last_round),
        "damage": total_damage,
        "board_value": value,
        "rank_display": f"{rank_tier} {rank_division} ({league_points} LP)" if rank_tier else "Not tracked for this game",
        "short_date": format_short_date(game_datetime),
        "time_ago": time_ago(game_datetime),
        "units": units_display,
        "traits": traits_display,
        "companion_icon": companion_icon,
        "companion_name": companion_name,
    }