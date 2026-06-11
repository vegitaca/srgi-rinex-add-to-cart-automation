# SRGI RINEX Cart Automation

Automates requesting RINEX data from
https://srgi.big.go.id/rinex/v1/download-file-box: for each (station, year, DOY)
you list, it opens that station's page, clicks the DOY if it's available
(green), and submits it to "Keranjang". Once a station's days are all in the
cart, it goes to the cart page, clicks "Download", fills in the purpose form
("Pendidikan"), agrees to the Terms & Conditions, and submits the request —
fully automated, end to end, station by station.

**This requires the Indonesia network/VPN to be active for the entire run**,
since the cart/download step is geo-restricted.

## Setup (one-time)

1. Install Python 3.10+ and Microsoft Edge.
2. In this folder:
   ```
   pip install -r requirements.txt
   playwright install msedge
   ```
3. Open `requests.txt` and edit it with your stations/years/days (see format
   below). This is the file you'll edit every time you want to request
   different data.

## Running

```
python add_to_cart.py
```

(This reads `requests.txt` by default. If you want to keep multiple request
lists, you can save them as different `.txt` files and run e.g.
`python add_to_cart.py my_other_list.txt`.)

- The script first prints a summary of what it's about to request and
  pauses ("Press Enter to start..."). **Check this list carefully** before
  pressing Enter — this is your chance to catch typos.
- An Edge window opens. **The first time**, it will stop and ask you to log
  in manually (email/password + the "I'm not a robot" CAPTCHA) — this can't
  be automated. After logging in, press Enter in the terminal.
- Your session is saved in `browser_profile/` (gitignored), so on later runs
  it usually skips straight past login as long as the session is still valid.
- The script then visits each station/year and checks each requested DOY:
  - `available` (green) — will be added to the cart
  - `in-cart` — DOY was already blue (previously requested), skipped
  - `unavailable` — DOY is red (no data), skipped
  - `doy-not-found` — check your config values
- For DOYs that are available, it groups consecutive days into ranges and
  uses the site's "bulk select" toggle (the button with the tooltip "Klik
  untuk mengunduh banyak data sekaligus") to add each contiguous range as a
  single cart entry, e.g. `bako 360 - 365 (6 File)`. A lone day (not
  adjacent to another requested/available day) is added as its own
  one-day "range", e.g. `bako 17 - 17 (1 File)`.
- After each station/year batch with at least one available DOY, it clicks
  "Lanjut Ke Keranjang" then "Kembali ke halaman unduh data" automatically.
- The site only handles one station's download at a time, so jobs are
  grouped by station: the script processes all years for a station, adds
  them to the cart, then automatically goes to
  `https://srgi.big.go.id/rinex/v1/carts`, clicks "Download", selects
  "Pendidikan" as the purpose, checks the Terms & Conditions box, clicks
  "Continue", and waits (up to ~2 minutes) for the request to be submitted
  before moving on to the next station.
- It also dismisses unrelated popups that can appear on the cart page (e.g.
  an occasional "thank you for filling the survey" dialog) so they don't
  block the download flow.

## requests.txt format

One line per station + year:

```
STATION   YEAR   DAYS
```

`DAYS` (day-of-year, 1-365/366) can be a single day, a range (`360-365`), or
a comma-separated mix of both with no spaces (`360-365,367,370-372`). Lines
starting with `#` are comments/notes and are ignored.

Example:

```
# December 2025 + early January 2026 for bako
bako 2025 360-365
bako 2026 1-3

# A few extra days for calo
calo 2025 355-359,365
```

- One line = one station + one year + a set of days.
- The site can't select across years in one go, so split a request that
  spans Dec→Jan into two lines (one per year), as shown above.
- Keep each line's day count to a manageable chunk size (e.g. 20-30 per run)
  to avoid overloading the "on process" queue, per your earlier experience.

(If you prefer JSON, `add_to_cart.py` still accepts a `config.json` in the
old `{"jobs": [...]}` format — just run `python add_to_cart.py config.json`.
See `config.example.json` for that format.)

## Notes / known quirks

- A DOY that's already "blue" (bg-primary) on the calendar means it's
  already been requested before — the script skips it and reports
  `in-cart`. If a blue day falls in the middle of a requested range, it
  simply splits that range into smaller ranges around it (so e.g.
  requesting 1-160 with day 1 already blue results in a `2 - 160` entry).
- Red (bg-danger) means no RINEX data is available for that day yet.
- Credentials are never stored or auto-typed; login is always manual via the
  browser window for CAPTCHA reasons.
