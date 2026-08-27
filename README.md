# feedstock-maintainers

**Experimental project** for gathering insights into who maintains [conda-forge](https://conda-forge.org/)
feedstocks: how many people maintain each package, who co-maintains with whom, and what that
collaboration network looks like as a whole. This is exploratory tooling, not a production
service — expect rough edges and breaking changes.

The project has two parts:

- **CLI (Python)** — scrapes `extra.recipe-maintainers` out of every feedstock's recipe in
  [conda-forge/feedstocks](https://github.com/conda-forge/feedstocks), fetches each maintainer's
  public GitHub profile, and builds a maintainer collaboration graph from the results.
- **Web app (Svelte)** — visualizes the resulting graph in the browser.

## How it works

1. `fetch feedstocks` reads `.gitmodules` from a local checkout of `conda-forge/feedstocks` and
   downloads each feedstock's `recipe/recipe.yaml` (or `recipe/meta.yaml`) into a local cache.
2. `generate maintainers` parses the cached recipes and extracts `extra.recipe-maintainers` into
   `maintainers.json`.
3. `fetch maintainer-info` looks up each unique maintainer username against the GitHub Users API
   and writes their public profile info to `maintainer-info.json`. Usernames that can't be
   resolved (deleted/renamed accounts, persistent fetch failures) are logged with a reason to
   `maintainers-not-found.json` — a useful signal for spotting feedstocks that may be abandoned.
4. `generate graph-data` combines the two into a [graphology](https://graphology.github.io/)-format
   graph — maintainers as nodes, shared feedstocks as weighted edges — for the web app to render.

## Development

### Prerequisites

- [Pixi](https://pixi.sh/) for the Python CLI
- [Docker](https://www.docker.com/) and Docker Compose for the web app
- A local checkout of [conda-forge/feedstocks](https://github.com/conda-forge/feedstocks)
  (only needed for `fetch feedstocks`)

### Creating a GitHub token

Fetching goes much faster, and much further, with a GitHub token:

- Without a token, feedstock recipes are fetched anonymously from `raw.githubusercontent.com`
  and the GitHub Users API is capped at 60 requests/hour.
- With a token, recipes are fetched via the authenticated Contents API and the Users API allows
  5,000 requests/hour, with proactive rate-limit pacing based on GitHub's real `X-RateLimit-*`
  headers.

To create one: on GitHub, go to **Settings → Developer settings → Personal access tokens → Fine-grained
tokens**, generate a new token with no special scopes (all endpoints used here are public, read-only
data), and copy it somewhere safe.

Set it as an environment variable rather than passing `--token` on the command line, so it doesn't
end up in your shell history:

```sh
export GITHUB_TOKEN='ghp_...'
```

### Running the CLI

```sh
# install dependencies and drop into the project environment
pixi install
pixi shell

# 1. fetch feedstock recipes (requires a local conda-forge/feedstocks checkout)
feedstock-maintainers fetch feedstocks --feedstocks-repo /path/to/feedstocks

# 2. extract maintainers from the cached recipes
feedstock-maintainers generate maintainers

# 3. fetch each maintainer's GitHub profile info
feedstock-maintainers fetch maintainer-info maintainers.json

# 4. build the collaboration graph consumed by the web app
feedstock-maintainers generate graph-data --output web/static/data/maintainer-graph.json
```

Each step is resumable and safe to re-run — already-cached or already-fetched entries are skipped
by default (pass `--force` to re-fetch everything). Equivalent Pixi tasks are defined in
`pyproject.toml` (e.g. `pixi run fetch-feedstocks`).

Run the test suite with:

```sh
pixi run test
```

### Running the web app

The web app expects graph data at `web/static/data/maintainer-graph.json`, produced by
`generate graph-data` above.

With Docker Compose:

```sh
cd web
docker compose up
```

This builds the container, installs dependencies, and starts the Vite dev server at
[http://localhost:5173](http://localhost:5173) with hot reload enabled.

Without Docker, from the `web` directory:

```sh
pnpm install
pnpm dev
```

## Contributing

Contributions are welcome! A few general guidelines:

- Open an issue before starting significant work, to discuss the approach first.
- Keep pull requests focused — one logical change per PR.
- Add or update tests for any behavior change (`pixi run test` for the CLI).
- Follow the existing code style; there are no strict formatter/linter requirements enforced yet.
- Make sure existing tests pass before opening a PR.
- Be respectful and constructive in issues and reviews.

## License

[MIT](LICENSE)
