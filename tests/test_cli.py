"""CLI-level tests that don't require real network access."""

from __future__ import annotations

import json

from click.testing import CliRunner

from feedstock_maintainers import cli as cli_module
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
    runner = CliRunner()
    result = runner.invoke(
        main, ["generate", "maintainers", "--cache-dir", str(cache_dir), "--output", str(output)]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text()) == {"widget-feedstock": ["alice", "bob"]}


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
        main, ["fetch", "maintainer-info", str(maintainers_file), "--output", str(output)]
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
        main, ["fetch", "maintainer-info", str(maintainers_file), "--output", str(output)]
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
        ["fetch", "maintainer-info", str(maintainers_file), "--output", str(output), "--force"],
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
