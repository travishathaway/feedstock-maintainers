"""CLI-level tests that don't require real network access."""

from __future__ import annotations

import json

from click.testing import CliRunner

from feedstock_maintainers import cli as cli_module
from feedstock_maintainers import package_downloads as pd_module
from feedstock_maintainers.cli import main
from feedstock_maintainers.github import FetchError


def _patch_fetch_gitmodules(monkeypatch, text):
    monkeypatch.setattr(cli_module, "fetch_gitmodules", lambda: text)


def _gitmodules_text(*names: str) -> str:
    return "\n".join(
        f'[submodule "{name}"]\n'
        f"\tpath = {name}\n"
        f"\turl = https://github.com/conda-forge/{name}.git\n"
        "\tbranch = main\n"
        for name in names
    )


def test_startup_banner_never_prints_the_token(tmp_path, monkeypatch):
    _patch_fetch_gitmodules(monkeypatch, "")  # no feedstocks -> exits before any network calls

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "feedstocks",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--token",
            "super-secret-token",
        ],
    )

    assert result.exit_code == 0
    assert "super-secret-token" not in result.output
    assert "authenticated Contents API" in result.output


def test_startup_banner_reports_anonymous_mode_without_token(tmp_path, monkeypatch):
    _patch_fetch_gitmodules(monkeypatch, "")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["fetch", "feedstocks", "--cache-dir", str(tmp_path / "cache")],
        env={"GITHUB_TOKEN": ""},
    )

    assert result.exit_code == 0
    assert "anonymous raw.githubusercontent.com" in result.output


def test_fetch_errors_clearly_when_gitmodules_fetch_fails(monkeypatch):
    def raise_fetch_error():
        raise FetchError("boom")

    monkeypatch.setattr(cli_module, "fetch_gitmodules", raise_fetch_error)

    runner = CliRunner()
    result = runner.invoke(main, ["fetch", "feedstocks"])

    assert result.exit_code != 0
    assert "boom" in str(result.exception)


def _patch_fetch_recipe(monkeypatch, handler=None):
    seen: list[str] = []

    async def fake_fetch_recipe(client, source, cooldown, pacer, retries=3, token=None):
        seen.append(source.name)
        if handler is not None:
            return handler(source)
        return None

    monkeypatch.setattr(cli_module, "fetch_recipe", fake_fetch_recipe)
    return seen


def test_fetch_since_flag_is_passed_to_fetch_updated_feedstocks(tmp_path, monkeypatch):
    _patch_fetch_gitmodules(monkeypatch, _gitmodules_text("widget-feedstock"))
    _patch_fetch_recipe(monkeypatch)

    seen_calls = []

    def fake_fetch_updated_feedstocks(since, token=None, on_step=None):
        seen_calls.append((since, token))
        return set()

    monkeypatch.setattr(cli_module, "fetch_updated_feedstocks", fake_fetch_updated_feedstocks)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "feedstocks",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--since",
            "2026-08-01T00:00:00Z",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen_calls == [("2026-08-01T00:00:00Z", None)]


def test_fetch_since_forces_refetch_of_updated_feedstocks(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "widget-feedstock").mkdir()
    (cache_dir / "widget-feedstock" / "recipe.yaml").write_text("extra: {}\n")
    (cache_dir / "manifest.json").write_text(
        json.dumps({"widget-feedstock": {"status": "found", "filename": "recipe.yaml"}})
    )

    _patch_fetch_gitmodules(monkeypatch, _gitmodules_text("widget-feedstock"))
    seen = _patch_fetch_recipe(monkeypatch)
    monkeypatch.setattr(
        cli_module,
        "fetch_updated_feedstocks",
        lambda since, token=None, on_step=None: {"widget-feedstock"},
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["fetch", "feedstocks", "--cache-dir", str(cache_dir), "--since", "2026-08-01T00:00:00Z"],
    )

    assert result.exit_code == 0, result.output
    assert seen == ["widget-feedstock"]


def test_fetch_without_since_skips_fetch_updated_feedstocks(tmp_path, monkeypatch):
    _patch_fetch_gitmodules(monkeypatch, "")

    def fail_if_called(since, token=None, on_step=None):
        raise AssertionError("fetch_updated_feedstocks should not be called without --since")

    monkeypatch.setattr(cli_module, "fetch_updated_feedstocks", fail_if_called)

    runner = CliRunner()
    result = runner.invoke(main, ["fetch", "feedstocks", "--cache-dir", str(tmp_path / "cache")])

    assert result.exit_code == 0, result.output


