from pathlib import Path


APP_JS = Path("static/app.js").read_text()


def test_renderer_has_explicit_lifecycle_contexts():
    for context in ("active", "all-main", "trash"):
        assert f"'{context}'" in APP_JS


def test_all_active_rows_trash_without_archive_action():
    assert "lifecycleContext === 'all-main'" in APP_JS
    assert 'data-role="trash"' in APP_JS


def test_trash_rows_only_untrash_and_never_pin():
    assert "lifecycleContext !== 'trash'" in APP_JS
    assert 'data-role="untrash"' in APP_JS


def test_archived_rows_do_not_render_duplicate_rest_restore():
    assert "archivedRestoreRestHtml" not in APP_JS


def test_trash_bucket_depends_only_on_trashed_state():
    assert "const _trashConvs = _archivedConvs.filter(c => !!c.trashed);" in APP_JS
    assert "const _mainArchivedConvs = _archivedConvs.filter(c => !c.trashed);" in APP_JS
    assert "_archivedConvs.filter(c => !c.pinned" not in APP_JS


def test_archived_group_chats_follow_the_same_buckets():
    assert "_archivedGroupChatsForRender.filter(gc => !!gc.trashed)" in APP_JS
    assert "_archivedGroupChatsForRender.filter(gc => !gc.trashed)" in APP_JS
