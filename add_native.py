#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["python-barcode>=0.15", "pillow>=10", "segno>=1.6"]
# ///
"""Add Klarna cards to Google Wallet as native loyalty cards (no "[TEST ONLY]").

Two ways, both driven over ADB:

  - Stores in Google's loyalty catalogue (PROGRAMS, or --program "Card=Programme"):
    Add to Wallet → Loyalty card → search → Enter manually. Wallet picks the barcode.
  - Any other card (--scan): its barcode is rendered as an image, copied to the
    phone's Pictures/klarna2google, and imported through the scanner's
    "Import from gallery". Wallet reads the barcode from the image; the card is
    only added when what Wallet read matches the card.

Afterwards run `scan_wallet.py --remove` to delete the klarna2google copies.
Start with the phone unlocked; Google Wallet must be in English.

Usage:
  uv run add_native.py [--program "Card=Programme"] [names...]
  uv run add_native.py --scan [names...]     # default: all cards not in skip.json or PROGRAMS
"""
import argparse
import json
import pathlib
import re
import sys
import tempfile
import time

from extract_klarna import adb, dump, find, label
from scan_wallet import WALLET, guard_unlocked, key, tap, type_text

PROGRAMS = {
    "Basic-Fit": "Basic-Fit N.V. Belgium",
    "C&A": "C&A Belgium",
    "JBC": "JBC Belgium",
    "Makro": "Makro Belgium",
    "Sodexo": "Sodexo Belgium",
}
PHONE_DIR = "/sdcard/Pictures/klarna2google"
NO_RESULTS = "xqzkvw"  # a search with no results shows the Scan button


def wait_for(text, tries=6):
    for _ in range(tries):
        nodes = dump()
        if find(nodes, text):
            return nodes
        time.sleep(1.5)
    sys.exit(f"Stopped: '{text}' did not appear.")


def open_catalogue():
    """Go to Wallet's loyalty programme search, from wherever Wallet is."""
    adb("shell", "monkey", "-p", WALLET, "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(3)
    for _ in range(8):
        nodes = dump()
        if find(nodes, "Search loyalty programmes") or find(nodes, "Clear"):
            return
        step = find(nodes, "Loyalty card") or find(nodes, "Add to Wallet", "View")
        if step:
            tap(step)
        else:
            key("BACK")
        time.sleep(2)
    sys.exit("Could not reach the loyalty programme search in Google Wallet.")


def search(term):
    nodes = dump()
    tap(find(nodes, "Clear") or find(nodes, "Search loyalty programmes"))
    time.sleep(1)
    type_text(term)
    time.sleep(2.5)
    return dump()


def add(name, programme, member_id):
    # The search is picky: "C&A" finds "C&A Belgium", "C&A Belgium" finds nothing,
    # while "Makro Belgi" does find "Makro Belgium".
    for term in (name, programme[:-2], programme):
        hit = find(search(term), programme, "TextView")
        if hit:
            break
    else:
        return "programme not found"
    tap(hit)
    tap(find(wait_for("Enter manually"), "Enter manually"))
    nodes = wait_for("Member ID")
    if not find(nodes, programme):
        return "wrong form opened"
    tap(find(nodes, "Member ID"))
    time.sleep(1)
    type_text(member_id)
    time.sleep(1)
    key("BACK")  # hide the keyboard so the button is reachable
    time.sleep(1)
    tap(find(dump(), "Add to Wallet"))
    time.sleep(5)
    nodes = dump()
    ok = any(label(n) == member_id for n in nodes)
    key("BACK")
    time.sleep(2)
    return "added" if ok else "not confirmed, check Wallet"


def render(card, path):
    """Write the card's barcode as a PNG, in the format Klarna uses."""
    value = card.get("barcode_value") or card["number"]
    fmt = card.get("barcode_format")
    if fmt in ("QR_CODE", "AZTEC", "DATA_MATRIX", "PDF_417"):
        import segno  # Wallet reads QR fine; other 2D formats are rare on loyalty cards
        segno.make(value, error="m").save(path, scale=14, border=4)
        return
    from barcode import EAN13, Code39, Code128
    from barcode.writer import ImageWriter
    opts = {"module_width": 0.5, "module_height": 25, "quiet_zone": 8, "font_size": 12,
            "text_distance": 5, "dpi": 300}
    if fmt == "CODE_39":
        code = Code39(value, writer=ImageWriter(), add_checksum=False)
    elif fmt == "EAN_13" and len(value) == 13:
        code = EAN13(value[:12], writer=ImageWriter())
    else:
        code = Code128(value, writer=ImageWriter())
    code.save(str(path)[:-4], opts)


def push_image(card, tmp):
    """Copy the barcode to the phone as its newest picture, so it shows first in the picker."""
    name = re.sub(r"[^a-z0-9]+", "_", card["name"].lower()).strip("_") + ".png"
    local = pathlib.Path(tmp) / name
    render(card, local)
    remote = f"{PHONE_DIR}/{name}"
    adb("shell", "mkdir", "-p", PHONE_DIR)
    adb("push", str(local), remote)
    adb("shell", "touch", remote)
    adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote}")
    time.sleep(2)
    return remote


