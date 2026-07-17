"""
Context-aware markdown chunker for the renovation company knowledge base.

Why not a generic fixed-size splitter?
- The FAQ doc is a series of self-contained Q&A pairs -> each pair should be ONE chunk.
- The Services menu has 3 tiers, each a coherent unit, plus a comparison table that
  deserves to be its own retrievable chunk.
- The company info doc is short -> split by ## sections.

Every chunk gets a contextual header prepended before embedding (Anthropic's
"contextual retrieval" trick) so the embedding never floats without knowing
what document/section it belongs to.
"""

import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    text: str                    # the raw chunk content (for showing to the LLM at answer time)
    embed_text: str              # text + contextual prefix (what actually gets embedded)
    metadata: dict = field(default_factory=dict)


DOC_TITLES = {
    "client-faqs-home-remodeling.md": "Client FAQs — Home Remodeling",
    "general-company-information.md": "General Company Information",
    "Services-and-Packages-Menu.md": "Services & Packages Menu",
}


def _prefix(doc_title: str, section: str, extra: str = "") -> str:
    header = f"Document: {doc_title}\nSection: {section}"
    if extra:
        header += f"\n{extra}"
    return header


def chunk_faq(text: str, filename: str) -> list[Chunk]:
    """Split into one chunk per numbered Q&A pair (### headers)."""
    doc_title = DOC_TITLES[filename]
    chunks = []

    # Split on '### N. Question text' headers
    parts = re.split(r"(?m)^### (\d+\.\s*.+)$", text)
    # parts[0] is intro text before first header; then alternating (header, body)
    intro = parts[0].strip()
    if intro and len(intro) > 40:  # skip trivial title-only intro
        chunks.append(Chunk(
            text=intro,
            embed_text=_prefix(doc_title, "Introduction") + "\n\n" + intro,
            metadata={"source_doc": filename, "doc_type": "faq", "section_title": "Introduction"},
        ))

    for i in range(1, len(parts), 2):
        question = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        body = body.rstrip("-").strip()  # trim trailing '---' separators
        full_text = f"### {question}\n\n{body}"
        chunks.append(Chunk(
            text=full_text,
            embed_text=_prefix(doc_title, question) + "\n\n" + full_text,
            metadata={
                "source_doc": filename,
                "doc_type": "faq",
                "section_title": question,
            },
        ))
    return chunks


def chunk_company_info(text: str, filename: str) -> list[Chunk]:
    """Split into one chunk per ## section."""
    doc_title = DOC_TITLES[filename]
    chunks = []

    parts = re.split(r"(?m)^## (.+)$", text)
    intro = parts[0].strip()
    if intro:
        # drop the leading '# General Company Information' title line if present
        intro = re.sub(r"(?m)^# .+\n?", "", intro).strip()
    if intro:
        chunks.append(Chunk(
            text=intro,
            embed_text=_prefix(doc_title, "Introduction") + "\n\n" + intro,
            metadata={"source_doc": filename, "doc_type": "company_info", "section_title": "Introduction"},
        ))

    for i in range(1, len(parts), 2):
        section_title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        full_text = f"## {section_title}\n\n{body}"
        chunks.append(Chunk(
            text=full_text,
            embed_text=_prefix(doc_title, section_title) + "\n\n" + full_text,
            metadata={"source_doc": filename, "doc_type": "company_info", "section_title": section_title},
        ))
    return chunks


def chunk_services(text: str, filename: str) -> list[Chunk]:
    """
    One chunk per tier (## header), the comparison table as its own chunk,
    and the closing 'Ready to Begin' CTA as its own chunk.
    """
    doc_title = DOC_TITLES[filename]
    chunks = []

    # Split on ## headers (tiers + comparison + CTA all use ##)
    parts = re.split(r"(?m)^## (.+)$", text)
    intro = parts[0].strip()
    intro = re.sub(r"(?m)^# .+\n?", "", intro).strip()
    if intro:
        chunks.append(Chunk(
            text=intro,
            embed_text=_prefix(doc_title, "Introduction") + "\n\n" + intro,
            metadata={"source_doc": filename, "doc_type": "services", "section_title": "Introduction", "tier": None},
        ))

    for i in range(1, len(parts), 2):
        section_title = parts[i].strip()
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        full_text = f"## {section_title}\n\n{body}"

        # tag which tier this is, if any
        tier = None
        m = re.search(r"Tier (\d)", section_title)
        if m:
            tier = f"Tier {m.group(1)}"

        section_type = "comparison_table" if "Comparison" in section_title else (
            "cta" if "Ready to Begin" in section_title else "tier"
        )

        chunks.append(Chunk(
            text=full_text,
            embed_text=_prefix(doc_title, section_title, extra=f"Type: {section_type}") + "\n\n" + full_text,
            metadata={
                "source_doc": filename,
                "doc_type": "services",
                "section_title": section_title,
                "tier": tier,
                "section_type": section_type,
            },
        ))
    return chunks


CHUNKERS = {
    "client-faqs-home-remodeling.md": chunk_faq,
    "general-company-information.md": chunk_company_info,
    "Services-and-Packages-Menu.md": chunk_services,
}


def chunk_file(filepath: str, filename: str) -> list[Chunk]:
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()
    return CHUNKERS[filename](text, filename)


if __name__ == "__main__":
    import os
    import json

    data_dir = os.path.join(os.path.dirname(__file__), "data")
    all_chunks = []
    for filename in CHUNKERS:
        path = os.path.join(data_dir, filename)
        cs = chunk_file(path, filename)
        print(f"\n=== {filename}: {len(cs)} chunks ===")
        for c in cs:
            print(f"  - [{c.metadata.get('section_title')}] ({len(c.text)} chars)")
        all_chunks.extend(cs)

    print(f"\nTOTAL CHUNKS: {len(all_chunks)}")
