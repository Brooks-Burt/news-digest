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
PAGE_TITLE = "News Digest"

# RSS feed URLs — swap these out for any topic
FEEDS = [
    "https://patriotswire.usatoday.com/feed/",
    "https://musketfire.com/feed/",
    "https://profootballtalk.nbcsports.com/category/teams/afc/new-england-patriots/feed/",
    "https://www.patspulpit.com/rss/index.xml",
    "[https://www.boston.com/tag/new-england-patriots/feed/](https://www.boston.com/tag/new-england-patriots/feed/)",
    "https://www.si.com/nfl/patriots/",
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
LOOKBACK_HOURS = 168

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
          #  print(f"WARN: failed to download {url}")
            return ""

        article = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            include_images=False,
        )

        return article or ""

    except Exception:
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
        except Exception as e:
            print(f"WARN: failed to parse {url}: {e}", file=sys.stderr)
            continue

        source_name = feed.feed.get("title", url)

        for entry in feed.entries:
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            if published:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_dt < cutoff:
                    continue
            else:
                pub_dt = None

            article_text = extract_article(entry.get("link", ""))
            article_text = article_text[:6000]
            entries.append({
                "source": source_name,
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", ""),
                "summary": (entry.get("summary", "") or "")[:400],
                "article": article_text,
                "published": pub_dt.isoformat() if pub_dt else None,
            })
            #test 
            print(article_text[:400])
   
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
        "For each item, write a neutral 2-3 sentence summary IN YOUR OWN WORDS (never copy "
        "wording from the snippet) and keep the original link and source name. "
        "Ensure capture of main point/player article may be hinting at (ie if title is "
        "this linebacker could prove to be a problem, please include players name)."
        "Respond ONLY with valid JSON, no markdown fences, matching this schema:\n"
        '{"groups": [{"category": "string", "items": [{"headline": "string", '
        '"summary": "string", "source": "string", "link": "string"}]}]}'
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
                    <span class="source">{html.escape(item.get("source",""))}</span>
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
<title>Patriots Training Camp Digest</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 720px;
          margin: 40px auto; padding: 0 20px; background: #f7f7f8; color: #1a1a1a; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  .updated {{ color: #666; font-size: 0.85rem; margin-bottom: 32px; }}
  .group {{ margin-bottom: 32px; }}
  .group h2 {{ font-size: 1.1rem; border-bottom: 2px solid #002244; padding-bottom: 6px;
               color: #002244; }}
  ul {{ list-style: none; padding: 0; }}
  .item {{ background: white; border-radius: 8px; padding: 14px 16px; margin-bottom: 10px;
           box-shadow: 0 1px 2px rgba(0,0,0,0.06); }}
  .headline {{ font-weight: 600; color: #002244; text-decoration: none; }}
  .headline:hover {{ text-decoration: underline; }}
  .summary {{ margin: 6px 0 4px; color: #333; font-size: 0.95rem; }}
  .source {{ font-size: 0.75rem; color: #888; text-transform: uppercase; letter-spacing: 0.03em; }}
  .empty {{ color: #666; }}
</style>
</head>
<body>
  <h1>Patriots Training Camp Digest</h1>
  <div class="updated">Last updated: {now}</div>
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
