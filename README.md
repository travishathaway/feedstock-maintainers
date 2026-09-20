# feedstock-maintainers

> [!WARNING]
> **Experimental project** for gathering insights into who maintains [conda-forge](https://conda-forge.org/)
> feedstocks: how many people maintain each package, who co-maintains with whom, and what that
> collaboration network looks like as a whole. This is exploratory tooling, not a production
> service — expect rough edges and breaking changes.

The project has two parts:

- **CLI (Python)** — scrapes `extra.recipe-maintainers` (and the real conda package name(s)) out
  of every feedstock's recipe in
  [conda-forge/feedstocks](https://github.com/conda-forge/feedstocks), fetches each maintainer's
  public GitHub profile, builds a maintainer collaboration graph and a conda package dependency
  graph, and links the two together so you can ask things like "who maintains package X?" or
  "which heavily-depended-on packages have the fewest maintainers?".
- **Web app (Svelte)** — visualizes the maintainer collaboration graph in the browser.

## How it works

The CLI is split into three pipelines that build on each other. Each command reads its inputs
from local JSON files and writes its output to a local JSON file, so you can re-run any single
step without redoing the ones before it.

### 1. Maintainer pipeline

Who maintains each feedstock, and how are maintainers connected to each other?

1. `fetch feedstocks` downloads `.gitmodules` from `conda-forge/feedstocks` directly (no local
   checkout needed) and fetches each feedstock's `recipe/recipe.yaml` (or `recipe/meta.yaml`)
   into a local cache (`recipe_cache/` by default).
2. `generate maintainers` parses the cached recipes and writes two files in one pass:
   - `maintainers.json` — `extra.recipe-maintainers` per feedstock.
   - `package-names.json` — the real, installable conda package name(s) each feedstock's recipe
     declares (see [Linking maintainers to packages](#linking-maintainers-to-packages) below).
3. `fetch maintainer-info` looks up each unique maintainer username against the GitHub Users API
   and writes their public profile info to `maintainer-info.json`. Usernames that can't be
   resolved (deleted/renamed accounts, persistent fetch failures) are logged with a reason to
   `maintainers-not-found.json` — a useful signal for spotting feedstocks that may be abandoned.
4. `generate maintainer-graph` combines `maintainers.json` and `maintainer-info.json` into a
   [graphology](https://graphology.github.io/)-format graph — maintainers as nodes, shared
   feedstocks as weighted edges — for the web app to render.

### 2. Package pipeline

What depends on what, across the whole conda-forge ecosystem?

5. `generate package-graph` builds a directed dependency graph (package -> its dependencies) by
   downloading `repodata.json.zst` (the conda channel index) straight from conda-forge for one or
   more `--platform`s.
6. `generate transitive-dependencies` builds the same graph and ranks every package by how many
   other packages depend on it only *transitively* (through a chain of dependencies) and never
   directly — the ecosystem's "hidden" load-bearing packages.

### 3. Linking maintainers to packages

Feedstock names and conda package names usually match, but not always (a feedstock can produce
several packages, e.g. `boost` -> `libboost`, `libboost-devel`, ...; or a differently-cased/named
package). `package-names.json` from step 2 resolves that, so the two pipelines can be joined by
real package name:

7. `generate package-maintainers` joins `package-names.json` with `maintainers.json` into
   `package-maintainers.json` — `{package_name: [maintainer_login, ...]}`. This directly answers
   "who maintains package X?".
8. `generate maintainer-coverage` joins `transitive-dependencies.json` with
   `package-maintainers.json` into `maintainer-coverage.json`: overall stats (mean/median/stddev
   of maintainers per package) plus every package ranked by a `risk_score`
   (`transitive_dependents / (maintainer_count + 1)`) — packages that are heavily depended on but
   have relatively few maintainers sort first.

## Development

### Prerequisites

- [Pixi](https://pixi.sh/) for the Python CLI
- [Docker](https://www.docker.com/) and Docker Compose for the web app

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

`feedstock-maintainers` and `fsm` are equivalent entrypoints (`fsm` is just shorter to type).

```sh
# install dependencies and drop into the project environment
pixi install
pixi shell

# ── Maintainer pipeline ────────────────────────────────────────────────────
# 1. fetch feedstock recipes (fetches .gitmodules from conda-forge/feedstocks)
fsm fetch feedstocks

# 2. extract maintainers + real package name(s) from the cached recipes
fsm generate maintainers

# 3. fetch each maintainer's GitHub profile info
fsm fetch maintainer-info

# 4. build the collaboration graph consumed by the web app
fsm generate maintainer-graph --output web/static/data/maintainer-graph.json

# ── Package pipeline (see "Platforms" below) ────────────────────────────────
# 5. build the package dependency graph (downloads repodata.json.zst from conda-forge)
fsm generate package-graph -p linux-64 -p noarch

# 6. rank packages by transitive-only dependents (same download, same --platform options)
fsm generate transitive-dependencies -p linux-64 -p noarch

# ── Link the two together ──────────────────────────────────────────────────
# 7. who maintains each package?
fsm generate package-maintainers

# 8. maintainer-count stats + "heavily depended on, few maintainers" ranking
fsm generate maintainer-coverage
```

Every command has `--help` for its full option list (e.g. `fsm generate maintainers --help`).
Each step is resumable and safe to re-run — already-cached or already-fetched entries are skipped
by default (pass `--force` to re-fetch everything in the `fetch` commands). All `generate`
commands are pure local computation (no network access) and read/write plain JSON files, so
you're free to point `--output`/`-o` and the various `--*-file` options wherever you like;
the defaults above (`maintainers.json`, `package-names.json`, `maintainer-info.json`,
`package-maintainers.json`, `transitive-dependencies.json`, `maintainer-coverage.json`, ...) all
live in the current directory. Equivalent Pixi tasks for every fixed-argument command are defined
in `pyproject.toml` (e.g. `pixi run fetch-feedstocks`, `pixi run generate-maintainer-coverage`);
`generate package-graph` and `generate transitive-dependencies` take variable `--platform`
options, so they're run directly via `fsm`/`pixi run fsm generate ...` instead.

#### Platforms

`generate package-graph` and `generate transitive-dependencies` both download `repodata.json.zst`
for each `--platform`/`-p` you pass (e.g. `linux-64`, `noarch`, `linux-aarch64`, `osx-arm64`, ...)
concurrently, straight from
`https://conda.anaconda.org/conda-forge/<platform>/repodata.json.zst`, decompress it in memory,
and read both legacy `.tar.bz2` and newer `.conda` artifacts automatically -- no local file
needed.

These files are large (hundreds of MB uncompressed); pass as many subdirs as you want covered
(`linux-64`, `noarch`, `osx-arm64`, ...) as positional arguments -- both legacy `.tar.bz2` and
newer `.conda` artifacts in each file are read automatically.

Run the test suite with:

```sh
pixi run test
```

### Running the web app

The web app expects graph data at `web/static/data/maintainer-graph.json`, produced by
`generate maintainer-graph` above.

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
