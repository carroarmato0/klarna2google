#!/usr/bin/env python3
"""Extract loyalty cards from the Klarna Android app via ADB UI automation.

Klarna blocks screenshots (FLAG_SECURE) and backups, so we read the
accessibility tree with uiautomator. Start with the phone unlocked and the
Klarna "Loyalty Cards" screen open. Works with Klarna set to English, Dutch or
French (Profile -> Language).

Usage: python3 extract_klarna.py [output.json]
"""
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter

# Klarna UI labels per app language; the list title identifies the language.
LANGUAGES = {
    "en": {
        "title": "Loyalty Cards", "count": r"(\d+) loyalty cards?", "search": "Search",
        "edit": "Edit card", "save": "Save", "offers": "Offers",
        "not_cards": ("Sort by", "Add loyalty card", "Back", "Add"),
    },
    "nl": {
        "title": "Klantenkaarten", "count": r"(\d+) klantenkaarten?", "search": "Zoeken",
        "edit": "Kaart bewerken", "save": "Opslaan", "offers": "Aanbiedingen",
        "not_cards": ("Sorteer op", "Klantenkaart toevoegen", "Terug", "Toevoegen"),
    },
    "fr": {
        "title": "Cartes de fidélité", "count": r"(\d+) cartes? de fidélité", "search": "Rechercher",
        "edit": "Modifier la carte", "save": "Enregistrer", "offers": "Offres",
        "not_cards": ("Trier par", "Ajouter une carte de fidélité", "Retour", "Ajouter"),
    },
}
L = LANGUAGES["en"]  # set by detect_language()


def adb(*args):
    return subprocess.run(["adb", *args], check=True, capture_output=True, text=True).stdout


def dump():
    adb("shell", "uiautomator", "dump", "/sdcard/k2g.xml")
    xml = adb("exec-out", "cat", "/sdcard/k2g.xml")
    nodes = []
    for n in ET.fromstring(xml).iter("node"):
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", n.get("bounds")))
        nodes.append({
            "text": n.get("text") or "",
            "desc": n.get("content-desc") or "",
            "cls": n.get("class", "").rsplit(".", 1)[-1],
            "click": n.get("clickable") == "true",
            "box": (x1, y1, x2, y2),
        })
    return nodes


def label(n):
    return n["text"] or n["desc"]


def find(nodes, value, cls=None):
    for n in nodes:
        if label(n) == value and (cls is None or n["cls"] == cls):
            return n
    return None


def tap(n):
    x1, y1, x2, y2 = n["box"]
    adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))


