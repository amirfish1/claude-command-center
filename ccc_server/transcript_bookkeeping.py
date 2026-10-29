"""Recognize compaction records without treating them as new conversation turns."""


def is_compaction_bookkeeping(event):
    if event.get("isCompactSummary") or event.get("bookkeeping"):
        return True
    message = event.get("message")
    content = message.get("content", "") if isinstance(message, dict) else event.get("text", "")
    if isinstance(content, list):
        content = "\n".join(str(b.get("text") or "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
    text = str(content or "").lstrip()
    return (text.startswith(("This session is being continued from a previous conversation",
                             "<local-command-caveat>", "<local-command-stdout>",
                             "<local-command-stderr>"))
            or text == "/compact" or text.startswith("/compact ")
            or (text.startswith(("<command-name>", "<command-message>"))
                and "<command-name>/compact</command-name>" in text))
