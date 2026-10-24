#!/usr/bin/env python3
"""Extract loyalty cards from the Klarna Android app via ADB UI automation.

Klarna blocks screenshots (FLAG_SECURE) and backups, so we read the
accessibility tree with uiautomator. Start with the phone unlocked and the
Klarna "Loyalty Cards" screen open.

Usage: python3 extract_klarna.py [output.json]
"""
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter


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


def read_card(button):
    """Open a card from the list, read its raw number and nickname, return to the list."""
    tap(button)
    _, edit = wait_for("Edit card", "Button")
    tap(edit)
    nodes, _ = wait_for("Save", "Button")
    fields = [n for n in nodes if n["cls"] == "EditText"]
    number = fields[0]["text"] if fields else ""
    nickname = fields[1]["text"] if len(fields) > 1 else ""
    if nickname == "Nickname (optional)":
        nickname = ""
    adb("shell", "input", "keyevent", "BACK")  # close edit sheet (may only hide keyboard)
    time.sleep(0.6)
    if find(dump(), "Save", "Button"):
        adb("shell", "input", "keyevent", "BACK")
        time.sleep(0.6)
    adb("shell", "input", "keyevent", "BACK")  # back to list
    wait_for("Loyalty Cards")
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
        and label(n) not in ("Sort by", "Add loyalty card", "Back", "Add")
    )


def clean_name(name):
    """Logo-less cards are labelled 'WK, World Karts': drop the initials."""
    m = re.fullmatch(r"(\S{1,3}), (.+)", name)
    return m.group(2) if m else name


def list_end(nodes):
    """Top of the 'Offers' section (or bottom of the screen) ends the card grid."""
    offers = find(nodes, "Offers")
    return offers["box"][1] if offers else 10**6


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "klarna_cards.json"
    nodes, _ = wait_for("Loyalty Cards")
    header = next(n for n in nodes if re.fullmatch(r"\d+ loyalty cards?", label(n)))
    expected = int(label(header).split()[0])
    grid_top = header["box"][3]
    search = find(nodes, "Search", "EditText")
    view_top = (search["box"][3] if search else 400) + 20
    w, h = map(int, re.findall(r"\d+", adb("shell", "wm", "size").splitlines()[-1]))
    view_bottom = h - 60

    cards, visited, offset = [], set(), 0
    while True:
        end = list_end(nodes)
        for n in [n for n in nodes if is_card_button(n, grid_top, offset, view_top, view_bottom, end)]:
            key = (label(n), n["box"][0], round((n["box"][1] + offset) / 40))
            if key in visited:
                continue
            visited.add(key)
            number, nickname = read_card(n)
            card = {"name": clean_name(label(n)), "number": number, "nickname": nickname}
            if number and not any(c["number"] == number and c["name"] == card["name"] for c in cards):
                cards.append(card)
                print(f"[{len(cards)}/{expected}] {card['name']}: {number}", file=sys.stderr)
            nodes = dump()  # positions may be stale after navigating
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
