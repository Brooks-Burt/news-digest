"""
News Digest
Pulls headlines from RSS feeds, summarizes/groups them with an LLM API,
and writes the result to docs/index.html for GitHub Pages / static hosting.

To repoint this at a different topic, edit the CONFIG block below —
nothing else needs to change.
"""

import trafilatura
import os
import sys
import json
import html
from datetime import datetime, timedelta, timezone

import feedparser
import requests

# ----------------------------------------------------------------------
# CONFIG — everything topic-specific lives here
# ----------------------------------------------------------------------

# What this digest is about, used in the page title and the prompt sent
# to the model so it knows what's relevant vs. noise.
DIGEST_TOPIC = "New England Patriots training camp"
PAGE_TITLE = "Patriots Camp Digest"
OVERREACTION_TITLE = "Panic Meter"

# RSS feed URLs — swap these out for any topic
FEEDS = [
    "https://musketfire.com/feed/",
    "https://www.patspulpit.com/rss/index.xml",
    "https://www.boston.com/tag/new-england-patriots/feed/",
    "https://www.nytimes.com/athletic/rss/nfl/patriots/",
    "https://www.thecoldwire.com/sports/nfl/new-england-patriots/feed/",
    "https://www.patspropaganda.com/feed/",
    "https://feeds.bleacherreport.com/articles"
]

# How the model should group stories. Adjust per topic.
CATEGORIES = [
    "Roster & Depth Chart",
    "Injuries",
    "Contracts & Business",
    "Standout Performers",
    "Coaching & Scheme",
    "Other News",
]

# Categories for the overreaction page — same underlying stories, dumber framing
OVERREACTION_CATEGORIES = [
    "Panic Alerts",
    "Injury Death Watch",
    "Front Office Conspiracy",
    "Legacy on the Line",
    "Scheme Meltdowns",
    "Everything Else Is Fine (Probably)",
]

# Only include stories published within this many hours (catches "daily" news,
# not stale evergreen posts some feeds include)
LOOKBACK_HOURS = 168

MODEL = "gpt-4o-mini"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
DOCS_DIR = os.path.join(os.path.dirname(__file__), "docs")
OUTPUT_PATH = os.path.join(DOCS_DIR, "index.html")
OVERREACTION_OUTPUT_PATH = os.path.join(DOCS_DIR, "overreactions.html")

# ----------------------------------------------------------------------
# 1.5. GET ARTICLE TEXT (PENDING OPTION)
# ----------------------------------------------------------------------

def extract_article(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            print(f"WARN: failed to download {url}", file=sys.stderr)
            return ""

        article = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            include_images=False,
        )
        if not article:
            print(f"WARN: failed to extract article from {url}", file=sys.stderr)
        return article or ""

    except Exception as e:
        print(f"ERROR: failed to extract article from {url}: {e}", file=sys.stderr)
        return ""

# ----------------------------------------------------------------------
# 2. FETCH
# ----------------------------------------------------------------------
def fetch_recent_entries():
    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    entries = []

    for url in FEEDS:
        try:
            feed = feedparser.parse(url)
            print(f"DEBUG: {url}", file=sys.stderr)
            print(f"DEBUG:   bozo={feed.bozo}, status={feed.get('status', 'n/a')}", file=sys.stderr)
            if feed.bozo:
                print(f"DEBUG:   bozo_exception={feed.bozo_exception}", file=sys.stderr)
            print(f"DEBUG:   title={feed.feed.get('title', 'NO TITLE')}, entries={len(feed.entries)}", file=sys.stderr)
        except Exception as e:
            print(f"WARN: failed to parse {url}: {e}", file=sys.stderr)
            continue

        source_name = feed.feed.get("title", url)

        for i, entry in enumerate(feed.entries):
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                print(f"DEBUG:   entry {i}: published {pub_dt.isoformat()}, cutoff {cutoff.isoformat()}", file=sys.stderr)
                if pub_dt < cutoff:
                    print(f"DEBUG:     -> FILTERED OUT (too old)", file=sys.stderr)
                    continue
            else:
                print(f"DEBUG:   entry {i}: NO PUBLISH DATE", file=sys.stderr)
                pub_dt = None

            print(f"DEBUG:     -> KEEPING", file=sys.stderr)
            article_text = extract_article(entry.get("link", ""))

            entries.append({
                "source": source_name,
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "summary": (entry.get("summary", "") or "")[:400],
                "article": article_text[:6000] if article_text else "",
                "published": pub_dt.isoformat() if pub_dt else None,
            })

    with_article = sum(1 for e in entries if e.get("article"))
    print(f"DEBUG: {with_article}/{len(entries)} entries have extracted article text", file=sys.stderr)

    return entries