def test_generate_maintainers_reads_from_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    (cache_dir / "widget-feedstock").mkdir(parents=True)
    (cache_dir / "widget-feedstock" / "recipe.yaml").write_text(
        "extra:\n  recipe-maintainers: [alice, bob]\n"
    )
    (cache_dir / "manifest.json").write_text(
        json.dumps({"widget-feedstock": {"status": "found", "filename": "recipe.yaml"}})
    )

    output = tmp_path / "maintainers.json"
    package_names_output = tmp_path / "package-names.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "generate",
            "maintainers",
            "--cache-dir",
            str(cache_dir),
            "--output",
            str(output),
            "--package-names-output",
            str(package_names_output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == {"widget-feedstock": ["alice", "bob"]}


def test_generate_maintainers_also_writes_package_names_for_multi_output_feedstock(tmp_path):
    cache_dir = tmp_path / "cache"
    (cache_dir / "boost-feedstock").mkdir(parents=True)
    (cache_dir / "boost-feedstock" / "meta.yaml").write_text(
        "package:\n"
        "  name: boost-split\n"
        "outputs:\n"
        "  - name: libboost\n"
        "  - name: boost-cpp\n"
        "extra:\n"
        "  recipe-maintainers: [alice]\n"
    )
    (cache_dir / "manifest.json").write_text(
        json.dumps({"boost-feedstock": {"status": "found", "filename": "meta.yaml"}})
    )

    output = tmp_path / "maintainers.json"
    package_names_output = tmp_path / "package-names.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "generate",
            "maintainers",
            "--cache-dir",
            str(cache_dir),
            "--output",
            str(output),
            "--package-names-output",
            str(package_names_output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(package_names_output.read_text()) == {
        "boost-feedstock": ["libboost", "boost-cpp"]
    }


def test_generate_maintainer_history_append_creates_file_on_first_run(tmp_path):
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(json.dumps({"widget-feedstock": ["alice", "bob"]}))
    history_file = tmp_path / "data" / "maintainer-history.json"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "generate",
            "maintainer-history-append",
            "--maintainers-file",
            str(maintainers_file),
            "--history-file",
            str(history_file),
        ],
    )

    assert result.exit_code == 0, result.output
    history = json.loads(history_file.read_text())
    assert len(history) == 1
    assert history[0]["unique_maintainer_count"] == 2


def test_generate_maintainer_history_append_collapses_same_month_runs(tmp_path):
    maintainers_file = tmp_path / "maintainers.json"
    history_file = tmp_path / "maintainer-history.json"

    def _run(maintainers: dict) -> list:
        maintainers_file.write_text(json.dumps(maintainers))
        result = CliRunner().invoke(
            main,
            [
                "generate",
                "maintainer-history-append",
                "--maintainers-file",
                str(maintainers_file),
                "--history-file",
                str(history_file),
            ],
        )
        assert result.exit_code == 0, result.output
        return json.loads(history_file.read_text())

    _run({"widget-feedstock": ["alice"]})
    history = _run({"widget-feedstock": ["alice", "bob"]})

    assert len(history) == 1  # same calendar month -> updated in place, not appended
    assert history[0]["unique_maintainer_count"] == 2


def test_fetch_maintainer_history_backfill_invokes_run_backfill_with_parsed_dates(
    tmp_path, monkeypatch
):
    seen_calls = []

    def fake_run_backfill(start, end, checkpoint_path, output_path, **kwargs):
        seen_calls.append((start, end, checkpoint_path, output_path))

    monkeypatch.setattr(cli_module, "run_backfill", fake_run_backfill)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "maintainer-history-backfill",
            "--start-date",
            "2020-01-01",
            "--end-date",
            "2020-03-01",
            "--state-file",
            str(tmp_path / "state.json"),
            "--output",
            str(tmp_path / "history.json"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(seen_calls) == 1
    start, end, checkpoint_path, output_path = seen_calls[0]
    assert start.isoformat() == "2020-01-01"
    assert end.isoformat() == "2020-03-01"
    assert checkpoint_path == tmp_path / "state.json"
    assert output_path == tmp_path / "history.json"


def test_generate_feedstock_count_append_creates_file_on_first_run(tmp_path):
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(
        json.dumps({"widget-feedstock": ["alice"], "gadget-feedstock": ["bob"]})
    )
    history_file = tmp_path / "data" / "feedstock-count-history.json"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "generate",
            "feedstock-count-append",
            "--maintainers-file",
            str(maintainers_file),
            "--history-file",
            str(history_file),
        ],
    )

    assert result.exit_code == 0, result.output
    history = json.loads(history_file.read_text())
    assert len(history) == 1
    assert history[0]["feedstock_count"] == 2


