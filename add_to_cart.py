"""
Automates adding RINEX data (station + DOY + year) to the SRGI BIG cart
(https://srgi.big.go.id/rinex/v1/download-file-box).

Usage:
    python add_to_cart.py config.json

On first run, a Chrome window opens. Log in manually (handles the CAPTCHA
yourself), then press Enter in the terminal to continue. The session is
saved in ./browser_profile so future runs skip the login step as long as
the session is still valid.
"""

import json
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = "https://srgi.big.go.id"
PROFILE_DIR = "browser_profile"


def close_info_modal(page):
    try:
        close_btn = page.locator("button:has-text('×'), .modal .close, .modal button[aria-label='Close']")
        if close_btn.count() > 0 and close_btn.first.is_visible():
            close_btn.first.click()
            page.wait_for_timeout(300)
    except Exception:
        pass


def ensure_logged_in(page):
    page.goto(f"{BASE_URL}/rinex/v1/download-file-box")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)
    if "/login" in page.url:
        print("\n>>> Not logged in.")
        print(">>> Please log in manually in the opened browser window")
        print(">>> (enter email/password, complete the CAPTCHA, click Login).")
        input(">>> Press Enter here once you're logged in and see the station list...\n")
        page.goto(f"{BASE_URL}/rinex/v1/download-file-box")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(1500)


def get_doy_status_map(page, year):
    """Return {doy_string: status} for all DOY cells under the `year` heading.

    status is one of: 'available' (bg-success), 'in-cart' (bg-primary),
    'unavailable' (bg-danger), or 'unknown'. Returns None if the year
    heading isn't found on the page.
    """
    return page.evaluate(
        """(year) => {
            const h5s = Array.from(document.querySelectorAll('h5'));
            const yearH5 = h5s.find(h => h.textContent.trim() === String(year));
            if (!yearH5) return null;
            const map = {};
            let el = yearH5.nextElementSibling;
            while (el && el.tagName !== 'H5') {
                const span = el.querySelector('span');
                if (span) {
                    let status = 'unknown';
                    if (el.className.includes('bg-success')) status = 'available';
                    else if (el.className.includes('bg-primary')) status = 'in-cart';
                    else if (el.className.includes('bg-danger')) status = 'unavailable';
                    map[span.textContent.trim()] = status;
                }
                el = el.nextElementSibling;
            }
            return map;
        }""",
        year,
    )


def click_doy_cell(page, year, doy):
    """Click the cell for `doy` under the `year` heading, regardless of its
    current status. Returns 'clicked', 'doy-not-found', or 'year-not-found'.
    """
    return page.evaluate(
        """([year, doy]) => {
            const h5s = Array.from(document.querySelectorAll('h5'));
            const yearH5 = h5s.find(h => h.textContent.trim() === String(year));
            if (!yearH5) return 'year-not-found';
            let el = yearH5.nextElementSibling;
            while (el && el.tagName !== 'H5') {
                const span = el.querySelector('span');
                if (span && span.textContent.trim() === String(doy)) {
                    el.click();
                    return 'clicked';
                }
                el = el.nextElementSibling;
            }
            return 'doy-not-found';
        }""",
        [year, doy],
    )


def set_bulk_mode(page, enable=True):
    """Enable/disable the 'Klik untuk mengunduh banyak data sekaligus'
    bulk-select toggle. When active, the button has an inline green
    background style. Returns True if the button was found.
    """
    btn = page.locator("button[title='Klik untuk mengunduh banyak data sekaligus']")
    if btn.count() == 0:
        return False
    style = btn.first.get_attribute("style") or ""
    is_active = "40, 167, 69" in style
    if is_active != enable:
        btn.first.click()
        page.wait_for_timeout(300)
    return True


def group_into_ranges(doys):
    """Group a sorted-or-unsorted list of ints into contiguous (start, end) ranges."""
    if not doys:
        return []
    s = sorted(set(doys))
    ranges = []
    start = prev = s[0]
    for d in s[1:]:
        if d == prev + 1:
            prev = d
        else:
            ranges.append((start, prev))
            start = prev = d
    ranges.append((start, prev))
    return ranges