# ----------------------------------------------------------------------
# 3. SUMMARIZE / GROUP via LLM API
# ----------------------------------------------------------------------
def _call_model(entries, system_prompt):
    entries_text = "\n\n".join(
    f"""[{i}]
SOURCE: {e['source']}
TITLE: {e['title']}
LINK: {e['link']}

SNIPPET:
{e['summary']}

ARTICLE:
{e['article'] if e.get('article') else '[ARTICLE NOT AVAILABLE]'}
"""
    for i, e in enumerate(entries)
)

    response = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": entries_text},
            ],
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    text = data["choices"][0]["message"]["content"]
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        print("ERROR: model did not return valid JSON:", text[:500], file=sys.stderr)
        sys.exit(1)


def build_digest(entries):
    """The factual, beat-writer-toned digest."""
    if not entries:
        return {"groups": [], "note": "No new stories in the lookback window."}

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    system_prompt = (
        f"You are a beat writer who has covered {DIGEST_TOPIC} for years, writing a daily digest. "
        "You will receive both an RSS snippet and, when available, the extracted article text. "
        "Always prefer information from the ARTICLE section because it contains more complete details. "
        "If ARTICLE is unavailable or marked '[ARTICLE NOT AVAILABLE]', summarize using only the RSS snippet. "
        "Never invent facts, quotes, or details that are not present in either source — personality "
        "belongs in your word choice and tone, never in the substance of what happened. "
        "Write with the voice of an experienced beat writer: confident, a little wry, using natural "
        "football vernacular (e.g. 'held down the edge,' 'climbed the depth chart,' 'made his case') "
        "rather than stiff or corporate phrasing. You may add one brief aside or bit of color per item "
        "(a short clause or parenthetical) reacting to the news the way a knowledgeable writer would — "
        "but keep it grounded in what the source actually reported, not speculation about outcomes it "
        "didn't state. Vary how each item opens; do not start every summary the same way. Keep the "
        "core facts, who/what, front and center — flavor is seasoning, not the whole dish. "
        f"Group items into these categories: {', '.join(CATEGORIES)}. "
        "Merge near-duplicate stories covering the same event (keep only one, but you may note "
        "if multiple outlets covered it). Skip anything not actually relevant to the topic. "
        "Write each summary in 2-3 sentences IN YOUR OWN WORDS (never copy wording from the "
        "snippet or article) and keep the original link and source name. "
        "Ensure capture of main point/player article may be hinting at (ie if title is "
        "this linebacker could prove to be a problem, please include players name)."
        "For each item, also include a field \"source_detail\" set to exactly \"full_article\" "
        "if you used the ARTICLE section, or \"rss_summary\" if ARTICLE was unavailable and you "
        "used only the snippet. "
        "Respond ONLY with valid JSON, no markdown fences, matching this schema:\n"
        '{"groups": [{"category": "string", "items": [{"headline": "string", '
        '"summary": "string", "source": "string", "link": "string", '
        '"source_detail": "full_article | rss_summary"}]}]}'
    )

    return _call_model(entries, system_prompt)


