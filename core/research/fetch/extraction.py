"""
HTML and Document Content Extraction (Phase 1 / Part 3 / Step 4).

Deterministic extraction of structured textual content, metadata (OpenGraph, author,
publication date, language, canonical URL), and page links from fetched HTML/XHTML.
Operates without LLMs or non-deterministic heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any, Optional
import urllib.parse

import bs4
from bs4 import BeautifulSoup, Comment, NavigableString, Tag

logger = logging.getLogger("AutonomOS.Research.HtmlExtractor")

# Tags that represent non-content or interactive UI boilerplate
STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "iframe",
    "button",
    "canvas",
    "object",
    "applet",
    "template",
}


@dataclass
class ExtractedDocument:
    """
    Normalized, structured representation of extracted document content and metadata.
    """
    clean_text: str = ""
    title: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    language: Optional[str] = None
    published_date: Optional[str] = None
    modified_date: Optional[str] = None
    canonical_url: Optional[str] = None
    opengraph: dict[str, str] = field(default_factory=dict)
    links: list[dict[str, str]] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    is_html: bool = True
    content_type: str = "text/html"

    def to_metadata_dict(self) -> dict[str, Any]:
        """Convert extracted metadata into a dictionary suitable for CrawlerReport.metadata."""
        d: dict[str, Any] = {
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "language": self.language,
            "published_date": self.published_date,
            "modified_date": self.modified_date,
            "canonical_url": self.canonical_url,
            "opengraph": self.opengraph,
            "links_count": len(self.links),
            "links": self.links[:100],
            "headings": self.headings,
            "is_html": self.is_html,
            "content_type": self.content_type,
        }
        # Filter out None values for clean presentation
        return {k: v for k, v in d.items() if v is not None}


class HtmlExtractor:
    """
    Deterministic HTML parser and structured content extractor.
    """

    @classmethod
    def extract(
        cls,
        body_text: str,
        base_url: str = "",
        content_type: str = "text/html",
    ) -> ExtractedDocument:
        """
        Extract clean structured text and metadata from raw response body.
        """
        ct_lower = (content_type or "text/html").lower()

        # Non-HTML text handling (JSON, plain text, CSV, markdown)
        if not ("html" in ct_lower or "xml" in ct_lower or ct_lower == ""):
            if ct_lower.startswith("text/") or "json" in ct_lower:
                return ExtractedDocument(
                    clean_text=body_text.strip(),
                    is_html=False,
                    content_type=content_type,
                )
            # Binary or unsupported content type
            return ExtractedDocument(
                clean_text=f"[Binary or unsupported content: {content_type} ({len(body_text)} chars)]",
                is_html=False,
                content_type=content_type,
            )

        if not body_text or not body_text.strip():
            return ExtractedDocument(
                clean_text="",
                is_html=True,
                content_type=content_type,
            )

        # Parse HTML safely
        try:
            soup = BeautifulSoup(body_text, "html.parser")
        except Exception as err:
            logger.warning(f"HTML parsing error, falling back to regex: {err}")
            clean = re.sub(r"<[^>]+>", " ", body_text)
            clean = re.sub(r"\s+", " ", clean).strip()
            return ExtractedDocument(
                clean_text=clean,
                is_html=False,
                content_type=content_type,
            )

        # 1. Extract metadata before stripping tags
        title = cls._extract_title(soup)
        description = cls._extract_description(soup)
        author = cls._extract_author(soup)
        language = cls._extract_language(soup)
        published_date = cls._extract_published_date(soup)
        modified_date = cls._extract_modified_date(soup)
        canonical_url = cls._extract_canonical_url(soup, base_url)
        opengraph = cls._extract_opengraph(soup)
        links = cls._extract_links(soup, base_url)

        # 2. Collect headings
        headings: list[str] = []
        for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
            htext = h.get_text(separator=" ", strip=True)
            if htext:
                headings.append(f"{h.name.upper()}: {htext}")

        # 3. Remove non-content elements and comments
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        for tag_name in STRIP_TAGS:
            for el in soup.find_all(tag_name):
                el.decompose()

        # 4. Extract structured text representation
        target_root = soup.body if (soup.body is not None and len(soup.body.find_all(True)) > 0) else soup
        clean_text = cls._convert_node_to_markdown(target_root)
        clean_text = cls._normalize_whitespace(clean_text)

        return ExtractedDocument(
            clean_text=clean_text,
            title=title,
            description=description,
            author=author,
            language=language,
            published_date=published_date,
            modified_date=modified_date,
            canonical_url=canonical_url,
            opengraph=opengraph,
            links=links,
            headings=headings,
            is_html=True,
            content_type=content_type,
        )

    # -------------------------------------------------------------
    # Metadata Extraction Helpers
    # -------------------------------------------------------------

    @classmethod
    def _extract_title(cls, soup: BeautifulSoup) -> Optional[str]:
        # 1. <title> tag
        title_tag = soup.find("title")
        if title_tag and title_tag.get_text(strip=True):
            return title_tag.get_text(strip=True)

        # 2. <meta property="og:title">
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content", "").strip():
            return og_title["content"].strip()

        # 3. <meta name="twitter:title">
        tw_title = soup.find("meta", attrs={"name": "twitter:title"})
        if tw_title and tw_title.get("content", "").strip():
            return tw_title["content"].strip()

        return None

    @classmethod
    def _extract_description(cls, soup: BeautifulSoup) -> Optional[str]:
        # 1. <meta name="description">
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content", "").strip():
            return meta_desc["content"].strip()

        # 2. <meta property="og:description">
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        if og_desc and og_desc.get("content", "").strip():
            return og_desc["content"].strip()

        # 3. <meta name="twitter:description">
        tw_desc = soup.find("meta", attrs={"name": "twitter:description"})
        if tw_desc and tw_desc.get("content", "").strip():
            return tw_desc["content"].strip()

        return None

    @classmethod
    def _extract_author(cls, soup: BeautifulSoup) -> Optional[str]:
        # 1. <meta name="author">
        author_tag = soup.find("meta", attrs={"name": "author"})
        if author_tag and author_tag.get("content", "").strip():
            return author_tag["content"].strip()

        # 2. <meta property="article:author">
        art_author = soup.find("meta", attrs={"property": "article:author"})
        if art_author and art_author.get("content", "").strip():
            return art_author["content"].strip()

        # 3. <meta name="twitter:creator">
        tw_creator = soup.find("meta", attrs={"name": "twitter:creator"})
        if tw_creator and tw_creator.get("content", "").strip():
            return tw_creator["content"].strip()

        # 4. <a rel="author">
        rel_author = soup.find("a", attrs={"rel": "author"})
        if rel_author and rel_author.get_text(strip=True):
            return rel_author.get_text(strip=True)

        return None

    @classmethod
    def _extract_language(cls, soup: BeautifulSoup) -> Optional[str]:
        # 1. <html lang="...">
        html_tag = soup.find("html")
        if html_tag and html_tag.get("lang", "").strip():
            return html_tag["lang"].strip()

        # 2. <meta http-equiv="content-language">
        http_lang = soup.find("meta", attrs={"http-equiv": re.compile(r"^content-language$", re.I)})
        if http_lang and http_lang.get("content", "").strip():
            return http_lang["content"].strip()

        # 3. <meta name="language">
        meta_lang = soup.find("meta", attrs={"name": "language"})
        if meta_lang and meta_lang.get("content", "").strip():
            return meta_lang["content"].strip()

        return None

    @classmethod
    def _extract_published_date(cls, soup: BeautifulSoup) -> Optional[str]:
        # Meta candidate tags
        pub_keys = [
            ("property", "article:published_time"),
            ("name", "date"),
            ("name", "pubdate"),
            ("name", "publishdate"),
            ("name", "dc.date"),
            ("name", "dc.date.issued"),
            ("name", "sailthru.date"),
        ]
        for attr_name, attr_val in pub_keys:
            tag = soup.find("meta", attrs={attr_name: attr_val})
            if tag and tag.get("content", "").strip():
                return tag["content"].strip()

        # <time datetime="..." class="...published...">
        time_tag = soup.find("time", attrs={"datetime": True})
        if time_tag and time_tag.get("datetime", "").strip():
            classes = time_tag.get("class", [])
            class_str = " ".join(classes) if isinstance(classes, list) else str(classes)
            if any(k in class_str.lower() for k in ["pub", "date", "entry", "post", "time"]):
                return time_tag["datetime"].strip()

        return None

    @classmethod
    def _extract_modified_date(cls, soup: BeautifulSoup) -> Optional[str]:
        mod_keys = [
            ("property", "article:modified_time"),
            ("name", "last-modified"),
            ("name", "revised"),
            ("name", "dc.date.modified"),
        ]
        for attr_name, attr_val in mod_keys:
            tag = soup.find("meta", attrs={attr_name: attr_val})
            if tag and tag.get("content", "").strip():
                return tag["content"].strip()

        return None

    @classmethod
    def _extract_canonical_url(cls, soup: BeautifulSoup, base_url: str) -> Optional[str]:
        link_tag = soup.find("link", attrs={"rel": lambda r: r and "canonical" in (r if isinstance(r, list) else [r])})
        if link_tag and link_tag.get("href", "").strip():
            raw_href = link_tag["href"].strip()
            if base_url:
                return urllib.parse.urljoin(base_url, raw_href)
            return raw_href
        return None

    @classmethod
    def _extract_opengraph(cls, soup: BeautifulSoup) -> dict[str, str]:
        og: dict[str, str] = {}
        for tag in soup.find_all("meta"):
            prop = tag.get("property") or tag.get("name")
            content = tag.get("content")
            if prop and content and prop.lower().startswith("og:"):
                og[prop.lower()] = content.strip()
        return og

    @classmethod
    def _extract_links(cls, soup: BeautifulSoup, base_url: str) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        seen_urls: set[str] = set()

        for a in soup.find_all("a", href=True):
            raw_href = a["href"].strip()
            if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue

            resolved_url = urllib.parse.urljoin(base_url, raw_href) if base_url else raw_href
            if resolved_url in seen_urls:
                continue

            anchor_text = a.get_text(separator=" ", strip=True)
            seen_urls.add(resolved_url)
            links.append({"url": resolved_url, "text": anchor_text})

        return links

    # -------------------------------------------------------------
    # Structured Markdown Conversion
    # -------------------------------------------------------------

    @classmethod
    def _convert_node_to_markdown(cls, node: Tag | NavigableString) -> str:
        if isinstance(node, NavigableString):
            return str(node)

        if not isinstance(node, Tag):
            return ""

        tag_name = node.name.lower()

        # Head / Metadata tags
        if tag_name in ("head", "meta", "link", "title"):
            if node.find(["h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "ul", "ol", "table", "section", "article"]):
                return cls._render_children(node)
            return ""

        # Headings
        if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag_name[1])
            inner = cls._render_children(node).strip()
            if inner:
                return f"\n\n{'#' * level} {inner}\n\n"
            return ""

        # Paragraphs & Containers
        if tag_name in ("p", "div", "section", "article", "main"):
            inner = cls._render_children(node).strip()
            if inner:
                return f"\n\n{inner}\n\n"
            return ""

        # Blockquote
        if tag_name == "blockquote":
            inner = cls._render_children(node).strip()
            if inner:
                lines = [f"> {line}" for line in inner.split("\n")]
                return f"\n\n" + "\n".join(lines) + "\n\n"
            return ""

        # Code block
        if tag_name == "pre":
            code_tag = node.find("code")
            code_text = code_tag.get_text() if code_tag else node.get_text()
            return f"\n\n```\n{code_text.strip()}\n```\n\n"

        if tag_name == "code":
            inner = cls._render_children(node).strip()
            return f"`{inner}`" if inner else ""

        # Lists
        if tag_name == "ul":
            items = []
            for li in node.find_all("li", recursive=False):
                item_text = cls._render_children(li).strip()
                if item_text:
                    items.append(f"* {item_text}")
            return "\n\n" + "\n".join(items) + "\n\n" if items else ""

        if tag_name == "ol":
            items = []
            for i, li in enumerate(node.find_all("li", recursive=False), 1):
                item_text = cls._render_children(li).strip()
                if item_text:
                    items.append(f"{i}. {item_text}")
            return "\n\n" + "\n".join(items) + "\n\n" if items else ""

        if tag_name == "li":
            return cls._render_children(node)

        # Tables
        if tag_name == "table":
            return cls._render_table(node)

        # Links
        if tag_name == "a":
            href = node.get("href", "").strip()
            anchor_text = cls._render_children(node).strip()
            if href and anchor_text and not href.startswith(("javascript:", "mailto:")):
                return f"[{anchor_text}]({href})"
            return anchor_text

        # Formatting
        if tag_name in ("strong", "b"):
            inner = cls._render_children(node).strip()
            return f"**{inner}**" if inner else ""

        if tag_name in ("em", "i"):
            inner = cls._render_children(node).strip()
            return f"*{inner}*" if inner else ""

        if tag_name == "br":
            return "\n"

        if tag_name == "hr":
            return "\n\n---\n\n"

        # Default: render children
        return cls._render_children(node)

    @classmethod
    def _render_children(cls, node: Tag) -> str:
        parts = []
        for child in node.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif isinstance(child, Tag):
                parts.append(cls._convert_node_to_markdown(child))
        return "".join(parts)

    @classmethod
    def _render_table(cls, table_node: Tag) -> str:
        rows: list[list[str]] = []
        for tr in table_node.find_all("tr"):
            row_cells = []
            for cell in tr.find_all(["th", "td"]):
                cell_text = cell.get_text(separator=" ", strip=True).replace("|", "\\|")
                row_cells.append(cell_text)
            if row_cells:
                rows.append(row_cells)

        if not rows:
            return ""

        max_cols = max(len(r) for r in rows)
        # Pad shorter rows
        for r in rows:
            while len(r) < max_cols:
                r.append("")

        table_lines = []
        # Header row
        header = rows[0]
        table_lines.append("| " + " | ".join(header) + " |")
        table_lines.append("| " + " | ".join(["---"] * max_cols) + " |")

        for r in rows[1:]:
            table_lines.append("| " + " | ".join(r) + " |")

        return "\n\n" + "\n".join(table_lines) + "\n\n"

    @classmethod
    def _normalize_whitespace(cls, text: str) -> str:
        if not text:
            return ""
        # Collapse multiple spaces on a single line
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
        # Join lines and collapse 3+ newlines to 2
        joined = "\n".join(lines)
        joined = re.sub(r"\n{3,}", "\n\n", joined)
        return joined.strip()
