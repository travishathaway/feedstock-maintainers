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
