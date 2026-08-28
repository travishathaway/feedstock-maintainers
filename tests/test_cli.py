"""CLI-level tests that don't require real network access."""

from __future__ import annotations

import json

from click.testing import CliRunner

from feedstock_maintainers import cli as cli_module
from feedstock_maintainers.cli import main


def test_startup_banner_never_prints_the_token(tmp_path):
    (tmp_path / ".gitmodules").write_text("")  # no feedstocks -> exits before any network calls

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "feedstocks",
            "--feedstocks-repo",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "cache"),
            "--token",
            "super-secret-token",
        ],
    )

    assert result.exit_code == 0
    assert "super-secret-token" not in result.output
    assert "authenticated Contents API" in result.output


def test_startup_banner_reports_anonymous_mode_without_token(tmp_path):
    (tmp_path / ".gitmodules").write_text("")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "fetch",
            "feedstocks",
            "--feedstocks-repo",
            str(tmp_path),
            "--cache-dir",
            str(tmp_path / "cache"),
        ],
        env={"GITHUB_TOKEN": ""},
    )

    assert result.exit_code == 0
    assert "anonymous raw.githubusercontent.com" in result.output


def test_fetch_errors_clearly_when_gitmodules_missing(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["fetch", "feedstocks", "--feedstocks-repo", str(tmp_path)])

    assert result.exit_code != 0
    assert "--feedstocks-repo" in result.output


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