def remove_image(remote):
    adb("shell", "rm", "-f", remote)
    adb("shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE", "-d", f"file://{remote}")


def digits(text):
    return re.sub(r"\D", "", text)


def add_scanned(card):
    name, number = card["name"], card["number"]
    value = card.get("barcode_value") or number
    tap(find(search(NO_RESULTS), "Scan", "Button"))
    tap(find(wait_for("Import from gallery"), "Import from gallery"))
    nodes = wait_for("Media grid")
    photos = [n for n in nodes if label(n).startswith("Photo taken on")]
    tap(min(photos, key=lambda n: (n["box"][1], n["box"][0])))  # newest = top left
    time.sleep(1.5)
    tap(find(dump(), "Done"))
    tap(find(wait_for("Next", tries=10), "Next", "Button"))
    tap(find(wait_for("Loyalty card", tries=10), "Loyalty card"))
    time.sleep(1)
    tap(find(dump(), "Next"))
    nodes = wait_for("Edit pass info", tries=10)

    def set_field(name_, text):
        f = next((n for n in dump() if n["cls"] == "EditText" and name_ in label(n)), None)
        if f is None:
            return False
        # The label reads "<value>, <field>[, Required]", or just the field name when empty.
        current = label(f)[:label(f).rfind(name_)].rstrip(", ")
        if current == text:
            return True
        tap(f)
        time.sleep(0.5)
        adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
        for _ in range(len(current)):
            adb("shell", "input", "keyevent", "KEYCODE_DEL")
        type_text(text)
        time.sleep(0.8)
        key("BACK")  # hide the keyboard
        time.sleep(1)
        return True

    # The AI may misread the number from the image: always use the card's own.
    # Wallet lists the programme as title and the provider as subtitle; this
    # makes the card read "<name> / Loyalty programme", like catalogue cards.
    if not (set_field("Member ID", number) and set_field("Loyalty programme", name)
            and set_field("Provider", "Loyalty programme")):
        return discard("unexpected form")
    tap(find(dump(), "Save"))
    nodes = wait_for("Add pass", tries=10)
    code = next((label(n) for n in nodes if re.match(r"(Barcode|QR code)", label(n))), None)
    if code is None:
        return discard("Wallet found no barcode in the image")
    if code.startswith("Barcode") and digits(value) and digits(code) != digits(value):
        return discard("Wallet read a different barcode")
    tap(find(nodes, "Add"))
    nodes = wait_for("Added to Wallet", tries=10)
    tap(find(nodes, "Close"))
    time.sleep(2)
    return "added"


def discard(reason):
    nodes = dump()
    button = find(nodes, "Discard") or find(nodes, "Cancel")
    if button:
        tap(button)
    time.sleep(1.5)
    key("BACK")
    return f"not added: {reason}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cards", default="klarna_cards.json")
    ap.add_argument("--program", action="append", default=[], metavar="CARD=PROGRAMME")
    ap.add_argument("--scan", action="store_true", help="import the barcode as an image instead")
    ap.add_argument("--skip", default="skip.json", help="cards already in Wallet (from scan_wallet.py)")
    ap.add_argument("names", nargs="*", help="cards to add (default: all with a known programme)")
    args = ap.parse_args()

    programs = dict(PROGRAMS, **dict(p.split("=", 1) for p in args.program))
    cards = {c["name"]: c for c in json.load(open(args.cards))}
    skip = set(json.load(open(args.skip))) if pathlib.Path(args.skip).exists() else set()
    if args.names:
        names = args.names
    elif args.scan:
        names = [n for n in cards if n not in skip and n not in programs]
    else:
        names = [n for n in programs if n in cards and n not in skip]

    stay_on = adb("shell", "settings", "get", "global", "stay_on_while_plugged_in").strip()
    adb("shell", "svc", "power", "stayon", "usb")
    try:
        guard_unlocked()
        for name in names:
            card = cards[name]
            open_catalogue()
            if args.scan:
                with tempfile.TemporaryDirectory() as tmp:
                    remote = push_image(card, tmp)
                    try:
                        result = add_scanned(card)
                    finally:
                        remove_image(remote)
                print(f"  {name:<18} scanned: {result}", flush=True)
            else:
                result = add(name, programs[name], card.get("barcode_value") or card["number"])
                print(f"  {name:<12} → {programs[name]}: {result}", flush=True)
    finally:
        adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", stay_on or "0")
    print("\nNow run `python3 scan_wallet.py --remove` to delete the klarna2google copies.")


if __name__ == "__main__":
    main()
