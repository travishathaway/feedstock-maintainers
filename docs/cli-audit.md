# CLI audit (2026-10-08)

Method: `ruff --select F` (no unused imports/variables), plus an AST list of every top-level
symbol in `src/` grepped across `src/` and `tests/`, plus a check of which command outputs are
consumed by `update.yml`, `pages.yml`, `generate site-data` and the web app.

## Removed
- `recipe.extract_maintainers_from_text`, `extract_package_names_from_text`,
  `extract_license_from_text`: only tests called them; `parse_recipe` is what the CLI uses.
  Tests now go through small helpers over `parse_recipe`.
- `pyproject.toml`: `known-first-party` pointed at another project (`thath_idp`); a Flask
  comment on `B008`; ruff `target-version` (py310) disagreed with `requires-python >=3.12`;
  `rich-click` was missing from `[project].dependencies` (a plain `pip install` would fail on import).
- NOTES.md: stale `fsm fetch maintainers --force` -> `fetch maintainer-info`.

## Kept deliberately
- `_SilentUndefined._fail_with_undefined_error` (`recipe.py`): a jinja2 hook, called by jinja2.
- `__version__`, `scipy` (likely needed by `nx.pagerank`).

## Candidates for removal (your call)
| Item | Evidence |
|---|---|
| `generate maintainer-coverage` / `maintainer-coverage.json` | Not in `update.yml`, not archived, not read by `site-data` or the web app. Superseded by `site_data.compute_package_overview`. Removing it also drops the only `numpy` use (`graph_data.py` `compute_maintainer_coverage`) and `tests/test_cli.py` coverage tests. |
| `maintainers-not-found.json` | Archived, but only read by `fetch maintainer-info`'s own resume logic. |
| `fetch feedstock-count-history-backfill` | Runs every update but its commit message called it "temporary". It is incremental, so check whether it is still doing real work. |
| `fetch maintainer-history-backfill` | Manual-only; keep if you need to rebuild history from scratch. |

## Duplication worth consolidating
- `maintainer-history-append` and `feedstock-count-append` (`generate.py`) are near copies.
- `_atomic_write_json` is defined in `maintainer_history.py` and `feedstock_count_history.py`
  in addition to `cli/_shared._atomic_write`.
- Three bot-detection helpers: `activity._is_bot`, `health_signals.is_bot_actor`,
  `health_signals._is_bot_commit_author`.
- `site_data.is_team_handle` vs inline `"/" in login` in `fetch.py` and `teams.py`.
- `package-graph`, `transitive-dependencies` and `fetch package-about` each download the same
  repodata (three downloads per update run).

## Other findings
- `pages.yml` `find-source` only looks at `update.yml` runs, so it can miss a newer `cold-start.yml` artifact.
- `update.yml` passes `--maintainers-file maintainers.json` (the default).
- About 10 commands have no pixi task; 7 commands have no CLI-level tests
  (`package-graph`, `transitive-dependencies`, `feedstock-tiers`, `feedstock-activity`,
  `feedstock-health`, `listed-maintainer-counts`, `package-about`).
- `env.sh` (untracked, gitignored) contains a plaintext GitHub token. Consider rotating it.
