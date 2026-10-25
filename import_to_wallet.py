#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["google-auth>=2.30", "requests>=2.31"]
# ///
"""Import extracted loyalty cards into Google Wallet as Generic passes.

Creates (or updates) one pass class and one pass object per card through the
Google Wallet REST API, then writes an HTML page with "Add to Google Wallet"
links: one link that saves every card at once, plus one link per card.

Usage:
  uv run import_to_wallet.py --key service-account.json --issuer-id 3388000000012345678
  uv run import_to_wallet.py --key ... --issuer-id ... CRU "AB Race"   # only these cards
  uv run import_to_wallet.py --dry-run          # print the passes, touch nothing
"""
import argparse
import hashlib
import html
import json
import pathlib
import re
import sys
import time

API = "https://walletobjects.googleapis.com/walletobjects/v1"
SCOPE = "https://www.googleapis.com/auth/wallet_object.issuer"
CLASS_SUFFIX = "klarna2google"
SAVE_URL = "https://pay.google.com/gp/v/save/"
LOGO_BASE = "https://raw.githubusercontent.com/carroarmato0/klarna2google/main/logos/"
LOGOS = pathlib.Path(__file__).resolve().parent / "logos"


def ean_checksum_ok(digits):
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == check


# Klarna barcode formats Google Wallet can render as-is.
WALLET_FORMATS = {"AZTEC", "CODABAR", "CODE_39", "CODE_128", "DATA_MATRIX", "EAN_8", "EAN_13",
                  "PDF_417", "QR_CODE", "UPC_A"}


def wallet_barcode(klarna_format, value):
    """Map a Klarna barcode format to a Wallet BarcodeType, or None if unknown.

    Wallet only knows ITF_14; other ITF lengths fall back to CODE_128, which
    most retail scanners read as well."""
    if klarna_format in WALLET_FORMATS:
        return klarna_format
    if klarna_format == "ITF":
        if len(value) == 14 and value.isdigit():
            return "ITF_14"
        print(f"warning: {len(value)}-digit ITF barcode can't be shown in Wallet, using CODE_128", file=sys.stderr)
        return "CODE_128"
    return None


def guess_barcode(number):
    """Infer the symbology from the value when the format is unknown."""
    if number.startswith(("http://", "https://")) or not number.isascii() or len(number) > 40:
        return "QR_CODE"
    if number.isdigit():
        if len(number) == 13 and ean_checksum_ok(number):
            return "EAN_13"
        if len(number) == 8 and ean_checksum_ok(number):
            return "EAN_8"
        if len(number) == 12 and ean_checksum_ok(number):
            return "UPC_A"
    return "CODE_128"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "card"


def colour(name):
    """Stable, reasonably dark background colour per merchant."""
    h = hashlib.sha1(name.encode()).digest()
    return "#%02x%02x%02x" % tuple(40 + b % 120 for b in h[:3])


def build_object(issuer_id, card, formats, logo_base=LOGO_BASE):
    name = card["name"]
    number = card["number"]
    value = card.get("barcode_value") or number
    barcode_type = (formats.get(name) or wallet_barcode(card.get("barcode_format"), value)
                    or guess_barcode(value))
    obj = {
        "id": f"{issuer_id}.k2g_{slug(name)}_{hashlib.sha1(number.encode()).hexdigest()[:8]}",
        "classId": f"{issuer_id}.{CLASS_SUFFIX}",
        "state": "ACTIVE",
        "cardTitle": {"defaultValue": {"language": "en", "value": name}},
        "header": {"defaultValue": {"language": "en", "value": card.get("nickname") or name}},
        "hexBackgroundColor": colour(name),
        "barcode": {"type": barcode_type, "value": value},
    }
    logo = LOGOS / f"{slug(name)}.png"
    if logo_base and logo.exists():
        # Wallet caches images by URL; a content hash makes updated logos show up.
        version = hashlib.sha1(logo.read_bytes()).hexdigest()[:8]
        obj["logo"] = {
            "sourceUri": {"uri": f"{logo_base}{slug(name)}.png?v={version}"},
            "contentDescription": {"defaultValue": {"language": "en", "value": f"{name} logo"}},
        }
    if barcode_type != "QR_CODE":
        obj["barcode"]["alternateText"] = number
    else:
        obj["subheader"] = {"defaultValue": {"language": "en", "value": "Scan QR"}}
    return obj


