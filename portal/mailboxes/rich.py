"""Keep only inert formatting and ordinary links in outgoing HTML email."""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

TAGS = frozenset(('b', 'strong', 'i', 'em', 'u', 's', 'strike', 'del', 'p', 'div', 'br',
                  'ul', 'ol', 'li', 'blockquote', 'a'))
SKIP = frozenset(('script', 'style', 'iframe', 'object', 'template'))


def ordinary_link(value):
    if not isinstance(value, str) or any(ord(char) < 32 for char in value):
        return False
    try:
        url = urlsplit(value)
    except ValueError:
        return False
    return url.scheme in ('http', 'https') and bool(url.netloc) or url.scheme == 'mailto' and bool(url.path)


class MailMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.open_tags, self.hidden = [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.hidden.append(tag)
        if self.hidden or tag not in TAGS:
            return
        if tag == 'a':
            href = dict(attrs).get('href')
            if not ordinary_link(href):
                return
            self.parts.append('<a href="' + escape(href, quote=True) + '">')
        else:
            self.parts.append(f'<{tag}>')
        if tag != 'br':
            self.open_tags.append(tag)

    def handle_endtag(self, tag):
        if self.hidden:
            if self.hidden[-1] == tag:
                self.hidden.pop()
        elif tag in self.open_tags:
            current = None
            while current != tag:
                current = self.open_tags.pop()
                self.parts.append(f'</{current}>')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(escape(data))

    def render(self):
        self.parts.extend(f'</{tag}>' for tag in reversed(self.open_tags))
        return ''.join(self.parts)


def safe_mail_markup(value):
    if not value:
        return ''
    parser = MailMarkup()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return ''
    return parser.render()
