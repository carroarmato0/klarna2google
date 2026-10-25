#!/usr/bin/env python3
"""Find Klarna cards that already exist in Google Wallet, so they are not imported twice.

The Wallet API only sees passes of your own issuer, so the Wallet app is read
over ADB instead. Every pass that wasn't created by klarna2google is opened and
its card number compared with klarna_cards.json. Cards already in Wallet win:
matches are written to skip.json, which import_to_wallet.py leaves out.

  - number match (equal, or one ends with the other): skipped automatically
  - name match with a different number: only reported, it may be another card

With --remove, klarna2google passes of skipped cards are also removed from
Wallet. Start with the phone unlocked; Google Wallet must be in English.

Usage: python3 scan_wallet.py [--cards klarna_cards.json] [--skip skip.json] [--remove]
"""
import argparse
import json
import pathlib
import re
import sys
import time

from adb_ui import adb, dump, find, label

WALLET = "com.google.android.apps.walletnfcrel"
ALLOWED = (WALLET, "com.google.android.gms",  # gms hosts some Wallet screens
           "com.google.android.photopicker")  # used to import a barcode image
ROW_X = 275  # x of the title/subtitle column in Wallet pass lists
MIN_SUFFIX = 6


def foreground():
    m = re.search(r"mCurrentFocus=Window\{\S+ \S+ ([^/}\s]+)", adb("shell", "dumpsys", "window"))
    return m.group(1) if m else "?"


def guard():
    """Never send input to another app (or the lock screen) by accident."""
    app = foreground()
    if app not in ALLOWED:
        sys.exit(f"Stopped: {app} is in front instead of Google Wallet. Unlock the phone and run again.")


def tap(n):
    guard()
    x1, y1, x2, y2 = n["box"]
    adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))


def key(code):
    guard()
    adb("shell", "input", "keyevent", code)


def type_text(text):
    """adb input text: spaces as %s, shell metacharacters escaped."""
    guard()
    adb("shell", "input", "text", "".join("%s" if ch == " " else ("\\" + ch if ch in "&'()<>;|*$\"" else ch)
                                          for ch in text))


def swipe_up():
    guard()
    adb("shell", "input", "swipe", "540", "1900", "540", "900", "500")


def rows(nodes):
    """(title, subtitle, node) for each pass in a Wallet list."""
    tv = sorted((n for n in nodes if n["cls"] == "TextView" and n["box"][0] == ROW_X and label(n)),
                key=lambda n: n["box"][1])
    return [(label(a), label(b), a) for a, b in zip(tv, tv[1:]) if 50 < b["box"][1] - a["box"][1] < 80]


