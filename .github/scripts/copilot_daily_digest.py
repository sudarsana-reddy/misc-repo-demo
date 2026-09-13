#!/usr/bin/env python3

import os
import re
import sys
import uuid
import urllib.request
import xml.etree.ElementTree as ET
import html as _html
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

FEED_URL = os.environ.get("FEED_URL", "https://github.blog/changelog/label/copilot/feed/")
OUTPUT_PATH = os.environ.get("GITHUB_OUTPUT")


def write_result(has_updates: str, body: str) -> None:
    if not OUTPUT_PATH:
        print(f"has_updates={has_updates}")
        if body:
            print(body)
        return

    # Use a unique delimiter so the body cannot accidentally close the heredoc
    delim = f"DELIM_{uuid.uuid4().hex}"
    with open(OUTPUT_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"has_updates={has_updates}\n")
        fh.write(f"email_body<<{delim}\n")
        if body:
            fh.write(body)
            if not body.endswith("\n"):
                fh.write("\n")
        fh.write(f"{delim}\n")


def clean_html(raw: str) -> str:
    """Turn an HTML fragment into plain text.

    - Unescape HTML entities
    - Remove script/style blocks and HTML comments
    - Treat <br> and <p> as line breaks
    - Strip remaining tags and collapse whitespace
    """
    if not raw:
        return ""

    # Unescape common HTML entities first
    text = _html.unescape(raw)

    # Remove script/style blocks and their content
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)

    # Remove HTML comments
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)

    # Normalize some block-level tags to newlines so paragraphs/lines are preserved
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*/?p\s*>", "\n", text)

    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)

    # Collapse multiple newlines to a maximum of two, and multiple spaces to one
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)

    # Trim each line and remove leading/trailing whitespace
    lines = [ln.strip() for ln in text.splitlines()]
    final = "\n".join([ln for ln in lines if ln])

    return final.strip()


def strip_github_blog_footer(text: str) -> str:
    """Remove trailing "first on The GitHub Blog" footer (and common variants).

    This strips trailing punctuation/dashes and is case-insensitive.
    """
    if not text:
        return text
    return re.sub(r"(?i)\s*[-–—:]*\s*first on the github blog\.?\s*$", "", text).strip()


def fetch_feed() -> bytes:
    try:
        with urllib.request.urlopen(FEED_URL, timeout=30) as response:
            return response.read()
    except Exception as exc:
        print(f"Failed to fetch feed: {exc}")
        raise


def parse_items(xml_bytes: bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        print(f"Failed to parse feed XML: {exc}")
        raise

    items = root.findall("./channel/item")
    if not items:
        # try Atom entries
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    return items


def parse_published(pub: str) -> datetime | None:
    if not pub:
        return None

    # Try RFC-2822 parser first (RSS pubDate)
    try:
        dt = parsedate_to_datetime(pub)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Try ISO 8601 variants (Atom)
    try:
        # datetime.fromisoformat doesn't accept a trailing 'Z', replace with +00:00
        iso = pub.strip()
        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # As a last resort, try to parse a common subset with regex (YYYY-MM-DD)
    m = re.search(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?", pub)
    if m:
        part = m.group(0)
        try:
            if part.endswith("Z"):
                part = part[:-1] + "+00:00"
            dt = datetime.fromisoformat(part)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    return None


def collect_last_two_days_matches(items):
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    day_before_yesterday = today - timedelta(days=2)
    matches = []

    for node in items:
        if node.tag.endswith("item"):
            title = node.findtext("title", default="Untitled")
            link = node.findtext("link", default="")
            pub = node.findtext("pubDate", default="")
            raw_desc = node.findtext("description", default="")
            description = clean_html(raw_desc)
        else:
            ns = "{http://www.w3.org/2005/Atom}"
            title = node.findtext(f"{ns}title", default="Untitled")
            link = ""
            for link_node in node.findall(f"{ns}link"):
                href = link_node.get("href")
                rel = link_node.get("rel") or ""
                # prefer alternate or first href
                if href and (rel == "alternate" or not link):
                    link = href
            pub = node.findtext(f"{ns}published", default="") or node.findtext(f"{ns}updated", default="")
            # Prefer summary, fall back to content
            summary = node.find(f"{ns}summary")
            content = node.find(f"{ns}content")
            raw_desc = ""
            if summary is not None and (summary.text or len(summary)):
                raw_desc = ET.tostring(summary, encoding="unicode", method="xml")
            elif content is not None and (content.text or len(content)):
                raw_desc = ET.tostring(content, encoding="unicode", method="xml")
            description = clean_html(raw_desc)

        if not pub:
            continue

        published_at = parse_published(pub)
        if not published_at:
            continue

        # Check if published date is within the last 2 days (yesterday or day before yesterday)
        if published_at.date() not in (yesterday, day_before_yesterday):
            continue

        # Keep description reasonably short
        desc_short = (description or "").strip()
        if len(desc_short) > 2000:
            desc_short = desc_short[:2000].rsplit(" ", 1)[0] + "..."

        # Remove trailing "first on The GitHub Blog" footer from title and description
        title_clean = strip_github_blog_footer(str(title or "Untitled").strip())
        desc_clean = strip_github_blog_footer(desc_short)

        matches.append(
            {
                "title": title_clean,
                "link": str(link or "").strip(),
                "published": published_at.strftime("%Y-%m-%d %H:%M UTC"),
                "description": desc_clean,
            }
        )

    return matches


def build_email_body(matches):
    if not matches:
        return ""

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    day_before_yesterday = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    lines = [f"GitHub Copilot updates for {day_before_yesterday} and {yesterday}", ""]
    for item in matches:
        lines.append(f"- {item['title']}")
        lines.append(f"  Published: {item['published']}")
        if item["link"]:
            lines.append(f"  Link: {item['link']}")
        if item["description"]:
            lines.append(f"  Details: {item['description']}")
        lines.append("")
    return "\n".join(lines).strip()


def main() -> int:
    try:
        xml_bytes = fetch_feed()
        items = parse_items(xml_bytes)
        matches = collect_last_two_days_matches(items)
    except Exception:
        write_result("false", "")
        return 0

    if matches:
        body = build_email_body(matches)
        write_result("true", body)
        print(body)
    else:
        write_result("false", "")
        print("No new GitHub Copilot changes or updates were published in the last 2 days.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
