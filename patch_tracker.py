import os
import json
import hashlib
import requests
from datetime import datetime

SNAPSHOT_DIR = "trait_snapshots"
TRAITS_URL = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tfttraits.json"


def _fetch_traits(set_name: str = "TFTSet18") -> list:
    response = requests.get(TRAITS_URL)
    response.raise_for_status()
    return [t for t in response.json() if t.get("set") == set_name]


def _content_hash(traits: list) -> str:
    """
    A stable fingerprint of the data. sort_keys makes it insensitive to
    key ordering, so we only get a new hash when values actually change.
    """
    canonical = json.dumps(traits, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def list_snapshots() -> list:
    """Snapshot file paths, oldest first (filenames sort chronologically)."""
    if not os.path.exists(SNAPSHOT_DIR):
        return []
    files = [f for f in os.listdir(SNAPSHOT_DIR) if f.endswith(".json")]
    return [os.path.join(SNAPSHOT_DIR, f) for f in sorted(files)]


def take_snapshot(set_name: str = "TFTSet18") -> str:
    """
    Fetches current trait data and saves it if it differs from the most
    recent snapshot. Returns a human-readable status message.
    """
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    traits = _fetch_traits(set_name)
    new_hash = _content_hash(traits)

    existing = list_snapshots()
    if existing:
        latest = existing[-1]
        with open(latest, "r", encoding="utf-8") as f:
            if _content_hash(json.load(f)) == new_hash:
                return f"No change since {os.path.basename(latest)} — nothing saved."

    filename = f"{datetime.now().strftime('%Y-%m-%d')}_{new_hash}.json"
    path = os.path.join(SNAPSHOT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(traits, f, indent=2)
    return f"Saved new snapshot: {filename}"


def flatten_traits(traits: list) -> dict:
    """
    Turns nested trait data into a flat {path: value} dict so two versions
    can be compared with simple dictionary operations.

    Breakpoints are keyed by index, not by min_units, deliberately: if a
    breakpoint moves from 5 units to 4, we want that reported as a change
    to that breakpoint, not as one breakpoint vanishing and another appearing.
    """
    flat = {}
    for t in traits:
        tid = t["trait_id"]
        flat[f"{tid}::name"] = t.get("display_name")
        flat[f"{tid}::tooltip"] = t.get("tooltip_text")

        for i, group in enumerate(t.get("innate_trait_sets", [])):
            for c in group.get("constants", []):
                flat[f"{tid}::innate{i}::{c['name']}"] = c["value"]

        for i, bp in enumerate(t.get("conditional_trait_sets", [])):
            flat[f"{tid}::bp{i}::min_units"] = bp.get("min_units")
            flat[f"{tid}::bp{i}::max_units"] = bp.get("max_units")
            flat[f"{tid}::bp{i}::style"] = bp.get("style_name")
            for c in bp.get("constants", []):
                flat[f"{tid}::bp{i}::{c['name']}"] = c["value"]

    return flat


def diff_snapshots(old_path: str, new_path: str) -> dict:
    """
    Compares two snapshot files. Returns changes grouped by trait_id.
    """
    with open(old_path, "r", encoding="utf-8") as f:
        old_flat = flatten_traits(json.load(f))
    with open(new_path, "r", encoding="utf-8") as f:
        new_flat = flatten_traits(json.load(f))

    changes = []
    for key in sorted(set(old_flat) | set(new_flat)):
        before = old_flat.get(key)
        after = new_flat.get(key)
        if before == after:
            continue
        trait_id, _, field = key.partition("::")
        changes.append({
            "trait_id": trait_id,
            "field": field,
            "before": before,
            "after": after,
        })

    grouped = {}
    for c in changes:
        grouped.setdefault(c["trait_id"], []).append(c)
    return grouped


def latest_diff() -> tuple:
    """
    Diffs the two most recent snapshots.
    Returns (grouped_changes, old_name, new_name), or (None, None, None).
    """
    snaps = list_snapshots()
    if len(snaps) < 2:
        return None, None, None
    old, new = snaps[-2], snaps[-1]
    return diff_snapshots(old, new), os.path.basename(old), os.path.basename(new)