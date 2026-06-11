"""
Automates adding RINEX data (station + DOY + year) to the SRGI BIG cart
(https://srgi.big.go.id/rinex/v1/download-file-box) and submitting the
download request for each station (purpose: Pendidikan, agreeing to T&C).

Usage:
    python add_to_cart.py config.json

On first run, an Edge window opens. Log in manually (handles the CAPTCHA
yourself), then press Enter in the terminal to continue. The session is
saved in ./browser_profile so future runs skip the login step as long as
the session is still valid.

The Indonesia VPN/network must be active for the whole run, since the
download-submission step requires it.
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


def dismiss_unrelated_popups(page):
    """Close any visible modal that isn't the download-purpose form (e.g. the
    occasional 'thank you for filling the survey' popup), so it doesn't block
    later clicks. Best-effort; ignores errors.
    """
    try:
        modals = page.locator(".modal.show, .modal[style*='display: block'], .modal[style*='display:block']")
        for i in range(modals.count()):
            modal = modals.nth(i)
            if not modal.is_visible():
                continue
            if modal.locator("#button-term-ok").count() > 0:
                continue  # this is the download-purpose modal, leave it alone
            close_btn = modal.locator(
                "button:has-text('×'), button:has-text('Close'), button:has-text('Tutup'), "
                "button:has-text('OK'), button:has-text('Lewati'), button:has-text('Nanti'), "
                "button[aria-label='Close']"
            )
            if close_btn.count() > 0 and close_btn.first.is_visible():
                close_btn.first.click()
                page.wait_for_timeout(300)
    except Exception:
        pass


def submit_cart_download(page, station):
    """On the cart page, click Download, fill the purpose form (Pendidikan +
    agree to T&C), click Continue, and wait for the request to be submitted
    (cart becomes empty). Assumes Indonesia VPN/network is active throughout.
    Returns True if the cart ended up empty, False otherwise.
    """
    print(f"\n  Going to cart to submit download request for '{station}'...")
    page.goto(f"{BASE_URL}/rinex/v1/carts")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1000)
    dismiss_unrelated_popups(page)

    if page.get_by_text("Tidak ada list item", exact=False).count() > 0:
        print("  Cart is already empty, nothing to download.")
        return True

    download_btn = page.locator("button.btn-primary.btn-md:visible", has_text="Download")
    if download_btn.count() == 0:
        print("  (warning: Download button not found on cart page)")
        return False
    download_btn.first.click()
    page.wait_for_timeout(500)
    dismiss_unrelated_popups(page)

    # Purpose-of-data-usage modal
    try:
        page.wait_for_selector("#button-term-ok", timeout=10000)
    except Exception:
        print("  (warning: download form modal didn't appear)")
        return False

    pendidikan = page.locator("input[name='purpose-group'][value='pendidikan']")
    if pendidikan.count() > 0 and not pendidikan.first.is_checked():
        pendidikan.first.check(force=True)

    terms_checkbox = page.locator("#button-term-ok")
    if not terms_checkbox.is_checked():
        terms_checkbox.check(force=True)
    page.wait_for_timeout(300)

    continue_btn = page.locator(".modal-footer button", has_text="Continue")
    continue_btn.first.click()
    print("  Submitted download request, waiting for it to be processed (up to 2 minutes)...")

    # Wait for the cart to clear (request accepted) or the request-ready link to appear.
    for attempt in range(24):  # up to 2 minutes
        dismiss_unrelated_popups(page)
        if page.get_by_text("Tidak ada list item", exact=False).count() > 0:
            print(f"  -> Cart cleared, request submitted for '{station}'.")
            return True
        link = page.locator("#Link a")
        if link.count() > 0 and link.first.is_visible():
            href = link.first.get_attribute("href")
            print(f"  -> Download ready: {href}")
            return True
        page.wait_for_timeout(5000)

    print("  (gave up waiting for the download request to finish processing)")
    return False


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

    # While the real availability is still being fetched, almost all cells
    # show as a red/'unavailable' placeholder (sometimes with a couple of
    # stray non-placeholder cells already resolved). The refresh button's
    # icon spins permanently and isn't a useful loading signal. Instead, we
    # treat the page as "still loading" while the fraction of cells that
    # AREN'T 'unavailable'/'unknown' is tiny (<=2 cells or <5%), and only
    # declare it loaded once that fraction grows AND two consecutive reads
    # come back identical (the real colors have settled). Capped at 5 min.
    print("  Waiting for DOY availability to finish loading (up to 5 minutes)...")
    loaded = False
    previous_map = None
    for attempt in range(60):  # up to 5 minutes (60 * 5s)
        current_map = get_doy_status_map(page, year)
        still_loading = True
        if current_map is not None:
            total = len(current_map)
            resolved = sum(
                1 for status in current_map.values()
                if status not in ("unavailable", "unknown")
            )
            still_loading = resolved <= 2 or (total > 0 and resolved / total < 0.05)
        if current_map is not None and not still_loading and current_map == previous_map:
            loaded = True
            break
        previous_map = current_map
        print(f"    ... still loading ({(attempt + 1) * 5}s)")
        page.wait_for_timeout(5000)

    if not loaded:
        print("  (gave up waiting for DOY status to settle)")
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


def group_jobs_by_station(jobs):
    """Group jobs into ordered (station, [jobs]) groups, preserving the
    order stations first appear in. Jobs for the same station (different
    years) stay together in one group.
    """
    groups = []
    index_by_station = {}
    for job in jobs:
        station = job["station"]
        if station not in index_by_station:
            index_by_station[station] = len(groups)
            groups.append((station, []))
        groups[index_by_station[station]][1].append(job)
    return groups


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "requests.txt"
    jobs = load_jobs(config_path)
    station_groups = group_jobs_by_station(jobs)

    print(f"Loaded {len(jobs)} request(s) from {config_path}, grouped into "
          f"{len(station_groups)} station(s):")
    for station, station_jobs in station_groups:
        for job in station_jobs:
            doys = job["doys"]
            print(f"  - {job['station']}  {job['year']}  DOYs {doys[0]}-{doys[-1]} ({len(doys)} day(s))"
                  if doys else f"  - {job['station']}  {job['year']}  (no DOYs)")
    print("\nThe site only handles one station's download at a time. After each "
          "station's data is added to cart, the script will automatically click "
          "Download, select 'Pendidikan', agree to the T&C, and submit the "
          "request before moving on to the next station.")
    print("Make sure the Indonesia VPN/network is already active before starting.")
    input("\nPress Enter to start, or Ctrl+C to cancel...\n")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            channel="msedge",
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

        for i, (station, station_jobs) in enumerate(station_groups, start=1):
            print(f"\n##### Station {i}/{len(station_groups)}: {station} #####")
            for job in station_jobs:
                process_job(page, job)
                time.sleep(1)

            print(f"\n  -> All requested DOYs for station '{station}' have been "
                  f"added to the cart: {BASE_URL}/rinex/v1/carts")
            submit_cart_download(page, station)

        print("\nAll stations processed. You can review the cart at:")
        print(f"{BASE_URL}/rinex/v1/carts")
        input("\nPress Enter to close the browser...")
        context.close()


if __name__ == "__main__":
    main()
