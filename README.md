# klarna2google

Move your loyalty cards from the Klarna app (formerly Stocard) to Google Wallet.

Klarna has no export, blocks screenshots and disallows backups, so the cards are
read from the app's accessibility tree over ADB. No root needed.

## Requirements

- Android phone with USB debugging enabled, Klarna app in **English**
- `adb`, Python 3.10+, [`uv`](https://docs.astral.sh/uv/)
- A Google Wallet issuer account (free, see below)

## 1. Extract cards from Klarna

Unlock the phone, open Klarna → **Loyalty Cards**, then:

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

## 3. Import into Google Wallet

```sh
uv run import_to_wallet.py --key service-account.json --issuer-id <ISSUER_ID>
```

Open the generated `wallet_links.html` on your phone (or `adb shell am start -d '<link>'`)
and tap **Add**. Re-running the script updates passes already in Wallet.

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

`klarna_cards.json`, `wallet_links.html` and `service-account.json` hold personal data or
credentials and are git-ignored. Never commit or share them.