def open_pass_list():
    adb("shell", "monkey", "-p", WALLET, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
    for _ in range(8):
        nodes = dump()
        more = find(nodes, "View more passes")
        if more:
            tap(more)
            time.sleep(2.5)
            return
        more = find(nodes, "View more")
        if more and more["box"][1] > 1500:  # the "Passes" section on the Wallet home screen
            tap(more)
        else:
            key("BACK")
        time.sleep(2.5)
    sys.exit("Could not find the passes list in Google Wallet.")


def list_passes():
    seen, idle = [], 0
    while idle < 2:
        new = [(t, s) for t, s, _ in rows(dump()) if (t, s) not in seen]
        seen += new
        idle = 0 if new else idle + 1
        swipe_up()
        time.sleep(1.2)
    return seen


def search(query):
    for _ in range(3):
        nodes = dump()
        box = find(nodes, "Clear search") or find(nodes, "Search")
        if box:
            break
        key("BACK")
        time.sleep(1.8)
    else:
        sys.exit("Lost the Wallet search field.")
    tap(box)
    time.sleep(1.2)
    type_text(query)
    key("ENTER")
    time.sleep(2.5)
    return rows(dump())


def open_pass(title, subtitle, index=0):
    """Open the index-th pass titled title/subtitle; None if there is no such pass."""
    # Wallet's search misses some full titles ("Basic-Fit N.V."); the first word still finds them.
    for query in dict.fromkeys((title, re.split(r"[\s\-.&']", title)[0])):
        hits = [n for t, s, n in search(query) if (t, s) == (title, subtitle)]
        if hits:
            break
    else:
        return None
    if index >= len(hits):
        return None
    tap(hits[index])
    time.sleep(3)
    return dump()


def read_number(title, subtitle):
    nodes = open_pass(title, subtitle)
    if nodes is None:
        return None
    number = None
    for n in nodes:
        m = re.fullmatch(r"Card (?:number|value) (.+)", label(n))
        if m:
            number = m.group(1)
            break
    else:
        # Some passes only show the number as plain text, e.g. under "Member ID".
        texts = [label(n) for n in nodes if n["cls"] == "TextView" and label(n)]
        for i, text in enumerate(texts[:-1]):
            if text in ("Member ID", "Card number", "Account number", "Membership number"):
                number = texts[i + 1]
                break
    key("BACK")
    time.sleep(1.8)
    return number


def remove_pass(name):
    """Remove our pass for `name`, recognised by its "[TEST ONLY]" title.

    A native card can look the same in the list ("Ivago / Ivago"), so every
    pass with this title is opened until ours is found."""
    for index in range(5):
        nodes = open_pass(name, name, index)
        if nodes is None:
            return "not in Wallet"
        if find(nodes, f"[TEST ONLY] {name}"):
            break
        key("BACK")
        time.sleep(1.8)
    else:
        return "skipped: none of the passes is ours"
    for _ in range(3):
        if find(dump(), "Remove"):
            break
        swipe_up()
        time.sleep(1.2)
    tap(find(dump(), "Remove"))
    time.sleep(2)
    nodes = dump()
    confirm = find(nodes, "Remove", "Button")
    if not (confirm and find(nodes, "Remove pass?")):
        return "failed: no confirmation dialog"
    tap(confirm)
    time.sleep(2.5)
    return "removed"


def norm(text):
    return re.sub(r"[^0-9A-Za-z]", "", text or "").upper()


def same_number(a, b):
    a, b = norm(a), norm(b)
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return a == b or (len(short) >= MIN_SUFFIX and long_.endswith(short))


def same_name(a, b):
    """All words of one name occur in the other: 'Hubo' ~ 'Hubo Belgium'."""
    a, b = (set(re.findall(r"[0-9a-z]{2,}", x.lower())) for x in (a, b))
    return bool(a and b) and (a <= b or b <= a)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cards", default="klarna_cards.json")
    ap.add_argument("--skip", default="skip.json", help="skip list to update")
    ap.add_argument("--remove", action="store_true", help="also remove our passes of skipped cards from Wallet")
    args = ap.parse_args()

    cards = json.load(open(args.cards))
    names = {c["name"] for c in cards}
    stay_on = adb("shell", "settings", "get", "global", "stay_on_while_plugged_in").strip()
    adb("shell", "svc", "power", "stayon", "usb")
    try:
        guard_unlocked()
        open_pass_list()
        passes = list_passes()
        # Our generic passes repeat the card name as subtitle.
        ours = {t for t, s in passes if t == s and t in names}
        native = [(t, s) for t, s in passes if not (t == s and t in names)]
        print(f"{len(passes)} passes in Wallet, {len(ours)} from klarna2google; reading {len(native)} others…",
              file=sys.stderr)
        found = []
        for t, s in native:
            number = read_number(t, s)
            found.append({"title": t, "subtitle": s, "number": number})
            print(f"  {t} ({s}): {number or '-'}", file=sys.stderr)

        skip, maybe = {}, []
        for c in cards:
            for f in found:
                if same_number(c["number"], f["number"]):
                    skip[c["name"]] = f
                    break
            else:
                maybe += [(c, f) for f in found if f["number"] and same_name(c["name"], f["title"])]

        path = pathlib.Path(args.skip)
        existing = json.loads(path.read_text()) if path.exists() else []
        merged = sorted(set(existing) | set(skip))
        path.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")

        print(f"\nAlready in Wallet, will be skipped ({len(skip)}):")
        for name, f in skip.items():
            print(f"  {name:<20} = {f['title']} ({f['number']})")
        if maybe:
            print("\nSame name, different number (not skipped, check yourself):")
            for c, f in maybe:
                print(f"  {c['name']:<20} {c['number']}  vs  {f['title']} {f['number']}")
        print(f"\n{path}: {len(merged)} cards")

        if args.remove:
            todo = [n for n in merged if n in ours]
            print(f"\nRemoving {len(todo)} duplicate klarna2google passes from Wallet:")
            for name in todo:
                print(f"  {name}: {remove_pass(name)}", flush=True)
        elif any(n in ours for n in merged):
            print("Some skipped cards were already imported; run again with --remove to delete those passes.")
    finally:
        adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", stay_on or "0")


def guard_unlocked():
    if "isKeyguardShowing=true" in adb("shell", "dumpsys", "window"):
        sys.exit("Unlock the phone first.")


if __name__ == "__main__":
    main()
