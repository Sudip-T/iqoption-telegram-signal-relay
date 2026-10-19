"""
Find duplicate ids in UNDERLYING_ASSESTS.

Several display names in options_assests.py map to the same numeric id
(e.g. "XAUUSD-op" and "XAUUSD" both point to 1912). That's expected —
it's just alternate names for the same underlying instrument — but it's
also the root cause of the symbol-collision bug we ran into with
`id_to_name = {v: k for k, v in UNDERLYING_ASSESTS.items()}`: doing a
one-directional reverse lookup silently drops every name but the last
one written for each duplicated id, so subscription-confirmation checks
tagged with a "lost" name (e.g. "MORSTAN-OTC") never resolve.

Drop this file next to options_assests.py and run it directly.
"""
from collections import defaultdict
from typing import Dict, List


def find_duplicate_ids(mapping: Dict[str, int]) -> Dict[int, List[str]]:
    """
    Given a dict of {name: id}, return only the ids shared by more than
    one name, mapped to the full list of names that share them.

    Example:
        {"XAUUSD-op": 1912, "XAUUSD": 1912, "ETHUSD": 1918}
        -> {1912: ["XAUUSD-op", "XAUUSD"]}
    """
    id_to_names: Dict[int, List[str]] = defaultdict(list)
    for name, asset_id in mapping.items():
        id_to_names[asset_id].append(name)

    return {
        asset_id: names
        for asset_id, names in id_to_names.items()
        if len(names) > 1
    }


if __name__ == "__main__":
    from options_assests import UNDERLYING_ASSESTS

    duplicates = find_duplicate_ids(UNDERLYING_ASSESTS)

    print(f"Found {len(duplicates)} duplicate id(s) out of {len(UNDERLYING_ASSESTS)} entries:\n")
    for asset_id, names in sorted(duplicates.items()):
        print(f"  {asset_id}: {names}")