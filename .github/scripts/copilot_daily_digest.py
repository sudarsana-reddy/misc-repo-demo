#!/usr/bin/env python3

import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
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

    with open(OUTPUT_PATH, "a", encoding="utf-8") as fh:
        fh.write(f"has_updates={has_updates}\n")
        fh.write("email_body<<'EOF'\n")
        fh.write(f"{body}\n")
        fh.write("EOF\n")


def clean_html(raw: str) -> str:
    return re.sub(r"<[^>]+>", "", raw or "").replace("&nbsp;", " ").strip()


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
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")
    return items


def collect_yesterday_matches(items):
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
    matches = []

    for node in items:
        if node.tag.endswith("item"):
            title = node.findtext("title", default="Untitled")
            link = node.findtext("link", default="")
            pub = node.findtext("pubDate", default="")
            description = clean_html(node.findtext("description", default=""))
        else:
            ns = "{http://www.w3.org/2005/Atom}"
            title = node.findtext(f"{ns}title", default="Untitled")
            link = ""
            for link_node in node.findall(f"{ns}link"):
                if link_node.get("href"):
                    link = link_node.get("href", "")
                    break
            pub = node.findtext(f"{ns}published", default="") or node.findtext(f"{ns}updated", default="")
            summary = node.find(f"{ns}summary")
            description = clean_html(summary.text if summary is not None and summary.text else "")

        if not pub:
            continue

        try:
            published_at = parsedate_to_datetime(pub)
        except Exception:
            continue

        if published_at.date() != yesterday:
            continue

        matches.append(
            {
                "title": str(title or "Untitled").strip(),
                "link": str(link or "").strip(),
                "published": published_at.strftime("%Y-%m-%d %H:%M UTC"),
                "description": description[:2000],
            }
        )

    return matches


def build_email_body(matches):
    if not matches:
        return ""

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    lines = [f"GitHub Copilot updates for {yesterday}", ""]
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
        matches = collect_yesterday_matches(items)
    except Exception:
        write_result("false", "")
        return 0

    if matches:
        body = build_email_body(matches)
        write_result("true", body)
        print(body)
    else:
        write_result("false", "")
        print("No new GitHub Copilot changes or updates were published yesterday.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
