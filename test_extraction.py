"""
test_extraction.py
-------------------
Standalone harness to test whether a given site will let trafilatura pull
and extract its article text — BEFORE wiring this into generate_digest.py.

Runs the same core path your real script uses:
    RSS feed  ->  first entry link  ->  download  ->  extract readable text

You can also point it straight at an article URL (skip the RSS step) by
setting TEST_MODE = "article".

Usage (in your venv on Mac):
    pip install trafilatura feedparser requests certifi
    python test_extraction.py
"""

import sys
import ssl
import certifi

# ----------------------------------------------------------------------
# macOS / local cert fix
# ----------------------------------------------------------------------
# On the GitHub Ubuntu runner the system CA bundle is already trusted, so
# HTTPS "just works". Locally on Mac (esp. python.org installs or behind an
# SSL-inspecting network) Python often can't verify certs -> the requests
# silently return nothing and you get "0 entries". Pointing Python at
# certifi's bundle makes local behave like the runner.
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

import trafilatura
import feedparser
import requests

# ----------------------------------------------------------------------
# CONFIG — the only thing you touch while testing
# ----------------------------------------------------------------------

TEST_MODE = "feed"          # "feed" = start from RSS; "article" = direct URL

# Used when TEST_MODE == "feed": pulls the first entry and extracts it.
FEED_URL = "https://patriotswire.usatoday.com/feed/"

# Used when TEST_MODE == "article": skips RSS, extracts this page directly.
ARTICLE_URL = "https://patriotswire.usatoday.com/"

# Browser-like header for the requests fallback. Many big outlets serve a
# blank/JS shell to trafilatura's default agent but respond to this.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


# ----------------------------------------------------------------------
# EXTRACTION — mirrors generate_digest.py, but loud instead of silent
# ----------------------------------------------------------------------
def extract_article(url):
    """Try trafilatura's own fetch first; fall back to requests+headers.
    Prints exactly where it succeeds or fails so you can diagnose per site."""
    print(f"\n--- extracting: {url}", file=sys.stderr)

    # Attempt 1: trafilatura's built-in fetcher
    try:
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            print(f"    trafilatura.fetch_url OK ({len(downloaded)} bytes)", file=sys.stderr)
            article = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=False,
                include_images=False,
            )
            if article:
                print(f"    extract OK ({len(article)} chars)", file=sys.stderr)
                return article
            print("    extract returned EMPTY (paywall/JS shell/unusual layout?)", file=sys.stderr)
        else:
            print("    trafilatura.fetch_url returned None (blocked/timeout?)", file=sys.stderr)
    except Exception as e:
        print(f"    trafilatura.fetch_url crashed: {e!r}", file=sys.stderr)

    # Attempt 2: requests with a browser UA, hand the HTML to trafilatura
    print("    -> trying requests fallback with browser UA", file=sys.stderr)
    try:
        resp = requests.get(url, headers=BROWSER_HEADERS, timeout=30)
        print(f"    requests status {resp.status_code} ({len(resp.text)} chars)", file=sys.stderr)
        resp.raise_for_status()
        article = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            include_images=False,
        )
        if article:
            print(f"    extract-from-requests OK ({len(article)} chars)", file=sys.stderr)
            return article
        print("    extract-from-requests returned EMPTY", file=sys.stderr)
    except Exception as e:
        print(f"    requests fallback crashed: {e!r}", file=sys.stderr)

    print("    RESULT: extraction FAILED for this URL", file=sys.stderr)
    return ""


# ----------------------------------------------------------------------
# FEED — grab the first usable entry link from an RSS feed
# ----------------------------------------------------------------------
def first_entry_link(feed_url):
    print(f"parsing feed: {feed_url}", file=sys.stderr)
    feed = feedparser.parse(feed_url)

    print(f"    bozo={feed.bozo}, status={feed.get('status', 'n/a')}", file=sys.stderr)
    if feed.bozo:
        print(f"    bozo_exception={feed.bozo_exception!r}", file=sys.stderr)
    print(f"    title={feed.feed.get('title', 'NO TITLE')}, "
          f"entries={len(feed.entries)}", file=sys.stderr)

    if not feed.entries:
        print("    no entries — can't test extraction from this feed", file=sys.stderr)
        return None, None

    entry = feed.entries[0]
    return entry.get("link", ""), entry.get("title", "").strip()


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------
def main():
    print(f"TEST_MODE = {TEST_MODE}", file=sys.stderr)

    if TEST_MODE == "feed":
        link, title = first_entry_link(FEED_URL)
        if not link:
            sys.exit(1)
        print(f"\nfirst entry: {title}\nlink: {link}", file=sys.stderr)
        article = extract_article(link)
    elif TEST_MODE == "article":
        article = extract_article(ARTICLE_URL)
    else:
        print(f"unknown TEST_MODE: {TEST_MODE!r}", file=sys.stderr)
        sys.exit(1)

    # ---- report ----
    print("\n" + "=" * 60)
    if article:
        print(f"SUCCESS — extracted {len(article)} characters")
        print("=" * 60)
        print("\nFIRST 800 CHARS:\n")
        print(article[:800])
        print("\n... [truncated]" if len(article) > 800 else "")
    else:
        print("FAILURE — nothing extracted. See the per-stage log above")
        print("to see whether it died at download, extraction, or both.")
        print("=" * 60)


if __name__ == "__main__":
    main()