def process_job(page, job):
    station = job["station"]
    year = job["year"]
    doys = job["doys"]

    print(f"\n=== Station {station} | Year {year} | DOYs {doys} ===")
    print(f"  Loading https://srgi.big.go.id/rinex/v1/download-file-box/{station} ...")
    page.goto(f"{BASE_URL}/rinex/v1/download-file-box/{station}")
    page.wait_for_load_state("domcontentloaded")
    close_info_modal(page)
    page.wait_for_selector("h5", timeout=20000)

    # Cells initially render red while a loading spinner is shown; the
    # real availability colors only appear after the spinner disappears.
    # This can take a while, so poll patiently (up to 5 minutes).
    print("  Waiting for the loading spinner to disappear (up to 5 minutes)...")
    loaded = False
    for attempt in range(60):  # up to 5 minutes
        has_spinner = page.evaluate(
            """() => {
                const spinners = document.querySelectorAll('.fa-spin');
                return Array.from(spinners).some(el => el.offsetParent !== null);
            }"""
        )
        if not has_spinner:
            loaded = True
            break
        print(f"    ... still loading ({(attempt + 1) * 5}s)")
        page.wait_for_timeout(5000)

    if not loaded:
        print("  (gave up waiting for spinner to disappear)")
    else:
        print("  Loading finished, checking DOY availability...")
    page.wait_for_timeout(1000)

    import os
    os.makedirs("debug", exist_ok=True)
    page.screenshot(path=f"debug/{station}_{year}.png", full_page=True)
    with open(f"debug/{station}_{year}.html", "w", encoding="utf-8") as f:
        f.write(page.content())
    print(f"  (saved debug/{station}_{year}.png and .html)")

    status_map = get_doy_status_map(page, year)
    if status_map is None:
        print(f"  Year {year} heading not found on this page, skipping.")
        return

    available_doys = []
    for doy in doys:
        status = status_map.get(str(doy), "doy-not-found")
        if status == "available":
            available_doys.append(doy)
        else:
            print(f"  DOY {doy}: {status}")

    if not available_doys:
        print("  Nothing new available for this station/year, skipping cart submit.")
        return

    ranges = group_into_ranges(available_doys)

    # Turn on bulk-select mode so each contiguous range becomes one cart entry.
    if not set_bulk_mode(page, enable=True):
        print("  (warning: bulk-select toggle button not found, proceeding without it)")
    page.wait_for_timeout(300)

    added = 0
    for lo, hi in ranges:
        click_doy_cell(page, year, lo)
        page.wait_for_timeout(400)
        if hi != lo:
            click_doy_cell(page, year, hi)
        else:
            # A single-day "range" needs the same cell clicked twice.
            click_doy_cell(page, year, lo)
        page.wait_for_timeout(600)
        n = hi - lo + 1
        added += n
        print(f"  DOY {lo}-{hi}: added ({n} file{'s' if n != 1 else ''})")

    # Lanjut Ke Keranjang
    page.get_by_text("Lanjut Ke Keranjang", exact=False).click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(800)
    print(f"  -> Submitted {added} DOY(s) to keranjang.")

    # Kembali ke halaman unduh data
    back_link = page.get_by_text("Kembali ke halaman unduh data", exact=False)
    if back_link.count() > 0:
        back_link.first.click()
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_timeout(800)


def parse_doy_spec(spec):
    """Parse a DOY spec like '360-365,1,5-8' into a sorted list of ints."""
    doys = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            lo, hi = int(lo.strip()), int(hi.strip())
            for d in range(lo, hi + 1):
                doys.add(d)
        else:
            doys.add(int(part))
    return sorted(doys)


def load_jobs_from_txt(path):
    """Parse a simple human-friendly request list file.

    Each non-empty, non-comment line is: STATION YEAR DOYS
    where DOYS is a comma-separated list of days and/or ranges,
    e.g.:  cnab 2025 360-365,367
    """
    jobs = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=2)
            if len(parts) != 3:
                raise ValueError(
                    f"{path}, line {lineno}: expected 'STATION YEAR DOYS', got: {raw_line!r}"
                )
            station, year, doy_spec = parts
            jobs.append({
                "station": station.lower(),
                "year": int(year),
                "doys": parse_doy_spec(doy_spec),
            })
    return jobs


def load_jobs(config_path):
    if config_path.endswith(".json"):
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)["jobs"]
    return load_jobs_from_txt(config_path)


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "requests.txt"
    jobs = load_jobs(config_path)

    print(f"Loaded {len(jobs)} request(s) from {config_path}:")
    for job in jobs:
        doys = job["doys"]
        print(f"  - {job['station']}  {job['year']}  DOYs {doys[0]}-{doys[-1]} ({len(doys)} day(s))"
              if doys else f"  - {job['station']}  {job['year']}  (no DOYs)")
    input("\nPress Enter to start, or Ctrl+C to cancel...\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            channel="chrome",
            headless=False,
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else context.new_page()

        def log_response(response):
            url = response.url
            if "data:image" in url or any(url.endswith(ext) for ext in (".css", ".js", ".png", ".jpg", ".svg", ".woff", ".woff2", ".ico")):
                return
            if "socket.io" in url:
                return
            if response.status >= 400:
                print(f"  [HTTP {response.status}] {url}")

        page.on("response", log_response)

        ensure_logged_in(page)
        close_info_modal(page)

        for job in jobs:
            process_job(page, job)
            time.sleep(1)

        print("\nAll jobs processed. You can review the cart at:")
        print(f"{BASE_URL}/rinex/v1/carts")
        input("\nPress Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()