def wait_for(value, cls=None, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        nodes = dump()
        n = find(nodes, value, cls)
        if n:
            return nodes, n
        time.sleep(0.4)
    raise RuntimeError(f"timed out waiting for {value!r}")


def detect_language():
    global L
    titles = {lang["title"]: lang for lang in LANGUAGES.values()}
    end = time.time() + 8
    while time.time() < end:
        nodes = dump()
        for n in nodes:
            if label(n) in titles:
                L = titles[label(n)]
                return nodes
        time.sleep(0.4)
    sys.exit("Open the Loyalty Cards screen in Klarna first (app language English, Dutch or French).")


def field_value(nodes, field):
    """An empty field shows its own label as text; Android reports it as the
    label of the container that wraps the EditText."""
    x1, y1, x2, y2 = field["box"]
    for n in nodes:
        bx1, by1, bx2, by2 = n["box"]
        if n is not field and n["cls"] == "ViewGroup" and label(n) == field["text"] \
                and bx1 <= x1 and by1 <= y1 and bx2 >= x2 and by2 >= y2:
            return ""
    return field["text"]


def read_card(button, name, verify=True):
    """Open a card from the list, read its raw number and nickname, return to the list.

    The second field is the nickname for cards of known merchants and the store
    name for custom cards; the latter equals the card name and is dropped."""
    tap(button)
    nodes, edit = wait_for(L["edit"], "Button")
    title_ok = any(label(n) == name and n["box"][3] < edit["box"][1] for n in nodes)
    if verify and not title_ok:  # the list moved under our tap and another card opened
        adb("shell", "input", "keyevent", "BACK")
        wait_for(L["title"])
        return None
    tap(edit)
    nodes, _ = wait_for(L["save"], "Button")
    fields = [n for n in nodes if n["cls"] == "EditText"]
    number = field_value(nodes, fields[0]) if fields else ""
    nickname = field_value(nodes, fields[1]) if len(fields) > 1 else ""
    if nickname == name:
        nickname = ""
    adb("shell", "input", "keyevent", "BACK")  # close edit sheet (may only hide keyboard)
    time.sleep(0.6)
    if find(dump(), L["save"], "Button"):
        adb("shell", "input", "keyevent", "BACK")
        time.sleep(0.6)
    adb("shell", "input", "keyevent", "BACK")  # back to list
    wait_for(L["title"])
    return number, nickname


def scroll_shift(before, after):
    """Vertical shift between two dumps, from card buttons matching by label and x."""
    deltas = Counter()
    for a in before:
        for b in after:
            if a["cls"] == b["cls"] and label(a) and label(a) == label(b) and a["box"][0] == b["box"][0]:
                deltas[a["box"][1] - b["box"][1]] += 1
    return deltas.most_common(1)[0][0] if deltas else None


def is_card_button(n, grid_top, offset, view_top, view_bottom, grid_end):
    x1, y1, x2, y2 = n["box"]
    return (
        y1 < grid_end and
        n["cls"] == "Button" and n["click"] and label(n)
        and y1 + offset > grid_top
        and y1 >= view_top and y2 <= view_bottom
        and (y2 - y1) > 200  # fully visible card tile
        and label(n) not in L["not_cards"]
    )


def clean_name(name):
    """Logo-less cards are labelled 'WK, World Karts': drop the initials."""
    m = re.fullmatch(r"(\S{1,3}), (.+)", name)
    return m.group(2) if m else name


def list_end(nodes):
    """Top of the 'Offers' section (or bottom of the screen) ends the card grid."""
    offers = find(nodes, L["offers"])
    return offers["box"][1] if offers else 10**6


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "klarna_cards.json"
    nodes = detect_language()
    header = next(n for n in nodes if re.fullmatch(L["count"], label(n)))
    expected = int(label(header).split()[0])
    grid_top = header["box"][3]
    search = find(nodes, L["search"], "EditText")
    view_top = (search["box"][3] if search else 400) + 20
    w, h = map(int, re.findall(r"\d+", adb("shell", "wm", "size").splitlines()[-1]))
    view_bottom = h - 60

    cards, visited, offset, retries = [], set(), 0, 0
    while True:
        # Re-read the screen after every card: opening one can reorder the list
        # ("Frequently used"), so earlier positions are not reliable.
        end = list_end(nodes)
        todo = [n for n in nodes if is_card_button(n, grid_top, offset, view_top, view_bottom, end)
                and (label(n), n["box"][0], round((n["box"][1] + offset) / 40)) not in visited]
        if todo:
            n = todo[0]
            name = clean_name(label(n))
            # After two title mismatches, trust the tap: a nickname may be shown as title.
            result = read_card(n, name, verify=retries < 2)
            nodes = dump()
            if result is None:
                retries += 1
                continue
            retries = 0
            visited.add((label(n), n["box"][0], round((n["box"][1] + offset) / 40)))
            number, nickname = result
            card = {"name": name, "number": number, "nickname": nickname}
            if number and not any(c["number"] == number and c["name"] == card["name"] for c in cards):
                cards.append(card)
                print(f"[{len(cards)}/{expected}] {card['name']}: {number}", file=sys.stderr)
            continue
        if end < 10**6:
            break  # reached the end of the card grid
        before = dump()
        adb("shell", "input", "swipe", str(w // 2), str(view_bottom - 300), str(w // 2), str(view_top + 300), "1500")
        time.sleep(1)
        nodes = dump()
        shift = scroll_shift(before, nodes)
        if not shift:
            break
        offset += shift

    with open(out, "w") as f:
        json.dump(cards, f, indent=2, ensure_ascii=False)
    print(f"Extracted {len(cards)} of {expected} cards -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