def test_generate_feedstock_count_append_collapses_same_month_runs(tmp_path):
    maintainers_file = tmp_path / "maintainers.json"
    history_file = tmp_path / "feedstock-count-history.json"

    def _run(maintainers: dict) -> list:
        maintainers_file.write_text(json.dumps(maintainers))
        result = CliRunner().invoke(
            main,
            [
                "generate",
                "feedstock-count-append",
                "--maintainers-file",
                str(maintainers_file),
                "--history-file",
                str(history_file),
            ],
        )
        assert result.exit_code == 0, result.output
        return json.loads(history_file.read_text())

    _run({"widget-feedstock": ["alice"]})
    history = _run({"widget-feedstock": ["alice"], "gadget-feedstock": ["bob"]})

    assert len(history) == 1  # same calendar month -> updated in place, not appended
    assert history[0]["feedstock_count"] == 2


def test_fetch_feedstock_count_history_backfill_invokes_run_backfill_with_parsed_dates(
    tmp_path, monkeypatch
):
    seen_calls = []

    def fake_run_backfill(start, end, output_path, **kwargs):
        seen_calls.append((start, end, output_path))

    monkeypatch.setattr(cli_module.fch, "run_backfill", fake_run_backfill)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "feedstock-count-history-backfill",
            "--start-date",
            "2020-01-01",
            "--end-date",
            "2020-03-01",
            "--output",
            str(tmp_path / "history.json"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(seen_calls) == 1
    start, end, output_path = seen_calls[0]
    assert start.isoformat() == "2020-01-01"
    assert end.isoformat() == "2020-03-01"
    assert output_path == tmp_path / "history.json"


def test_fetch_package_downloads_writes_output(tmp_path, monkeypatch):
    async def fake_fetch_monthly_downloads(months, timeout, **kwargs):
        return {"numpy": {"2026-01": 5, "2026-02": 8}, "scipy": {"2026-02": 2}}

    monkeypatch.setattr(cli_module, "fetch_monthly_downloads", fake_fetch_monthly_downloads)

    output = tmp_path / "package-downloads.json"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["fetch", "package-downloads", "--months", "2", "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == {
        "numpy": {"2026-01": 5, "2026-02": 8},
        "scipy": {"2026-02": 2},
    }


def test_fetch_package_downloads_passes_months_and_data_source_options(tmp_path, monkeypatch):
    seen_calls = []

    async def fake_fetch_monthly_downloads(months, timeout, data_source, **kwargs):
        seen_calls.append((months, data_source))
        return {}

    monkeypatch.setattr(cli_module, "fetch_monthly_downloads", fake_fetch_monthly_downloads)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "package-downloads",
            "--months",
            "3",
            "--data-source",
            "bioconda",
            "--output",
            str(tmp_path / "out.json"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(seen_calls) == 1
    months, data_source = seen_calls[0]
    assert len(months) == 3
    assert data_source == "bioconda"


def test_fetch_package_downloads_reports_error_on_fetch_failure(tmp_path, monkeypatch):
    async def fake_fetch_monthly_downloads(months, timeout, **kwargs):
        raise pd_module.PackageDownloadsFetchError("boom")

    monkeypatch.setattr(cli_module, "fetch_monthly_downloads", fake_fetch_monthly_downloads)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["fetch", "package-downloads", "--output", str(tmp_path / "out.json")],
    )

    assert result.exit_code != 0
    assert "boom" in result.output


def test_generate_package_maintainers_joins_files(tmp_path):
    package_names_file = tmp_path / "package-names.json"
    package_names_file.write_text(json.dumps({"boost-feedstock": ["libboost", "boost-cpp"]}))
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(json.dumps({"boost-feedstock": ["alice"]}))
    output = tmp_path / "package-maintainers.json"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "generate",
            "package-maintainers",
            "--package-names-file",
            str(package_names_file),
            "--maintainers-file",
            str(maintainers_file),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == {
        "libboost": ["alice"],
        "boost-cpp": ["alice"],
    }


def test_generate_maintainer_coverage_writes_stats_and_ranks_zero_maintainer_package_first(
    tmp_path,
):
    transitive_file = tmp_path / "transitive-dependencies.json"
    transitive_file.write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "name": "well-maintained",
                        "direct_dependents": 1,
                        "transitive_dependents": 100,
                        "transitive_only_dependents": 99,
                        "transitive_only_ratio": 0.99,
                    },
                    {
                        "name": "unmaintained",
                        "direct_dependents": 1,
                        "transitive_dependents": 100,
                        "transitive_only_dependents": 99,
                        "transitive_only_ratio": 0.99,
                    },
                ]
            }
        )
    )
    package_maintainers_file = tmp_path / "package-maintainers.json"
    package_maintainers_file.write_text(
        json.dumps({"well-maintained": ["alice", "bob"], "unmaintained": []})
    )
    output = tmp_path / "maintainer-coverage.json"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "generate",
            "maintainer-coverage",
            "--transitive-dependencies-file",
            str(transitive_file),
            "--package-maintainers-file",
            str(package_maintainers_file),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    data = json.loads(output.read_text())
    assert set(data["stats"].keys()) == {
        "package_count",
        "packages_with_zero_maintainers",
        "mean_maintainers",
        "median_maintainers",
        "stddev_maintainers",
    }
    assert [p["name"] for p in data["packages"]] == ["unmaintained", "well-maintained"]


