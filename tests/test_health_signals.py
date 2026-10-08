from datetime import datetime, timedelta, timezone

from feedstock_maintainers import health_signals as hs


def _pr(title="Fix", draft=False, login="alice", typename="User", labels=()):
    return {
        "number": 1,
        "isDraft": draft,
        "title": title,
        "createdAt": "2026-01-01T00:00:00Z",
        "author": {"login": login, "__typename": typename},
        "labels": {"nodes": [{"name": name} for name in labels]},
    }


def _bot_pr(title, labels=()):
    return _pr(title, login="regro-cf-autotick-bot", typename="Bot", labels=labels)


def test_classify_open_pr():
    assert hs.classify_open_pr(_pr(draft=True)) == "draft"
    assert hs.classify_open_pr(_bot_pr("x v1.2.3") | {"isDraft": True}) == "draft"
    assert hs.classify_open_pr(_pr()) == "human"
    assert hs.classify_open_pr(_bot_pr("foo v1.2.3")) == "version_update"
    assert hs.classify_open_pr(_bot_pr("Rebuild for python 3.14")) == "migration"
    assert hs.classify_open_pr(_bot_pr("foo v1.2.3", labels=["bot-migration"])) == "migration"
    assert hs.classify_open_pr(_bot_pr("Something odd")) == "bot_other"


def test_is_bot_actor():
    assert hs.is_bot_actor({"login": "x", "__typename": "Bot"})
    assert hs.is_bot_actor({"login": "regro-cf-autotick-bot", "__typename": "User"})
    assert hs.is_bot_actor({"login": "some-app[bot]", "__typename": "User"})
    assert not hs.is_bot_actor({"login": "alice", "__typename": "User"})
    assert not hs.is_bot_actor(None)  # ghost/deleted users are not assumed to be bots


def test_parse_health_signals_skips_bot_commits_and_comments():
    repo = {
        "defaultBranchRef": {
            "target": {
                "history": {
                    "nodes": [
                        {
                            "committedDate": "2026-09-01T00:00:00Z",
                            "author": {"name": "bot", "user": {"login": "regro-cf-autotick-bot"}},
                        },
                        {
                            "committedDate": "2026-08-01T00:00:00Z",
                            "author": {"name": "conda-forge-webservices[bot]", "user": None},
                        },
                        {
                            "committedDate": "2026-05-01T00:00:00Z",
                            "author": {"name": "Alice", "user": {"login": "alice"}},
                        },
                    ]
                }
            }
        },
        "openPRs": {"totalCount": 3, "nodes": [_pr(), _pr(draft=True), _bot_pr("foo v2.0")]},
        "recentPRs": {
            "nodes": [
                {
                    "comments": {
                        "nodes": [
                            {
                                "createdAt": "2026-09-20T00:00:00Z",
                                "author": {"login": "regro-cf-autotick-bot", "__typename": "Bot"},
                            },
                            {
                                "createdAt": "2026-07-01T00:00:00Z",
                                "author": {"login": "bob", "__typename": "User"},
                            },
                        ]
                    }
                }
            ]
        },
        "openIssues": {"totalCount": 4},
        "recentIssues": {"nodes": []},
    }
    parsed = hs.parse_health_signals(repo)
    assert parsed["last_human_commit_at"] == "2026-05-01T00:00:00Z"
    assert parsed["last_human_comment_at"] == "2026-07-01T00:00:00Z"
    assert parsed["open_prs"] == {
        "human": 1,
        "draft": 1,
        "version_update": 1,
        "migration": 0,
        "bot_other": 0,
        "total_open": 3,
    }
    assert parsed["open_issues_count"] == 4


def test_parse_health_signals_handles_empty_repo():
    parsed = hs.parse_health_signals({"defaultBranchRef": None})
    assert parsed["last_human_commit_at"] is None
    assert parsed["open_prs"]["total_open"] == 0


def test_build_query_aliases_each_feedstock():
    query = hs.build_health_signals_query(["numpy", "scipy"])
    assert 'r0: repository(owner: "conda-forge", name: "numpy-feedstock")' in query
    assert 'r1: repository(owner: "conda-forge", name: "scipy-feedstock")' in query


def test_store_should_fetch(tmp_path):
    store = hs.HealthSignalStore(tmp_path / "s.json")
    now = datetime.now(timezone.utc)
    assert store.should_fetch("foo", None, False, now)
    store.record("foo", "top", {"open_issues_count": 0})
    assert not store.should_fetch("foo", None, False, now)
    assert store.should_fetch("foo", {"foo"}, False, now)
    assert store.should_fetch("foo", None, True, now)
    assert store.should_fetch("foo", None, False, now + timedelta(days=8))
    store.flush()
    assert "foo" in hs.HealthSignalStore(tmp_path / "s.json").entries


def test_parse_health_signals_archived_repo_keeps_only_the_flag():
    repo = {"isArchived": True, "openPRs": {"totalCount": 5, "nodes": [_pr()]}}
    assert hs.parse_health_signals(repo) == {"archived": True}


def test_build_query_requests_is_archived():
    assert "isArchived" in hs.build_health_signals_query(["numpy"])


def _merged(merged_at, author, merger, approvers=()):
    def actor(login):
        bot = login.endswith("bot") or login == "conda-forge-admin"
        return {"login": login, "__typename": "Bot" if bot else "User"}

    return {
        "mergedAt": merged_at,
        "author": actor(author),
        "mergedBy": actor(merger),
        "reviews": {"nodes": [{"author": actor(a)} for a in approvers]},
    }


def test_parse_merged_prs_separates_any_from_human_involvement():
    merged = {
        "nodes": [
            _merged("2026-03-01T00:00:00Z", "regro-cf-autotick-bot", "conda-forge-admin"),
            _merged("2025-01-01T00:00:00Z", "regro-cf-autotick-bot", "pkgw"),
            _merged("2024-01-01T00:00:00Z", "alice", "alice"),
        ]
    }
    assert hs._parse_merged_prs(merged) == ("2026-03-01T00:00:00Z", "2025-01-01T00:00:00Z")


def test_parse_merged_prs_human_approval_counts_as_human():
    merged = {
        "nodes": [
            _merged("2026-03-01T00:00:00Z", "regro-cf-autotick-bot", "conda-forge-admin", ["bob"])
        ]
    }
    assert hs._parse_merged_prs(merged) == ("2026-03-01T00:00:00Z", "2026-03-01T00:00:00Z")


def test_parse_merged_prs_empty():
    assert hs._parse_merged_prs(None) == (None, None)
    assert hs._parse_merged_prs({"nodes": []}) == (None, None)
