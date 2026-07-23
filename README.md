# News Digest


Pulls news from RSS feeds daily, summarizes/groups it with the
openAI API, and publishes it as a simple webpage via GitHub Pages.

## One-time setup

1. **Create a new GitHub repo** (public is easiest — private works too, just
   uses a small slice of your free Actions minutes).

2. **Push these files into it.**
   ```
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo-name>.git
   git push -u origin main
   ```

3. **Get an OpenAI API key** at https://platform.openai.com/api-keys.
   This is separate from a ChatGPT login — it needs its own billing set up
   under platform.openai.com even if you already pay for ChatGPT Plus.

4. **Add the key as a repo secret**:
   Repo → Settings → Secrets and variables → Actions → New repository secret
   - Name: `OPENAI_API_KEY`
   - Value: (paste your key)

5. **Enable GitHub Pages**:
   Repo → Settings → Pages → Source: "Deploy from a branch" → Branch: `main`,
   folder: `/docs` → Save.
   GitHub will give you a URL like `https://<you>.github.io/<repo-name>/`.
   Bookmark it — that's your daily digest page.

6. **Test it manually** before waiting for the schedule:
   Repo → Actions tab → "Daily Patriots Digest" workflow → "Run workflow".
   Check the run logs, then visit your Pages URL after it finishes (may take
   a minute to publish the first time).

## Adjusting things

- **Feed list**: edit `FEEDS` in `generate_digest.py`. Add any RSS feed URL.
- **Run time**: edit the `cron` line in `.github/workflows/daily-digest.yml`
  (times are UTC).
- **Lookback window**: `LOOKBACK_HOURS` in the script — how far back to pull
  stories from, useful if camp news is sparse some days vs. others.
- **Categories**: tweak the `system_prompt` in `build_digest()` if you want
  different groupings (e.g. split out "Rookie Watch" during camp).

## Cost

- GitHub Actions + Pages: free at this scale.
- OpenAI API: a daily digest run is a few thousand tokens — expect well
  under $1/month with gpt-4o-mini (the default in the script). Note that
  OpenAI billing is separate from ChatGPT Plus and requires adding a
  payment method at platform.openai.com/settings/organization/billing.
