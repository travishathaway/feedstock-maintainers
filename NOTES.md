# Notes

### Summary

Project notes that read like journal logs.

## 2026-08-29

I've been refactoring the `fsm fetch feedstocks` command to:

1. Fetch the `.gitmodules` from a remote URL (no longer necessary to have checked it out locally) (done)
2. Determine what should be updated by cloning the repo locally using the last updated timestamp in the recipe_cache/manifest.json file (use the last fetched date).

Number 2 isn't done yet and will take a little bit to work through the right way to do it (e.g. clone repo with `--shallow-since` and store the fetch dates in the manifest.json itself).

I'm also working on GitHub Actions I can use to keep this feedstock cache up-to-date so the later GitHub Action that want to generate the website can use it.

I'll have to figure out how to do this for the "maintainer-info.json", but that's a different battle for a different day...

## 2026-08-30

Number 2 from yesterday has been completed. Now, I need to find a way to update everything else regularly and then build the website.

### Maintainer Info

Unfortunately, the `maintainer-info.json` is likely to get stale pretty quick. There's no way I can currently think of that would allow us to detect which account profile has been updated recently. Updating everything, about 8,000 users, takes a little under two hours running on a laptop (5,000 req/hour) and would take about eight hours in GitHub Actions (1,000 req/hour).

I think the easiest way around this is to set up a cronjob on a server outside of GHA that runs once per day that will refresh the profile information entirely. For this cronjob, we'll have to set it up as follows:

1. Fetch the latest cached artifact from GitHub's API
2. Run `fsm fetch maintainers --force` to trigger a complete refresh
3. Recompile everything to `.tar.gz` and upload to `SEED_URL`
4. Trigger the `reset.yml` action to manually reset GitHub's artifact cache and force a download from `SEED_URL` next time `update.yml` is run
