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
PAGE_TITLE = "Pats Training Camp Digest"

# RSS feed URLs — swap these out for any topic
FEEDS = [
    "https://musketfire.com/feed/",
    "https://www.patspulpit.com/rss/index.xml",
    "https://www.boston.com/tag/new-england-patriots/feed/",
    "https://www.nytimes.com/athletic/rss/nfl/patriots/",
    "https://www.thecoldwire.com/sports/nfl/new-england-patriots/feed/",
    "https://www.patspropaganda.com/feed/",
  #  "https://feeds.bleacherreport.com/articles"
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

# Only include stories published within this many hours (catches "daily" news,
# not stale evergreen posts some feeds include)
LOOKBACK_HOURS = 48

MODEL = "gpt-4o-mini"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OUTPUT_PATH = os.path.join(os.path.dirname(__file__), "docs", "index.html")

# ----------------------------------------------------------------------
# 1.5. GET ARTICLE TEXT (PENDING OPTION)
# ----------------------------------------------------------------------

def extract_article(url):
    try:
        downloaded = trafilatura.fetch_url(url)
      #  print(f"Downloaded {url}: {len(downloaded) if downloaded else 0} bytes")
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
# 3. SUMMARIZE / GROUP via Claude API
# ----------------------------------------------------------------------
def build_digest(entries):
    if not entries:
        return {"groups": [], "note": "No new stories in the lookback window."}

    if not OPENAI_API_KEY:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

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

    system_prompt = (
        f"You are organizing news about {DIGEST_TOPIC} into a daily digest. "
        "You will receive both an RSS snippet and, when available, the extracted article text. "
        "Always prefer information from the ARTICLE section because it contains more complete details. "
        "If ARTICLE is unavailable or marked '[ARTICLE NOT AVAILABLE]', summarize using only the RSS snippet. "
        "Never invent facts that are not present in either source. "
        f"Group them into these categories: {', '.join(CATEGORIES)}. "
        "Merge near-duplicate stories covering the same event (keep only one, but you may note "
        "if multiple outlets covered it). Skip anything not actually relevant to the topic. "
        "You are a senior NFL editor producing a Patriots morning briefing. "
        "Your audience has NOT read these articles. For every article: "
        "State immediately who the story is about."
        "Rewrite clickbait into factual language."
        "Mention every important player or coach by name."
        "Never write 'a player', 'one veteran', 'a forgotten weapon', or similar vague phrases."
        "Explain why the story matters."
        "Write 1 concise paragraphs. Assume your briefing must stand on its own. "
        "If the article contains speculation, clearly label it as speculation."
        "Never invent facts."
        "For each item, also include a field \"source_detail\" set to exactly \"full_article\" "
        "if you used the ARTICLE section, or \"rss_summary\" if ARTICLE was unavailable and you "
        "used only the snippet. "
        "Respond ONLY with valid JSON, no markdown fences, matching this schema:\n"
        '{"groups": [{"category": "string", "items": [{"headline": "string", '
        '"summary": "string", "source": "string", "link": "string", '
        '"source_detail": "full_article | rss_summary"}]}]}'
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


# ----------------------------------------------------------------------
# 4. RENDER HTML
# ----------------------------------------------------------------------
def render_html(digest):
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
<title>{html.escape(PAGE_TITLE)}</title>
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
</style>
</head>
<body>
  <header>
    <h1>{html.escape(PAGE_TITLE)}</h1>
    <div class="updated">Last updated: {now}</div>
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
    digest = build_digest(entries)
    output = render_html(digest)

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"Wrote digest to {OUTPUT_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()