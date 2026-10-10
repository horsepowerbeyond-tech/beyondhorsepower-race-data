# BeyondHorsepower race data automation

Prepared for https://github.com/horsepowerbeyond-tech/beyondhorsepower-race-data
and the existing https://beyondhorsepower-race-data.pages.dev feed.

Shopify serves the Race Centre and player. A standard public-repository GitHub Actions runner prepares static FastF1 JSON files and uploads only `public/` to the existing Cloudflare Pages Direct Upload project. No Python server is required.

## Upload through GitHub in a browser

1. Extract this ZIP on your computer.
2. In the repository choose **Add file > Upload files**.
3. Drag all contents from INSIDE the extracted folder into the upload area: `automation`, `public`, `tests`, `requirements.txt`, `workflow-template.yml` and `README.md`.
4. Commit directly to `main`. Do not upload the ZIP itself or its enclosing folder.

The root `workflow-template.yml` is deliberately inactive. It avoids hidden-folder upload problems on macOS and lets you configure credentials before enabling automation.

## Configure Cloudflare (next installation steps)

Create a scoped token with **Account > Cloudflare Pages > Edit**, restricted to the account containing the existing project. Keep the token private.

Repository **Settings > Secrets and variables > Actions > Secrets**:

| Secret | Value |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | The existing Pages project's 32-character account ID |
| `CLOUDFLARE_API_TOKEN` | The scoped Pages Edit token |

The project name and feed URL are already defaults. No DNS changes, GitHub Pages configuration, or new Cloudflare project are needed.

## Activate and test the workflow

1. Open `workflow-template.yml` in GitHub and copy its entire raw text.
2. Choose **Add file > Create new file**, name it `.github/workflows/update-races.yml`, paste the text and commit to `main`.
3. Open **Actions > Update BeyondHorsepower races > Run workflow**. Leave year and round blank to use the current season, or set them for a completed historical race.
4. Confirm the run succeeds and the updated feed still loads in both Shopify pages.
5. Under **Settings > Secrets and variables > Actions > Variables**, add `ENABLE_RACE_UPDATES` with value `true` to enable scheduled runs.

Scheduled checks run every three hours at minute 17 UTC. GitHub may delay scheduled runs; this is not a live timing service. Public-repository schedules can be disabled by GitHub after 60 days without repository activity; re-enable or commit a reviewed change when needed.

The exporter waits at least five hours after the scheduled race start, requires finished-session timing and classification data, and processes at most two races per run. Newest eligible races come first; older races in the selected season are backfilled over later runs. Missing data is retried. Recent races are refreshed for seven days. Blank season uses the current UTC year automatically at year rollover. The included Monaco 2024 replay is preserved.

## Shopify page automation (configure after data updates work)

Data updates alone automatically change the existing player and Race Centre's browser-rendered race listing. Shopify API access is needed separately to create individual race pages and update the hub's HTML summary for search crawlers.

Create/install a Shopify app owned by the SAME Shopify organization as the store, with `read_content` and `write_content` scopes. Client credentials are exchanged for a fresh token on every worker run. If that grant is not available for your app/store, use the supported OAuth installation flow instead; do not use an expired copied token.

Add Secrets `SHOPIFY_CLIENT_ID` and `SHOPIFY_CLIENT_SECRET`.
Add Variables:

| Variable | Value |
|---|---|
| `SHOPIFY_STORE` | The permanent `name.myshopify.com` hostname |
| `ENABLE_SHOPIFY_SYNC` | `true` once the app is installed and hub markers are present |
| `PUBLISH_RACES` | `false` for a draft review; `true` when automatic publication is desired |

In the manually created Shopify Race Centre page body, use HTML mode and include exactly one marker pair around the generated summary:

```html
<!-- BHR-AUTO-START -->
<h2>Race results and replay archive</h2>
<p>Explore race results, tyre strategies and interactive replays.</p>
<!-- BHR-AUTO-END -->
```

Editorial text outside these markers is preserved. Unrelated pages and unmarked content are protected. New race pages are drafts by default. Existing published pages remain published. A failed Shopify sync is reported after preserving the data deployment. Generic `/pages/race-replay` is not modified by the publisher.

## Archive, hosting and costs

Every run restores the full live index and all ready replay files before exporting. If any live replay cannot be retrieved or validated, the run stops before deployment, preserving the existing Cloudflare production deployment. The actual Cloudflare production branch is read from its API so uploads do not accidentally go to a preview branch.

The `public/_headers` file keeps Shopify's cross-origin data fetch enabled. `404.html` prevents missing JSON paths from being treated as a single-page HTML app. Deployment checks enforce Cloudflare's 25 MiB per-file and 20,000-file limits. Files remain public; there are no credentials in `public/`.

The workflow uses standard public-repository Ubuntu runners and static Cloudflare Pages hosting. There are no Actions artifact uploads, additional Actions cache storage, Pages Functions or paid runners. Every-three-hour checks mean at most 248 scheduled deployments in a 31-day month, within the Free plan's 500-deployment limit; manual runs and other projects sharing the quota also count. Keep the existing Free plan and account limits unchanged.

## Validation

FastF1 exporter and Shopify publisher tests cover completed-session eligibility, missing fields, ownership markers, unchanged-page detection, visibility and client-credential handling. Additional tests cover full archive restoration, interrupted downloads, mismatched replay IDs, missing files and deployment limits. Remote credentials and scheduled execution still require the first successful GitHub run; preparing this package does not activate them.
