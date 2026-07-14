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


def test_archive_shaping_carries_trashed_state_into_tri_state_buckets():
    start = APP_JS.index("const shaped = rows.map(c => {")
    body = APP_JS[start:APP_JS.index("}).map(_applyLiveOverlayToRow);", start)]

    assert body.count("trashed: !!c.trashed,") >= 2


def test_archived_group_chats_follow_the_same_buckets():
    assert "_archivedGroupChatsForRender.filter(gc => !!gc.trashed)" in APP_JS
    assert "_archivedGroupChatsForRender.filter(gc => !gc.trashed)" in APP_JS


def test_session_trash_handler_calls_additive_endpoint():
    assert "+ '/trash'" in APP_JS
    assert "trashed: wantTrashed" in APP_JS
    assert "{ archived: !!data.archived, trashed: !!data.trashed }" in APP_JS


def test_archive_buttons_carry_explicit_desired_state():
    assert 'data-archived="true"' in APP_JS
    assert 'data-archived="false"' in APP_JS
    assert "btn.dataset.archived === 'true'" in APP_JS


def test_group_chats_have_distinct_trash_and_untrash_calls():
    assert "fetch('/api/group-chats/trash'" in APP_JS
    assert "fetch('/api/group-chats/untrash'" in APP_JS


def test_all_tab_group_chat_trash_action_uses_trash_glyph_and_accessible_labels():
    start = APP_JS.index("const _allChatHtml =")
    all_chat = APP_JS[start:APP_JS.index("return {", start)]
    assert "replace(" not in all_chat
    assert "_groupChatLifecycleAction('ingroupchat-trash', 'Move to Trash', '&#128465;')" in all_chat
    action_start = APP_JS.index("const _groupChatLifecycleAction =")
    action_builder = APP_JS[action_start:APP_JS.index("const _chatHtml =", action_start)]
    assert "' data-role=\"' + role + '\"'" in action_builder
    assert "' title=\"' + label + '\" aria-label=\"' + label + '\">'" in action_builder

    start = APP_JS.index("const lifecycleButtons = lifecycleContext === 'trash'")
    archived_gc = APP_JS[
        start:APP_JS.index("return '<div class=\"conv-item conv-item-archived-gc\"", start)
    ]
    assert 'title="Untrash to Archived" aria-label="Untrash to Archived"' in archived_gc
    assert 'title="Move to Active" aria-label="Move to Active"' in archived_gc
    assert 'title="Move to Trash" aria-label="Move to Trash"' in archived_gc
