"""Learned spells and the color pairs they can safely use."""

from collections import defaultdict
from itertools import combinations
import re

from companion import linux_chord


def validate_spells(spells):
    if not isinstance(spells, dict):
        raise ValueError("Invalid learned spells in the profile.")
    for spell in spells.values():
        if (not isinstance(spell, dict)
                or not isinstance(spell.get("name"), str) or not spell["name"].isprintable()
                or not isinstance(spell.get("key"), str)
                or not isinstance(spell.get("colors"), list) or not spell["colors"]
                or not all(isinstance(color, str) and re.fullmatch(r"[0-9a-f]{6}:[0-9a-f]{6}", color)
                           for color in spell["colors"])):
            raise ValueError("Invalid learned spell in the profile.")


def color_owners(spells):
    owners = defaultdict(set)
    for action, spell in spells.items():
        for color in spell["colors"]:
            owners[color].add(action)
    return owners


def collision_grid(points, bounds, width, height):
    """Choose interior pixels, trying the existing X11 strip first."""
    (x, top), (_, bottom) = points
    x0, y0, x1, y1 = bounds
    vertical = [(x, top + (bottom - top) * fraction // 4) for fraction in (2, 1, 3)]
    columns = [int((x0 + (x1 - x0) * fraction / 8) * width / 65535)
               for fraction in (2, 3, 4, 5, 6)]
    rows = [int((y0 + (y1 - y0) * fraction / 8) * height / 65535)
            for fraction in (2, 3, 4, 5, 6)]
    base = {tuple(point) for point in points}
    return tuple(point for point in dict.fromkeys([*vertical, *((x, y) for x in columns for y in rows)])
                 if point not in base and x0 < point[0] * 65535 / width < x1
                 and y0 < point[1] * 65535 / height < y1)


def pick_refinement(observations, grid, points):
    """Prefer full separation, then a shared strip, then fewer samples."""
    (x, top), (_, bottom) = points
    best_points, best_matches, best_score = [], {}, (0, 0, 0)
    for count in (1, 2):
        for indices in combinations(range(len(grid)), count):
            lookup = defaultdict(set)
            extras = [grid[i] for i in indices]
            signatures = [":".join(colors[i] for i in indices) for _, colors in observations]
            for (action, _), signature in zip(observations, signatures):
                lookup[signature].add(action)
            matches = {signature: next(iter(actions)) for signature, actions in lookup.items()
                       if len(actions) == 1}
            in_strip = all(px == x and top <= py <= bottom for px, py in extras)
            score = (sum(signature in matches for signature in signatures), in_strip, -count)
            if matches and score > best_score:
                best_points, best_matches, best_score = extras, matches, score
    return best_points, best_matches


def learn_collisions(previous, spells, samples, grid):
    owners = color_owners(spells)
    old = previous.get("collisions", {})
    observations = defaultdict(list)
    refreshed = set()
    for entry, color, colors in samples:
        action = str(entry.action)
        refreshed.add(action)
        observations[color].append((action, colors))

    collisions = {}
    for color in old.keys() | {color for color, actions in owners.items() if len(actions) > 1}:
        actions = owners.get(color, set())
        prior = old.get(color)
        if len(actions) < 2:
            # Forgetting an entry alone doesn't establish that its lookalike is gone in game.
            if prior and not set(prior["owners"]) <= refreshed:
                collisions[color] = prior
            continue
        observed = observations[color]
        if {action for action, _ in observed} == actions:
            points, matches = pick_refinement(observed, grid, previous["points"])
        elif prior and set(prior["owners"]) == actions and not actions & refreshed:
            collisions[color] = prior
            continue
        else:
            points, matches = [], {}
        collisions[color] = {"owners": sorted(actions), "points": [list(point) for point in points],
                             "matches": matches}
    return collisions


def resolved_collisions(profile, owners=None):
    if owners is None:
        owners = color_owners(profile.get("spells", {}))
    return {color: rule for color, rule in profile.get("collisions", {}).items()
            if rule["matches"] and set(rule["owners"]) == owners.get(color)}


def validate_collisions(collisions, width, height, base_points):
    if not isinstance(collisions, dict):
        raise ValueError("Invalid icon collisions in the profile.")
    for color, rule in collisions.items():
        if not (re.fullmatch(r"[0-9a-f]{6}:[0-9a-f]{6}", color) and isinstance(rule, dict)
                and isinstance(rule.get("owners"), list) and len(rule["owners"]) >= 2
                and all(isinstance(action, str) for action in rule["owners"])
                and len(set(rule["owners"])) == len(rule["owners"])
                and isinstance(rule.get("points"), list) and len(rule["points"]) <= 2
                and isinstance(rule.get("matches"), dict)):
            raise ValueError("Invalid icon collision in the profile.")
        points, matches = rule["points"], rule["matches"]
        if (bool(points) != bool(matches)
                or not all(isinstance(point, list) and len(point) == 2
                           and all(type(value) is int for value in point)
                           and 0 <= point[0] < width and 0 <= point[1] < height
                           and point not in base_points for point in points)
                or len({tuple(point) for point in points}) != len(points)
                or not all(action in rule["owners"] and len(extra.split(":")) == len(points)
                           and all(re.fullmatch(r"[0-9a-f]{6}", part) for part in extra.split(":"))
                           for extra, action in matches.items())):
            raise ValueError("Invalid icon collision in the profile.")


def active_bindings(profile):
    spells = profile.get("spells", {})
    bindings = profile["bindings"].copy()
    owners = color_owners(spells)
    collisions = profile.get("collisions", {})
    for color in owners.keys() | collisions.keys():
        bindings.pop(color, None)
        if color not in collisions and len(owners.get(color, ())) == 1:
            key = spells[next(iter(owners[color]))]["key"]
            if key:
                bindings[color] = key
    for color, rule in resolved_collisions(profile, owners).items():
        for extra, action in rule["matches"].items():
            if key := spells[action]["key"]:
                bindings[f"{color}:{extra}"] = key
    return bindings


def merge_batch(profile, samples, grid, validate_key, *, refresh_keys=False):
    """Refresh samples and optionally replace saved keys with verified Hekili bindings."""
    captured = {}
    suggestions = defaultdict(set)
    for entry, color, _ in samples:
        action = str(entry.action)
        spell = captured.setdefault(action, {"name": entry.name, "key": "", "colors": []})
        if color not in spell["colors"]:
            spell["colors"].append(color)
        if chord := linux_chord(entry.key):
            try:
                validate_key(chord)
            except ValueError:
                continue
            suggestions[action].add(chord)

    previous = profile.get("spells", {})
    spells = {**previous, **captured}
    bindings = profile["bindings"].copy()
    owners = color_owners(spells)
    for action, spell in captured.items():
        legacy_keys = {bindings[color] for color in spell["colors"]
                       if color in bindings and len(owners[color]) == 1}
        if refresh_keys:
            # A missing, unsupported or ambiguous current binding must disable
            # the spell rather than leave an outdated key active.
            spell["key"] = next(iter(suggestions[action])) if len(suggestions[action]) == 1 else ""
        elif action in previous:
            spell["key"] = previous[action]["key"]
        elif len(legacy_keys) == 1:
            spell["key"] = legacy_keys.pop()
        elif not legacy_keys and len(suggestions[action]) == 1:
            spell["key"] = suggestions[action].pop()

        # Claimed colors now live in the catalog, even if shared or unbound.
        for color in spell["colors"]:
            bindings.pop(color, None)

    collisions = learn_collisions(profile, spells, samples, grid)
    return {**profile, "bindings": bindings, "spells": spells, "collisions": collisions}, captured
