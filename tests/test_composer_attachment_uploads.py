"""Behavioral guards for the session-composer attachment flow."""

import json
import pathlib
import subprocess
import textwrap


PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = PROJECT_ROOT / "static" / "app.js"


def _run_upload_helpers(expression):
    program = textwrap.dedent(
        """
        const fs = require('fs');
        const source = fs.readFileSync(process.argv[1], 'utf8');
        const extract = (name) => {
          const start = source.indexOf('function ' + name + '(');
          if (start < 0) throw new Error('missing helper: ' + name);
          const brace = source.indexOf('{', start);
          let depth = 0;
          for (let index = brace; index < source.length; index++) {
            if (source[index] === '{') depth++;
            if (source[index] === '}' && --depth === 0) return source.slice(start, index + 1);
          }
          throw new Error('unterminated helper: ' + name);
        };
        const load = (name) => {
          if (!source.includes('function ' + name + '(')) return;
          eval(extract(name) + '\\nglobalThis[' + JSON.stringify(name) + '] = ' + name + ';');
        };
        ['beginComposerUpload', 'finishComposerUpload', 'composerUploadIsPending',
         'guardComposerSend', 'shouldUsePastedImageUpload',
         'attachmentNameForClipboardImage', 'insertAtCursor'].forEach(load);
        process.stdout.write(JSON.stringify(%s));
        """
    ) % expression
    completed = subprocess.run(
        ["node", "-e", program, str(APP_JS)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_pending_composer_upload_blocks_until_every_file_settles():
    result = _run_upload_helpers(
        "(() => { const composer = {}; beginComposerUpload(composer); "
        "beginComposerUpload(composer); const during = composerUploadIsPending(composer); "
        "finishComposerUpload(composer); const oneLeft = composerUploadIsPending(composer); "
        "finishComposerUpload(composer); return { during, oneLeft, after: composerUploadIsPending(composer) }; })()"
    )
    assert result == {"during": True, "oneLeft": True, "after": False}


def test_only_previewable_images_use_the_pasted_image_upload_path():
    result = _run_upload_helpers(
        "['image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml', 'image/heic', ''].map(type => shouldUsePastedImageUpload({ type }))"
    )
    assert result == [True, True, True, True, False, False, False]


def test_send_handler_refuses_to_clear_a_composer_with_uploads_in_flight():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("async function sendToTerminal")
    end = source.index("function insertPendingSpawnCard", start)
    send_handler = source[start:end]

    assert "if (!guardComposerSend($input)) return;" in send_handler
    assert "Waiting for attachment upload to finish." in source


def test_send_guard_blocks_a_pending_composer_without_consuming_its_text():
    result = _run_upload_helpers(
        "(() => { const composer = { value: ' [uploading photo.jpg...] ' }; "
        "const toasts = []; global.showOpToast = (message, kind) => toasts.push({ message, kind }); "
        "beginComposerUpload(composer); const allowed = guardComposerSend(composer); "
        "return { allowed, value: composer.value, toasts }; })()"
    )
    assert result == {
        "allowed": False,
        "value": " [uploading photo.jpg...] ",
        "toasts": [{"message": "Waiting for attachment upload to finish.", "kind": "info"}],
    }


def test_clipboard_and_split_pane_paths_share_the_upload_send_guards():
    source = APP_JS.read_text(encoding="utf-8")
    paste_start = source.index("function attachImagePaste")
    paste_end = source.index("[document.getElementById('nsmBody')", paste_start)
    paste_handler = source[paste_start:paste_end]
    split_start = source.index("async function sendToSplitTerminal")
    split_end = source.index("function cpInputAutoResize", split_start)
    split_handler = source[split_start:split_end]

    assert "beginComposerUpload(el);" in paste_handler
    assert "finishComposerUpload(el);" in paste_handler
    assert "shouldUsePastedImageUpload(blob)" in paste_handler
    assert "if (!guardComposerSend($cpInput)) return;" in split_handler


def test_every_attachment_enabled_submitter_checks_the_shared_upload_guard():
    source = APP_JS.read_text(encoding="utf-8")

    def section(start_marker, end_marker):
        start = source.index(start_marker)
        return source[start:source.index(end_marker, start)]

    assert "if (!guardComposerSend($input)) return;" in section(
        "async function submitPlus", "async function sendToTerminal"
    )
    assert "if (!guardComposerSend(input)) return;" in section(
        "async function sendHumanGcPost", "function closeGroupChatReader"
    )
    assert "if (!guardComposerSend(textarea)) return;" in section(
        "const submit = () =>", "function onKey(ev)"
    )
    assert "if (!guardComposerSend(textArea)) return;" in section(
        "function annShowUxFixesPreview", "async function annOpenUxFixesQueue"
    )

    annotation_saves = source.count("if (!guardComposerSend(noteEl)) return null;")
    assert annotation_saves == 2


def test_group_chat_keeps_the_attachment_cleanup_value_hook():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("if (gcHumanInput) {")
    end = source.index("_autosizeGc();", start)
    group_chat_setup = source[start:end]

    assert "const ownDesc = Object.getOwnPropertyDescriptor(gcHumanInput, 'value');" in group_chat_setup
    assert "const desc = ownDesc || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value');" in group_chat_setup


def test_upload_placeholders_do_not_emit_input_or_persist_as_drafts():
    result = _run_upload_helpers(
        "(() => { const events = []; const composer = { tagName: 'TEXTAREA', value: '', selectionStart: 0, selectionEnd: 0, "
        "dispatchEvent: event => events.push(event.type) }; insertAtCursor(composer, ' [uploading image...] ', false); "
        "return { value: composer.value, events }; })()"
    )
    assert result == {"value": " [uploading image...] ", "events": []}


def test_kanban_handoff_and_f2_continuation_wait_for_attachment_uploads():
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count("insertAtCursor(el, placeholder, false);") == 2

    handoff_start = source.index("function routeToNewSession")
    handoff_end = source.index("$kptNewSession.addEventListener('focus'", handoff_start)
    handoff = source[handoff_start:handoff_end]
    assert "if (!guardComposerSend($kptNewSession)) return;" in handoff

    f2_start = source.index("async function f2RunContinue")
    f2_end = source.index("function f2GateStateForPane", f2_start)
    f2_handler = source[f2_start:f2_end]
    assert "if (!guardComposerSend(input)) return;" in f2_handler


def test_unnamed_clipboard_image_attachments_keep_a_usable_extension():
    result = _run_upload_helpers(
        "['image/svg+xml', 'image/heic', 'image/bmp', ''].map(type => attachmentNameForClipboardImage({ type }))"
    )
    assert result == ["pasted-image.svg", "pasted-image.heic", "pasted-image.bmp", "pasted-image"]


def test_clipboard_upload_failure_notifies_the_composer_after_replacing_placeholder():
    source = APP_JS.read_text(encoding="utf-8")
    start = source.index("function attachImagePaste")
    end = source.index("[document.getElementById('nsmBody')", start)
    paste_handler = source[start:end]
    assert "el.value = el.value.replace(placeholder, ' [upload failed: ' + e.message + '] ');\n        el.dispatchEvent(new Event('input', { bubbles: true }));" in paste_handler