def build_overreaction_digest(entries):
    """Same stories, dialed up to absurd overreaction — but still fact-anchored."""
    if not entries:
        return {"groups": [], "note": "No new stories in the lookback window."}

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    system_prompt = (
        f"You write an unhinged, satirical overreaction column about {DIGEST_TOPIC}, in the "
        "style of a sports-talk-radio caller who has had way too much coffee. Every story, no "
        "matter how minor, gets treated as a five-alarm crisis or a franchise-altering triumph — "
        "a backup lineman getting a rep in 11-on-11s should sound like the second coming, a "
        "veteran resting for a day should sound like the roster is collapsing. Lean into hyperbole, "
        "dramatic declarations, and mock-serious stakes ('this changes everything,' 'sound the "
        "alarms,' 'we need to talk'). "
        "CRITICAL GUARDRAIL: the exaggeration is only ever in tone, framing, and stakes — never in "
        "the underlying facts. Every who/what/when must remain accurate to the source. Never invent "
        "injuries, transactions, quotes, or outcomes that didn't happen; you are allowed to be absurd "
        "about how much a real, small event matters, not to make up a bigger event. If that line ever "
        "feels blurry, stay closer to the facts and let the tone carry the joke instead. "
        "You will receive both an RSS snippet and, when available, the extracted article text; prefer "
        "the ARTICLE section when present, and fall back to the SNIPPET when it says "
        "'[ARTICLE NOT AVAILABLE]'. "
        f"Group items into these categories: {', '.join(OVERREACTION_CATEGORIES)}. "
        "Merge near-duplicate stories covering the same event into one, exaggerated item. Skip "
        "anything not actually relevant to the topic. "
        "Write each summary in 2-3 sentences IN YOUR OWN WORDS (never copy wording from the "
        "snippet or article) and keep the original link and source name. "
        "For each item, also include a field \"source_detail\" set to exactly \"full_article\" "
        "if you used the ARTICLE section, or \"rss_summary\" if ARTICLE was unavailable and you "
        "used only the snippet. "
        "Respond ONLY with valid JSON, no markdown fences, matching this schema:\n"
        '{"groups": [{"category": "string", "items": [{"headline": "string", '
        '"summary": "string", "source": "string", "link": "string", '
        '"source_detail": "full_article | rss_summary"}]}]}'
    )

    return _call_model(entries, system_prompt)