def _patch_fetch_user_info(monkeypatch, handler):
    async def fake_fetch_user_info(client, username, cooldown, pacer, retries=3, token=None):
        return handler(username)

    monkeypatch.setattr(cli_module, "fetch_user_info", fake_fetch_user_info)


def test_fetch_maintainer_info_writes_profiles_and_skips_team_handles(tmp_path, monkeypatch):
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(
        json.dumps(
            {
                "widget-feedstock": ["alice", "conda-forge/go"],
                "gadget-feedstock": ["alice", "bob"],
            }
        )
    )
    output = tmp_path / "maintainer-info.json"

    _patch_fetch_user_info(monkeypatch, lambda username: {"login": username})

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "maintainer-info",
            "--maintainers-file",
            str(maintainers_file),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == {"alice": {"login": "alice"}, "bob": {"login": "bob"}}
    assert "1 team handles skipped" in result.output


def test_fetch_maintainer_info_resume_skips_existing_usernames(tmp_path, monkeypatch):
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(json.dumps({"widget-feedstock": ["alice", "bob"]}))
    output = tmp_path / "maintainer-info.json"
    output.write_text(json.dumps({"alice": {"login": "alice"}}))

    seen: list[str] = []

    def handler(username):
        seen.append(username)
        return {"login": username}

    _patch_fetch_user_info(monkeypatch, handler)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "maintainer-info",
            "--maintainers-file",
            str(maintainers_file),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == ["bob"]
    assert json.loads(output.read_text()) == {"alice": {"login": "alice"}, "bob": {"login": "bob"}}


def test_fetch_maintainer_info_force_refetches_existing_usernames(tmp_path, monkeypatch):
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(json.dumps({"widget-feedstock": ["alice"]}))
    output = tmp_path / "maintainer-info.json"
    output.write_text(json.dumps({"alice": {"login": "alice", "stale": True}}))

    seen: list[str] = []

    def handler(username):
        seen.append(username)
        return {"login": username, "stale": False}

    _patch_fetch_user_info(monkeypatch, handler)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "maintainer-info",
            "--maintainers-file",
            str(maintainers_file),
            "--output",
            str(output),
            "--force",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == ["alice"]
    assert json.loads(output.read_text()) == {"alice": {"login": "alice", "stale": False}}


def test_fetch_maintainer_info_404_is_reported_without_failing(tmp_path, monkeypatch):
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(json.dumps({"widget-feedstock": ["ghost", "alice"]}))
    output = tmp_path / "maintainer-info.json"
    not_found_output = tmp_path / "maintainers-not-found.json"

    _patch_fetch_user_info(
        monkeypatch, lambda username: None if username == "ghost" else {"login": username}
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "maintainer-info",
            "--maintainers-file",
            str(maintainers_file),
            "--output",
            str(output),
            "--not-found-output",
            str(not_found_output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == {"alice": {"login": "alice"}}
    not_found = json.loads(not_found_output.read_text())
    assert list(not_found) == ["ghost"]
    assert "404" in not_found["ghost"]
    assert "1" in result.output and "could not be resolved" in result.output


def test_fetch_maintainer_info_not_found_file_drops_usernames_that_are_later_found(
    tmp_path, monkeypatch
):
    maintainers_file = tmp_path / "maintainers.json"
    maintainers_file.write_text(json.dumps({"widget-feedstock": ["ghost"]}))
    output = tmp_path / "maintainer-info.json"
    not_found_output = tmp_path / "maintainers-not-found.json"
    not_found_output.write_text(json.dumps({"ghost": "GitHub user not found (HTTP 404)"}))

    # "ghost" resolves this time around -- the account was recreated.
    _patch_fetch_user_info(monkeypatch, lambda username: {"login": username})

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "maintainer-info",
            "--maintainers-file",
            str(maintainers_file),
            "--output",
            str(output),
            "--not-found-output",
            str(not_found_output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == {"ghost": {"login": "ghost"}}
    assert json.loads(not_found_output.read_text()) == {}
