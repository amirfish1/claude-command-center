from __future__ import annotations

from unittest import mock

import server


def test_github_issue_title_uses_issue_title_for_github_issue_url():
    response = mock.Mock(returncode=0, stdout='{"title":"Actual issue title"}', stderr="")
    with mock.patch.object(server.subprocess, "run", return_value=response) as run:
        assert server.github_issue_title("https://github.com/owner/repo/issues/57") == "Actual issue title"

    assert run.call_args.args[0] == [
        "gh", "issue", "view", "57", "--repo", "owner/repo", "--json", "title",
    ]


def test_github_issue_title_ignores_non_issue_urls():
    with mock.patch.object(server.subprocess, "run") as run:
        assert server.github_issue_title("https://github.com/owner/repo/pull/57") == ""

    run.assert_not_called()


def test_annotation_queue_uses_linked_github_issue_title():
    queued = {}

    class Queue:
        def enqueue(self, **kwargs):
            queued.update(kwargs)
            return {"number": 1, "project": "WT", "ref": "WT-1"}

    with mock.patch.object(server, "_q", Queue()), mock.patch.object(
        server, "github_issue_title", return_value="Actual issue title"
    ):
        result = server.enqueue_annotation_ux_fixes_queue(
            "Please fix this issue",
            meta={
                "url": "https://github.com/owner/repo/issues/57",
                "title": "problem",
            },
        )

    assert result["ok"]
    assert queued["title"] == "Actual issue title"