# ----------------------------------------------------------------------
# 4. RENDER HTML
# ----------------------------------------------------------------------
def render_html(digest, page_title=None, active_tab="digest"):
    page_title = page_title or PAGE_TITLE
    now = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")
    groups = digest.get("groups", [])

    if not groups:
        body = f'<p class="empty">{html.escape(digest.get("note", "No new stories today."))}</p>'
    else:
        sections = []
        for group in groups:
            items_html = "\n".join(
                f'''<li class="item">
                    <a class="headline" href="{html.escape(item.get("link",""))}" target="_blank" rel="noopener">{html.escape(item.get("headline",""))}</a>
                    <p class="summary">{html.escape(item.get("summary",""))}</p>
                    <div class="meta">
                        <span class="source">{html.escape(item.get("source",""))}</span>
                        <span class="badge {'badge-full' if item.get('source_detail') == 'full_article' else 'badge-rss'}">{'Full article' if item.get('source_detail') == 'full_article' else 'RSS summary only'}</span>
                    </div>
                </li>'''
                for item in group.get("items", [])
            )
            sections.append(f'''
                <section class="group">
                    <h2>{html.escape(group.get("category",""))}</h2>
                    <ul>{items_html}</ul>
                </section>''')
        body = "\n".join(sections)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html.escape(page_title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Oswald:wght@600;700&display=swap" rel="stylesheet">
<style>
  :root {{
    --navy: #0a2240;
    --navy-dark: #061530;
    --red: #b0313f;
    --silver: #c4c9cd;
    --bg: #f4f5f7;
    --card: #ffffff;
    --text: #1c2530;
    --text-muted: #66707c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Inter', -apple-system, Segoe UI, Roboto, sans-serif;
    max-width: 760px;
    margin: 0 auto 60px;
    padding: 0 20px;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
  }}
  header {{
    background: linear-gradient(135deg, var(--navy) 0%, var(--navy-dark) 100%);
    margin: 0 -20px 32px;
    padding: 36px 20px 22px;
    border-bottom: 4px solid var(--red);
  }}
  h1 {{
    font-family: 'Oswald', 'Inter', sans-serif;
    font-size: 1.9rem;
    font-weight: 700;
    letter-spacing: 0.01em;
    text-transform: uppercase;
    color: #ffffff;
    margin: 0 0 6px;
  }}
  .updated {{
    color: var(--silver);
    font-size: 0.82rem;
    font-weight: 500;
  }}
  .group {{ margin-bottom: 30px; }}
  .group h2 {{
    font-family: 'Oswald', 'Inter', sans-serif;
    font-size: 1rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--navy);
    border-left: 4px solid var(--red);
    padding: 2px 0 2px 10px;
    margin: 0 0 14px;
  }}
  ul {{ list-style: none; padding: 0; margin: 0; }}
  .item {{
    background: var(--card);
    border-radius: 10px;
    border: 1px solid #e8e9ec;
    padding: 16px 18px;
    margin-bottom: 12px;
    transition: box-shadow 0.15s ease, transform 0.15s ease;
  }}
  .item:hover {{
    box-shadow: 0 4px 14px rgba(10, 34, 64, 0.08);
    transform: translateY(-1px);
  }}
  .headline {{
    font-weight: 600;
    font-size: 1rem;
    color: var(--navy);
    text-decoration: none;
  }}
  .headline:hover {{ color: var(--red); text-decoration: underline; }}
  .summary {{
    margin: 8px 0 10px;
    color: var(--text);
    font-size: 0.93rem;
  }}
  .meta {{ display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }}
  .source {{
    font-size: 0.72rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 500;
  }}
  .badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    font-size: 0.65rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.02em;
  }}
  .badge-full {{ background: #e3f0e6; color: #2f7a3d; }}
  .badge-rss {{ background: #eceded; color: var(--text-muted); }}
  .empty {{ color: var(--text-muted); padding: 20px 0; }}
  nav {{
    display: flex;
    gap: 4px;
    margin-top: 16px;
  }}
  nav a {{
    font-family: 'Inter', sans-serif;
    font-size: 0.8rem;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    color: var(--silver);
    text-decoration: none;
    padding: 7px 14px;
    border-radius: 6px 6px 0 0;
    border: 1px solid transparent;
  }}
  nav a.active {{
    color: #ffffff;
    background: rgba(255,255,255,0.08);
    border-color: rgba(255,255,255,0.15);
    border-bottom-color: transparent;
  }}
  nav a:not(.active):hover {{
    color: #ffffff;
  }}
</style>
</head>
<body>
  <header>
    <h1>{html.escape(page_title)}</h1>
    <div class="updated">Last updated: {now}</div>
    <nav>
      <a href="index.html" class="{'active' if active_tab == 'digest' else ''}">Digest</a>
      <a href="overreactions.html" class="{'active' if active_tab == 'overreactions' else ''}">{html.escape(OVERREACTION_TITLE)}</a>
    </nav>
  </header>
  {body}
</body>
</html>"""


# ----------------------------------------------------------------------
# 5. MAIN
# ----------------------------------------------------------------------
def main():
    entries = fetch_recent_entries()
    print(f"Fetched {len(entries)} recent entries", file=sys.stderr)

    os.makedirs(DOCS_DIR, exist_ok=True)

    digest = build_digest(entries)
    output = render_html(digest, page_title=PAGE_TITLE, active_tab="digest")
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)
    print(f"Wrote digest to {OUTPUT_PATH}", file=sys.stderr)

    overreaction_digest = build_overreaction_digest(entries)
    overreaction_output = render_html(
        overreaction_digest, page_title=OVERREACTION_TITLE, active_tab="overreactions"
    )
    with open(OVERREACTION_OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(overreaction_output)
    print(f"Wrote overreaction page to {OVERREACTION_OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
