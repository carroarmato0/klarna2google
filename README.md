# klarna2google

Move your loyalty cards from the Klarna app (formerly Stocard) to Google Wallet.

Klarna has no export, blocks screenshots and disallows backups, so the cards are
read from the app's accessibility tree over ADB. No root needed.

## Requirements

- Android phone with USB debugging enabled, Klarna app in English, Dutch or French
  (Klarna → Profile → Language)
- `adb`, Python 3.10+, [`uv`](https://docs.astral.sh/uv/)
- A Google Wallet issuer account (free, see below)

## 1. Extract cards from Klarna

Unlock the phone, open Klarna → **Loyalty Cards** (*Klantenkaarten*, *Cartes de fidélité*), then:

```sh
python3 extract_klarna.py klarna_cards.json
```

The script opens every card and reads its number. Don't touch the phone while it runs.

## 2. Set up the Google Wallet API (one-off)

1. Create an issuer account at <https://pay.google.com/business/console/> and accept the
   Google Wallet API terms (Google Wallet API → Create a pass). Note the **Issuer ID**.
2. In a Google Cloud project, enable the
   [Google Wallet API](https://console.cloud.google.com/apis/library/walletobjects.googleapis.com).
3. [Create a service account](https://console.cloud.google.com/iam-admin/serviceaccounts/create),
   then Keys → Add key → JSON. Save it as `service-account.json`.
4. In the Pay & Wallet Console: Users → Invite a user → the service account email, access level **Developer**.

New issuer accounts are in demo mode: passes work for your own account but show `[TEST ONLY]`.

## 3. Add native loyalty cards where possible

Stores in Google's loyalty catalogue can be added as regular Wallet loyalty cards,
which never show `[TEST ONLY]` and need no issuer account. With Google Wallet open:

```sh
uv run add_native.py
```

This adds every card listed in `PROGRAMS` in the script (store name → catalogue
programme). Add others with `--program "Card=Programme name"`; find the exact
programme name in Wallet → Add to Wallet → Loyalty card → search.

Stores that aren't in the catalogue can still become native cards from a picture
of their barcode:

```sh
uv run add_native.py --scan
```

Each barcode is rendered as an image, copied to the phone's `Pictures/klarna2google`
and imported with the scanner's *Import from gallery*; the image is deleted again
afterwards. A card is only added when Wallet reads the same barcode. This works for
1D barcodes (Code 128, Code 39, EAN). Wallet doesn't pick up QR codes, neither from an
image nor with the camera, so those stay for step 5.

Then run `python3 scan_wallet.py --remove` to delete the klarna2google copies.

## 4. Skip cards already in Google Wallet

```sh
python3 scan_wallet.py
```

Opens Google Wallet on the phone (must be in English), reads the number of every
pass you already have and writes the Klarna cards found there to `skip.json`. Cards
already in Wallet win: a match on number (equal, or one ends with the other, as when
Klarna adds a prefix) is skipped. A match on name only is reported, not skipped.
Already imported duplicates? Run with `--remove` to delete our copies from Wallet.

## 5. Import the rest into Google Wallet

```sh
uv run import_to_wallet.py --key service-account.json --issuer-id <ISSUER_ID>
```

Open the generated `wallet_links.html` on your phone (or `adb shell am start -d '<link>'`)
and tap **Add**. Re-running the script updates passes already in Wallet.

Cards listed in `skip.json` are left out; use `--skip <file>` for another list.

Klarna doesn't expose the barcode type, so it is guessed from the number. If a card
doesn't scan, override it in a JSON file and pass `--formats formats.json`:

```json
{"JBC": "CODE_128"}
```

## Logos

`logos/<slug>.png` are served from this repository. To add or fix a logo, edit
`logos/sources.json` and run `uv run build_logos.py`. Merchants without a source get a monogram.
Logos are trademarks of their respective owners and are only used to identify the card.

## Privacy

`klarna_cards.json`, `skip.json`, `wallet_links.html` and `service-account.json` hold personal data or
credentials and are git-ignored. Never commit or share them.
