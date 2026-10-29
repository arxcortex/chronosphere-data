# chronosphere-data

Data and the Grok Bot for **Chronosphere** (arxcortex.io/chronosphere.html).

- **Data track:** every 6 hours Grok Bot refreshes the Now lens. It writes to the `live` branch and
  republishes GitHub Pages. No Netlify deploy, no permission needed, and a validator keeps the last good
  file if anything breaks.
- **Proposal track:** every Sunday Grok Bot opens up to 3 proposal issues (Idea, Frame, Why add, Cost,
  Risk). Reply `/approve`, `/reject` or `/revise <note>`. Approve opens a pull request; merging it is the
  only thing that ships.

`GROK.md` is the bot's standing brief. Edit it to change how the bot thinks.

## Layout

```
GROK.md                      the bot's brief (read by every Grok call)
lenses/geo/eras.json         geopolitical eras, extracted from chronosphere.html
lenses/religion/eras.json    religion/ideology eras
lenses/politics/             US elections from 1932, and the poll file the page reads
lenses/now/                  Now lens config, dispatches, embeds
data/history_index.json      flat index of historical events, used to find precedent echoes
data/chokepoints.json        hand-curated chokepoints
data/SOURCES.md              every source and its terms
static/                      province outlines, copied to the Pages root
proposals/BACKLOG.md         ideas for the bot to pick from
scripts/                     build_now, validate, propose, handle_command (Python, standard library)
client/now-client.js         loader to drop into the site as chronosphere/now-client.js
tools/extract_eras.mjs       re-run if you edit eras inside chronosphere.html
.github/workflows/           refresh-now (6 h), pages, propose (weekly), proposal-commands
```

Published at `https://<you>.github.io/chronosphere-data/`:
`now/now.json`, `now/now_30d.json`, `lenses/...`, `data/chokepoints.json`, `provinces_earth_simplified.json`, `provinces_ca_ru_cn.json`.

## One-time setup (about 20 minutes)

1. **Create the repo.** On GitHub, new repository `chronosphere-data`, **public** (Pages and Actions
   minutes are free only on public repos). Upload everything in this folder.
2. **Turn on Pages.** Settings > Pages > Source: **GitHub Actions**.
3. **Let Actions open pull requests.** Settings > Actions > General > Workflow permissions:
   "Read and write", and tick "Allow GitHub Actions to create and approve pull requests".
4. **xAI key.** In the xAI console create an API key and set a monthly spend limit. Then in the repo,
   Settings > Secrets and variables > Actions:
   - Secret `XAI_API_KEY` = the key
   - Variable `XAI_MODEL` = the Grok model to use (check the console for current names)
5. **Bot token.** GitHub > Settings > Developer settings > Fine-grained tokens. Repository access:
   `chronosphere-data` and your site repo only. Permissions: Contents, Issues, Pull requests (read and
   write). Save it as secret `BOT_TOKEN`.
6. **Site repo.** Add variable `SITE_REPO` = `<you>/<site-repo>` (the repo Netlify deploys arxcortex.io from).
7. **Protect main** in both repos: Settings > Branches > add rule for `main`, require a pull request.
   The bot can then never push to main; it can only write `live` and open pull requests.
8. **First run.** Actions > Refresh Now lens > Run workflow, with "Skip the Grok step" ticked.
   Open `https://<you>.github.io/chronosphere-data/now/now.json` and check it. Then run it again
   without the tick to see Grok's version. The 6-hour schedule is already on.
9. **First proposals.** Actions > Weekly proposals > Run workflow. Issues appear labelled `proposal`.
10. **Site side.** The Now lens is built into `chronosphere.html` (first pass, 24 Oct). Set
    `CHRONO_DATA_BASE` near the top of its Now section to `https://<you>.github.io/chronosphere-data`,
    deploy, check the NOW stop shows the live feed, then delete the site's `now/` preview folder.
    `client/now-client.js` is the same loader as a standalone module, for other pages.

## Costs

- GitHub: free (public repo, Pages soft limit 100 GB a month, 1 GB site).
- Netlify: only the page itself, plus one deploy per merged site proposal.
- xAI: 4 refresh calls a day plus a weekly proposal run. Set a spend cap on the key.

## When something breaks

- **Validation failed** on a refresh: the previous now.json stays live. Actions emails you. Usually a
  source was down; the next run normally fixes it.
- **Grok is down or the key ran out:** the refresh falls back to deterministic headlines from URL slugs.
- **Bot built the wrong thing:** close the pull request. Nothing shipped.

Data: GDELT Project (gdeltproject.org), USGS, NASA EONET. See `data/SOURCES.md`.
