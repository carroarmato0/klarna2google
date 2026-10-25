# klarna2google

Move your loyalty cards from the Klarna app (formerly Stocard) to Google Wallet.

Klarna has no export button and Google Wallet has no import, so this is a set of
small tools plus what we learned getting the cards across as regular, native
Wallet cards. See [What we learned](#what-we-learned) before you start: it
saves you the dead ends.

## Requirements

- A Klarna account you can log into at <https://app.klarna.com>
- Android phone with USB debugging enabled and Google Wallet in English
- `adb`, Python 3.10+, [`uv`](https://docs.astral.sh/uv/)

## 1. Export your cards from Klarna

Log in at <https://app.klarna.com>, open the browser console (F12 → Console) and
paste the contents of [`export_klarna.js`](export_klarna.js). Your browser downloads
`klarna_cards.json`; move it next to the scripts. Some browsers ask you to type
`allow pasting` first.

Each card has the number printed on the card, the barcode type and, when it differs
from the number, the barcode contents:

```json
{"name": "Makro", "number": "12345678901", "barcode_format": "ITF", "barcode_value": "1234567890100000000000"}
```

## 2. Skip cards already in Google Wallet

Unlock the phone, then:

```sh
python3 scan_wallet.py
```

Opens Google Wallet, reads the number of every pass you already have and writes the
Klarna cards found there to `skip.json`. Cards already in Wallet win: a match on
number (equal, or one ends with the other) is skipped. A match on name only is
reported, not skipped.

## 3. Add the cards as native Wallet loyalty cards

Native cards are what you get when you add a card in the Wallet app yourself: no
`[TEST ONLY]`, no developer account. There are two ways in, both driven over ADB.

**Stores in Google's loyalty catalogue.** Wallet asks for the member number and picks
the barcode itself:

```sh
uv run add_native.py
```

This adds every card listed in `PROGRAMS` in the script (card name → catalogue
programme). Add others with `--program "Card=Programme name"`; find the exact
programme name in Wallet → Add to Wallet → Loyalty card → search.

**Any other card**, with a 1D barcode (Code 128, Code 39, EAN, ITF) or a QR code:

```sh
uv run add_native.py --scan
```

Each barcode is rendered as an image, copied to the phone's `Pictures/klarna2google`,
imported with the scanner's *Import from gallery* and deleted again. A card is only
added when the barcode Wallet read matches the card. It shows up in Wallet as
"<name> / Loyalty programme", like catalogue cards.

Without arguments, `--scan` takes every card not in `skip.json` or `PROGRAMS`. Pass
card names to do a few.

## 4. Optional: Google Wallet API passes for what's left

For a card that doesn't work natively, for example a rare Aztec, Data Matrix or
PDF417 code (`--scan` draws those as QR), you can issue a pass yourself through the
Google Wallet API. You control the barcode type and logo, but those passes show
`[TEST ONLY]` (see below why that stays). If that's fine:

1. Create an issuer account at <https://pay.google.com/business/console/> and accept the
   Google Wallet API terms (Google Wallet API → Create a pass). Note the **Issuer ID**.
2. In a Google Cloud project, enable the
   [Google Wallet API](https://console.cloud.google.com/apis/library/walletobjects.googleapis.com).
3. [Create a service account](https://console.cloud.google.com/iam-admin/serviceaccounts/create),
   then Keys → Add key → JSON. Save it as `service-account.json`.
4. In the Pay & Wallet Console: Users → Invite a user → the service account email, access level **Developer**.
5. Run:

   ```sh
   uv run import_to_wallet.py --key service-account.json --issuer-id <ISSUER_ID> [names...]
   ```

   Open the generated `wallet_links.html` on your phone (or `adb shell am start -d '<link>'`)
   and tap **Add**. Re-running the script updates passes already in Wallet. Cards in
   `skip.json` are left out.

The barcode type comes from Klarna. Wallet has no plain ITF, only 14-digit `ITF_14`,
so other ITF codes fall back to Code 128. To force a type, pass `--formats formats.json`
with e.g. `{"JBC": "CODE_128"}`.

API passes get a logo from `logos/<slug>.png`, served from this repository. To add or
fix one, edit `logos/sources.json` and run `uv run build_logos.py`; merchants without
a source get a monogram. Logos are trademarks of their respective owners and are only
used to identify the card.

If you imported API passes and later add the same cards natively, run
`python3 scan_wallet.py --remove` to delete the API copies. It only removes passes
titled `[TEST ONLY] …`.

## What we learned

**Getting the cards out of Klarna**

- The Klarna app has no export, sets `FLAG_SECURE` (screenshots come out black),
  disallows backups and isn't debuggable. We first read the cards from its
  accessibility tree over ADB (`uiautomator dump`), opening every card in turn. That
  works but is slow, breaks on UI changes and language settings, and **doesn't show
  the barcode type**, so we had to guess it, and some guesses scanned wrong.
- The Klarna web app at <https://app.klarna.com> uses
  `GET /<market>/api/loyalty_cards_bff/v1/loyalty-content` with
  `Authorization: Bearer <token>`. The token is in localStorage
  (`@KLAPP:signIn:accessToken`); cookies alone get a 401. One request returns every
  card. In `processed.barcode`: `id.raw` is the number printed on the card, `content`
  is what the barcode encodes and `format` is the barcode type.
- Number and barcode differ more often than you'd think: a `001001` prefix in the barcode
  (E5), a 22-digit ITF barcode for an 11-digit card (Makro), a URL in a QR code. The ADB
  approach couldn't tell them apart. Use `id.raw` to type in and `content` to draw.

**Getting them into Google Wallet**

- **The Google Wallet API is a dead end for personal use.** Passes from a new issuer
  account show `[TEST ONLY]` until Google grants publishing access. Google asks for a
  business website (GitHub doesn't count), social media and a sample pass, and expects
  you to own the loyalty programme. Showing other stores' brands won't get approved.
- **Native cards are the way.** Wallet's own "Add to Wallet → Loyalty card" flow gives
  regular cards. Stores in Google's catalogue only need the member number (Google
  picks the barcode). Other stores go through the scanner, which also accepts an image
  from the gallery, so no camera or physical card is needed.
- The scanner is an AI "photo of a pass" flow. It reads Code 128, Code 39, EAN and QR
  codes from generated images. It fills in the Member ID itself and keeps the barcode
  contents separately, so you can correct the number without losing the barcode.
- **QR pitfalls:** `segno.make()` silently produces a *Micro QR* code
  for short values (13 digits became version M3), which Wallet doesn't read; use
  `segno.make_qr()`. And Wallet only finds longer QR codes (a URL, version 4) when the
  modules are big: at 14 px per module it saw nothing, at 32 px it worked. We first
  concluded "Wallet can't read QR codes"; check your test images with a decoder such
  as zbar before blaming Wallet.
- The catalogue search is picky: "C&A" finds "C&A Belgium" but "C&A Belgium" finds
  nothing, while "Makro Belgi" finds "Makro Belgium".
- **Logos on native cards can't be set.** Catalogue cards get Google's logo for the
  programme, or a letter when Google has none. Scanned cards get whatever Wallet picks.
- A native card can look identical to an API pass in the list ("Ivago / Ivago"). Only
  the `[TEST ONLY]` title tells them apart, which is why `scan_wallet.py --remove` checks it.

**Driving the phone over ADB**

- `uiautomator dump` gives labels and screen positions of every element. Tapping by
  label is more robust than by coordinates, but the scripts still assume a 1080 px wide
  screen and English labels, and use fixed waits. Another phone or a Wallet update can
  break them.
- Check the foreground app before every tap: twice a changed foreground sent taps into
  another app. The scripts stop when anything but Wallet (or its system dialogs) is in
  front, or when the phone locks. They keep the screen on while plugged in and restore
  the setting afterwards.

## Privacy

`klarna_cards.json`, `skip.json`, `wallet_links.html` and `service-account.json` hold
personal data or credentials and are git-ignored. Never commit or share them, and
delete them when you're done. `export_klarna.js` only talks to Klarna, from your own
browser; the token never leaves it.
