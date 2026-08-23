"""CLI-level tests that don't require real network access."""

from __future__ import annotations

from click.testing import CliRunner

from feedstock_maintainers.cli import main


def test_startup_banner_never_prints_the_token(tmp_path):
    gitmodules = tmp_path / ".gitmodules"
    gitmodules.write_text("")  # no feedstocks -> exits before any network calls

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--gitmodules", str(gitmodules), "--output", str(tmp_path / "out.json"), "--token", "super-secret-token"],
    )

    assert result.exit_code == 0
    assert "super-secret-token" not in result.output
    assert "authenticated Contents API" in result.output


def test_startup_banner_reports_anonymous_mode_without_token(tmp_path):
    gitmodules = tmp_path / ".gitmodules"
    gitmodules.write_text("")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["--gitmodules", str(gitmodules), "--output", str(tmp_path / "out.json")],
        env={"GITHUB_TOKEN": ""},
    )

    assert result.exit_code == 0
    assert "anonymous raw.githubusercontent.com" in result.output