def upsert(session, kind, resource):
    url = f"{API}/{kind}"
    r = session.post(url, json=resource)
    if r.status_code == 409:  # already exists: overwrite with current data
        r = session.put(f"{url}/{resource['id']}", json=resource)
    if not r.ok:
        sys.exit(f"{kind} {resource['id']}: HTTP {r.status_code}\n{r.text}")


def save_link(signer, email, object_ids):
    from google.auth import jwt

    claims = {
        "iss": email,
        "aud": "google",
        "typ": "savetowallet",
        "iat": int(time.time()),
        "origins": [],
        "payload": {"genericObjects": [{"id": i} for i in object_ids]},
    }
    return SAVE_URL + jwt.encode(signer, claims).decode()


def write_page(path, rows, all_link):
    items = "\n".join(
        f'<li><a href="{html.escape(link)}">{html.escape(name)}</a> '
        f"<code>{html.escape(btype)}</code></li>"
        for name, btype, link in rows
    )
    page = f"""<!doctype html><meta charset="utf-8"><title>Klarna2Google</title>
<style>body{{font:16px system-ui;max-width:40rem;margin:2rem auto;padding:0 1rem}}
a.all{{display:inline-block;padding:.8rem 1.2rem;background:#000;color:#fff;border-radius:2rem;text-decoration:none}}
li{{margin:.4rem 0}}code{{color:#666;font-size:.8em}}</style>
<h1>Add loyalty cards to Google Wallet</h1>
<p><a class="all" href="{html.escape(all_link)}">Add all {len(rows)} cards</a></p>
<p>Or add them one by one:</p><ul>{items}</ul>
"""
    with open(path, "w") as f:
        f.write(page)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cards", default="klarna_cards.json")
    ap.add_argument("--key", help="service account JSON key")
    ap.add_argument("--issuer-id", help="numeric Issuer ID from the Pay & Wallet Console")
    ap.add_argument("--formats", help="JSON file mapping card name -> barcode type, to override guesses")
    ap.add_argument("--skip", default="skip.json",
                    help="JSON list of card names to leave out (written by scan_wallet.py); used when present")
    ap.add_argument("--logo-base", default=LOGO_BASE,
                    help="public URL prefix where logos/<slug>.png are hosted ('' to disable)")
    ap.add_argument("--out", default="wallet_links.html")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("names", nargs="*", help="cards to import (default: all not in the skip list)")
    args = ap.parse_args()

    cards = json.load(open(args.cards))
    skip = set(json.load(open(args.skip))) if args.skip and pathlib.Path(args.skip).exists() else set()
    if skip:
        print(f"Leaving out {len(skip)} cards listed in {args.skip}", file=sys.stderr)
    cards = [c for c in cards if c["name"] not in skip and (not args.names or c["name"] in args.names)]
    formats = json.load(open(args.formats)) if args.formats else {}
    issuer_id = args.issuer_id or "ISSUER_ID"
    objects = [build_object(issuer_id, c, formats, args.logo_base) for c in cards]

    if args.dry_run:
        print(json.dumps(objects, indent=2, ensure_ascii=False))
        return
    if not (args.key and args.issuer_id):
        ap.error("--key and --issuer-id are required (or use --dry-run)")

    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_file(args.key, scopes=[SCOPE])
    session = AuthorizedSession(creds)

    upsert(session, "genericClass", {"id": f"{issuer_id}.{CLASS_SUFFIX}"})
    for obj in objects:
        upsert(session, "genericObject", obj)
        print(f"ok  {obj['cardTitle']['defaultValue']['value']:<20} {obj['barcode']['type']}", file=sys.stderr)

    rows = [
        (o["cardTitle"]["defaultValue"]["value"], o["barcode"]["type"],
         save_link(creds.signer, creds.service_account_email, [o["id"]]))
        for o in objects
    ]
    all_link = save_link(creds.signer, creds.service_account_email, [o["id"] for o in objects])
    write_page(args.out, rows, all_link)
    print(f"\n{len(objects)} passes ready. Open {args.out} on a device signed in to your Google account.", file=sys.stderr)


if __name__ == "__main__":
    main()
