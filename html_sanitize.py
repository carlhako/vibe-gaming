"""html_sanitize — strict allowlist sanitizer for AI-generated answer text.

Unlike a generated game's HTML (which only ever renders inside a sandboxed
<iframe> with no allow-same-origin), an "ask AI about this game" answer
renders directly in the trusted parent document (the info modal). The
game source sent to the model as context is itself AI-generated and can
come from an untrusted requester, so a prompt-injection attempt embedded
in a game's own code could try to steer the model into emitting a
<script>/onerror=/href= payload in its answer. This sanitizer makes that
irrelevant regardless of what the model outputs: only a small allowlist
of formatting tags survives, and every attribute is stripped
unconditionally (no href/src/style/on* ever passes through).

# Exports:
#   sanitize_answer_html(text: str) -> str
"""

from html import escape
from html.parser import HTMLParser

_ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "ul", "ol", "li",
    "table", "thead", "tbody", "tr", "th", "td",
    "h3", "h4", "code", "pre", "blockquote",
}

_VOID_TAGS = {"br"}

# Tags whose content must never survive either, not just the tag itself.
_DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed"}


class _AnswerSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop_depth = 0  # >0 while inside a _DROP_CONTENT_TAGS element

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        if tag in _ALLOWED_TAGS:
            self._out.append(f"<{tag}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self._drop_depth or tag in _DROP_CONTENT_TAGS:
            return
        if tag in _ALLOWED_TAGS:
            self._out.append(f"<{tag}>" if tag in _VOID_TAGS else f"<{tag}></{tag}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP_CONTENT_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth:
            return
        if tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._drop_depth:
            return
        self._out.append(escape(data))

    def handle_entityref(self, name):
        if self._drop_depth:
            return
        self._out.append(f"&{name};")

    def handle_charref(self, name):
        if self._drop_depth:
            return
        self._out.append(f"&#{name};")

    def get_html(self) -> str:
        return "".join(self._out)


def sanitize_answer_html(text: str) -> str:
    """Strip text down to a small allowlist of formatting-only tags with
    every attribute removed unconditionally, so it's safe to set via
    innerHTML in the trusted parent document regardless of what produced
    it. Disallowed tags are unwrapped (their text kept); script/style/
    iframe/object/embed have their contents dropped entirely, not just
    the tag."""
    if not text:
        return ""
    parser = _AnswerSanitizer()
    parser.feed(text)
    parser.close()
    return parser.get_html()
