"""Render flexible preview HTML in a sandbox without granting application access."""

import html
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

from runner.preview import DESIGN_CSS

TAGS = set("article section main header footer nav aside div span p h1 h2 h3 h4 h5 h6 ul ol li dl dt dd a strong em b i s small mark time br hr pre code blockquote figure figcaption img video source table thead tbody tfoot tr th td caption colgroup col details summary input label button style sub sup kbd samp abbr".split())
TAGS.update("svg g path rect circle ellipse line polyline polygon text tspan defs marker lineargradient radialgradient stop title desc".split())
VOID = {"br", "hr", "img", "source", "input", "col"}
DROP = {"script", "iframe", "object", "embed", "template", "noscript", "foreignobject", "math"}
ATTRS = {"class", "id", "style", "title", "role", "tabindex", "alt", "width", "height", "controls", "preload", "playsinline", "loop", "muted", "colspan", "rowspan", "scope", "open", "for", "name", "value", "checked", "disabled", "datetime", "start"}
ATTRS.update("viewbox x y x1 x2 y1 y2 cx cy r rx ry d points fill stroke stroke-width stroke-linecap stroke-linejoin stroke-dasharray opacity transform text-anchor dominant-baseline marker-end marker-start markerwidth markerheight refx refy orient offset stop-color stop-opacity".split())


class Content(HTMLParser):
    def __init__(self, ids):
        super().__init__(convert_charrefs=True)
        self.ids, self.parts, self.dropped, self.in_style = set(ids), [], [], False

    def handle_starttag(self, tag, attrs):
        if self.dropped or tag in DROP:
            if tag not in VOID:
                self.dropped.append(tag)
            return
        if tag not in TAGS:
            return
        attributes = []
        for key, value in attrs:
            value = value or ""
            if key in {"src", "poster"}:
                aid = value.removeprefix("artifact:")
                if tag in {"img", "video", "source"} and value.startswith("artifact:") and aid in self.ids:
                    attributes.append((key, f"{_origin()}/api/artifacts/{quote(aid, safe='')}"))
            elif key == "href" and tag == "a":
                if value.startswith("#"):
                    attributes.append((key, value))
                elif urlsplit(value).scheme in {"https", "http"}:
                    attributes.extend([("href", value), ("target", "_blank"), ("rel", "noopener noreferrer")])
            elif key == "type" and tag == "input":
                if value in {"radio", "checkbox"}:
                    attributes.append((key, value))
            elif key in ATTRS or key.startswith("aria-") or key.startswith("data-"):
                attributes.append((key, value))
        if tag == "input" and not any(k == "type" for k, v in attributes):
            return  # Previews have controls, never credential/text-entry forms.
        self.parts.append("<" + tag + "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in attributes) + ">")
        self.in_style = tag == "style"

    def handle_endtag(self, tag):
        if self.dropped:
            if tag in self.dropped:
                del self.dropped[self.dropped.index(tag):]
            return
        if tag in TAGS and tag not in VOID:
            self.parts.append(f"</{tag}>")
        if tag == "style":
            self.in_style = False

    def handle_data(self, data):
        if not self.dropped:
            self.parts.append(data if self.in_style else html.escape(data))


def _origin():
    from .settings import settings
    return settings().public_url.rstrip("/")


def render(document):
    parsed = Content(document.get("artifact_ids", []))
    parsed.feed(document["html"])
    body = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    body += f"<title>{html.escape(document['title'])}</title><style>{DESIGN_CSS}</style></head><body>"
    body += "".join(parsed.parts) + "</body></html>"
    sources = " ".join(f"{_origin()}/api/artifacts/{quote(aid, safe='')}" for aid in parsed.ids) or "'none'"
    policy = ("default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
              f"img-src {sources}; media-src {sources}; "
              "base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox allow-same-origin allow-popups allow-popups-to-escape-sandbox")
    return body, {"Content-Security-Policy": policy, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "SAMEORIGIN"}
