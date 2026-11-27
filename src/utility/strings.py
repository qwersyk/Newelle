import re 
import html
import xml 
import xml.dom.minidom
import json
from functools import lru_cache
from urllib.parse import urlparse
from gi.repository import GLib
from .media import extract_file, extract_image, extract_video, audio_text
from .message_chunk import extract_tables, replace_inline_latex
from .source_attribution import extract_source_section

def clean_prompt(prompt: str) -> str:
    prompt = audio_text(remove_thinking_blocks(prompt))
    _, message = extract_file(prompt)
    _, message = extract_image(message)
    _, message = extract_video(message)
    
    return message 


_TTS_FOOTNOTE_DEF_RE = re.compile(r"^[ \t]*\[\^[^\]]+\]:[^\n]*$", re.MULTILINE)
_TTS_FOOTNOTE_REF_RE = re.compile(r"[ \t]*\[\^[^\]]+\]")
_TTS_INLINE_CITATION_RE = re.compile(r"[ \t]*\[\d+(?:\s*[,–-]\s*\d+)*\](?!\s*[\(:])")

_TTS_FENCED_CODE_LINE_RE = re.compile(
    r"^[ \t]*(`{3,}|~{3,})[^\n]*\n[\s\S]*?(?:^[ \t]*\1[ \t]*$|\Z)",
    re.MULTILINE,
)
_TTS_FENCED_CODE_INLINE_RE = re.compile(r"(`{3,}|~{3,})[\s\S]*?(?:\1|\Z)")
_TTS_ESCAPED_CHAR_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~>])")
_TTS_INLINE_CODE_RE = re.compile(r"(``+)(.+?)\1|`([^`\n]+)`")
_TTS_UNCLOSED_INLINE_CODE_RE = re.compile(r"`([^`\n]*)\Z")

_TTS_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?(?:-->|\Z)")
_TTS_HTML_DROP_BLOCK_RE = re.compile(
    r"<(script|style|svg)\b[^>]*>[\s\S]*?</\1>",
    re.IGNORECASE,
)
_TTS_HTML_PRE_RE = re.compile(r"<pre\b[^>]*>[\s\S]*?</pre>", re.IGNORECASE)
_TTS_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>[\s\S]*?</table>", re.IGNORECASE)
_TTS_HTML_TR_RE = re.compile(r"<tr\b[^>]*>[\s\S]*?</tr>", re.IGNORECASE)
_TTS_HTML_TD_RE = re.compile(r"<t[dh]\b[^>]*>[\s\S]*?</t[dh]>", re.IGNORECASE)
_TTS_HTML_HEADING_RE = re.compile(r"<h[1-6]\b[^>]*>([\s\S]*?)</h[1-6]>", re.IGNORECASE)
_TTS_HTML_LI_RE = re.compile(r"<li\b[^>]*>([\s\S]*?)</li>", re.IGNORECASE)
_TTS_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TTS_HTML_HR_RE = re.compile(r"<hr\s*/?>", re.IGNORECASE)
_TTS_HTML_BLOCK_TAG_RE = re.compile(
    r"</?(?:p|div|section|article|blockquote|ul|ol|dl|dt|dd|header|footer|main|aside|nav|figure|figcaption|details|summary)\b[^>]*>",
    re.IGNORECASE,
)
_TTS_HTML_INLINE_TAG_RE = re.compile(
    r"</?(?:a|abbr|b|bdi|bdo|cite|code|data|del|dfn|em|i|ins|kbd|mark|q|rp|rt|ruby|s|samp|small|span|strong|sub|sup|time|u|var|wbr)\b[^<>]*>",
    re.IGNORECASE,
)

_TTS_PIPE_TABLE_ROW_RE = re.compile(r"^[ \t]*\|[^\n]+\|[ \t]*$", re.MULTILINE)
_TTS_PIPE_TABLE_SEP_RE = re.compile(
    r"^[ \t]*\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)+\|?[ \t]*$",
    re.MULTILINE,
)

_TTS_LATEX_BRACKET_DISPLAY_RE = re.compile(r"\\\[[\s\S]*?\\\]")
_TTS_LATEX_DOLLAR_DISPLAY_RE = re.compile(r"\$\$[\s\S]*?\$\$")
_TTS_LATEX_ENV_RE = re.compile(r"\\begin\{([a-zA-Z*]+)\}[\s\S]*?\\end\{\1\}")
_TTS_SUPERSCRIPTS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
_TTS_SUBSCRIPTS = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

_TTS_REF_LINK_DEF_RE = re.compile(r"^[ \t]*\[[^\]]+\]:[ \t]+\S+[^\n]*$", re.MULTILINE)
_TTS_MD_STANDALONE_IMAGE_RE = re.compile(
    r"^[ \t]*!\[([^\]]*)\]\((?:[^()\s]|\([^()\s]*\))+(?:\s+[\"'][^\"']*[\"'])?\s*\)[ \t]*$",
    re.MULTILINE,
)
_TTS_MD_IMAGE_RE = re.compile(
    r"!\[([^\]]*)\]\((?:[^()\s]|\([^()\s]*\))+(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)
_TTS_MD_LINK_RE = re.compile(
    r"\[([^\]]+)\]\((?:[^()\s]|\([^()\s]*\))+(?:\s+[\"'][^\"']*[\"'])?\s*\)"
)
_TTS_MD_REF_LINK_RE = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_TTS_AUTOLINK_URL_RE = re.compile(r"<(https?://[^>\s]+)>")
_TTS_AUTOLINK_MAIL_RE = re.compile(r"<mailto:([^>\s]+)>")
_TTS_IMAGE_FILENAME_RE = re.compile(
    r"[\w.\-/]+\.(?:png|jpe?g|gif|webp|svg|bmp|ico|tiff?|avif)",
    re.IGNORECASE,
)

_TTS_ACTION_SPAN = r"\*(?!\s|\*)[^*\n:.!?]+?(?<!\s|\*)\*"
_TTS_ACTION_STANDALONE_RE = re.compile(
    rf"^[ \t]*(?:{_TTS_ACTION_SPAN}[ \t]*)+$",
    re.MULTILINE,
)
_TTS_ACTION_LINE_START_RE = re.compile(
    rf"^([ \t]*)(?:{_TTS_ACTION_SPAN}[ \t]+)+([^\n])",
    re.MULTILINE,
)
_TTS_ACTION_AFTER_SENT_RE = re.compile(
    rf"([.!?…])(?:[ \t]+{_TTS_ACTION_SPAN})+(?=[ \t]*(?:$|\n)|[ \t]+([^\n]))"
)

_TTS_ATX_HEADING_RE = re.compile(r"^[ \t]*#{1,6}(?:[ \t]+(.*?))?[ \t]*#*[ \t]*$")
_TTS_SETEXT_UNDERLINE_RE = re.compile(r"^[ \t]*(?:={2,}|-{2,})[ \t]*$")
_TTS_HR_RE = re.compile(
    r"^[ \t]*(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$"
)
_TTS_STANDALONE_BOLD_RE = re.compile(
    r"^[ \t]*(?:\*\*(?!\s)((?:(?!\*\*).)+?)(?<!\s)\*\*|__(?!\s)((?:(?!__).)+?)(?<!\s)__)[ \t]*(:?)[ \t]*$"
)
_TTS_BLOCKQUOTE_LINE_RE = re.compile(r"^[ \t]*>[ \t]?(.*)$")
_TTS_GH_ALERT_RE = re.compile(
    r"^\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*",
    re.IGNORECASE,
)
_TTS_UL_ITEM_RE = re.compile(
    r"^[ \t]*[-*+•][ \t]+(?:\[[ xX✓✗-]\][ \t]+)?(.*)$"
)
_TTS_OL_ITEM_RE = re.compile(
    r"^[ \t]*(\d+)[.)][ \t]+(?:\[[ xX✓✗-]\][ \t]+)?(.*)$"
)
_TTS_KEY_VALUE_LINE_RE = re.compile(r"^[^:\n]{1,50}:[ \t]+\S")

_TTS_HEADING_END_PUNCT_RE = re.compile(r'[.!?:;…]["\'»”’)\]}]*$')
_TTS_BLOCK_END_PUNCT_RE = re.compile(r'[.!?:;,…—–-]["\'»”’)\]}]*$')


@lru_cache(maxsize=1)
def _get_latex_converter():
    try:
        from pylatexenc.latex2text import LatexNodes2Text
        return LatexNodes2Text()
    except ImportError:
        return None


def _convert_inline_latex_tts(span: str) -> str:
    if span.startswith("\\(") and span.endswith("\\)"):
        inner = span[2:-2].strip()
    elif span.startswith("$") and span.endswith("$"):
        inner = span[1:-1].strip()
    else:
        inner = span.strip()

    if not inner or len(inner) >= 60 or "\\begin" in inner:
        return ""

    # Preserve space after TeX control words when followed by operators/digits (e.g. \alpha = 1)
    prepared = re.sub(r"(\\[a-zA-Z]+)\s+(?=[^a-zA-Z\s])", r"\1{} ", inner)

    converter = _get_latex_converter()
    if converter is not None:
        try:
            converted = converter.latex_to_text(prepared)
        except (ValueError, TypeError, RuntimeError):
            converted = inner
    else:
        converted = re.sub(r"\\([a-zA-Z]+)", r"\1", inner)
        converted = re.sub(r"[{}]", "", converted)

    converted = re.sub(
        r"\^([0-9])(?!\d)",
        lambda m: m.group(1).translate(_TTS_SUPERSCRIPTS),
        converted,
    )
    converted = re.sub(
        r"_([0-9])(?!\d)",
        lambda m: m.group(1).translate(_TTS_SUBSCRIPTS),
        converted,
    )
    return " ".join(converted.split())


def _starts_new_sentence_token(ch: str) -> bool:
    if not ch:
        return False
    if ch in '"«“\'¡¿(':
        return True
    return ch.isupper()


def _remove_roleplay_actions(text: str) -> str:
    """Remove standalone or sentence-detached ``*action*`` expressions while keeping inline italics."""
    text = _TTS_ACTION_STANDALONE_RE.sub("", text)

    def _replace_line_start(match: re.Match) -> str:
        indent, next_char = match.group(1), match.group(2)
        if _starts_new_sentence_token(next_char):
            return indent + next_char
        return match.group(0)

    text = _TTS_ACTION_LINE_START_RE.sub(_replace_line_start, text)

    def _replace_after_sentence(match: re.Match) -> str:
        punct, next_char = match.group(1), match.group(2)
        if next_char is None or _starts_new_sentence_token(next_char):
            return punct
        return match.group(0)

    return _TTS_ACTION_AFTER_SENT_RE.sub(_replace_after_sentence, text)


def _strip_inline_markdown_tts(text: str) -> str:
    """Strip inline Markdown markers while preserving readable content."""
    text = re.sub(r"_\(([^)\n]+)\)", r"\1", text)
    text = re.sub(r"\^\(([^)\n]+)\)", r"\1", text)

    text = re.sub(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)___(?!\s)(.+?)(?<!\s)___(?!\w)", r"\1", text)

    text = re.sub(r"\*\*(?!\s)(.+?)(?<!\s)\*\*", r"\1", text)
    text = re.sub(r"(?<!\w)__(?!\s)(.+?)(?<!\s)__(?!\w)", r"\1", text)

    text = re.sub(r"(?<!\*)\*(?!\s|\*)([^*\n]+?)(?<!\s|\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!\w)_(?!\s|_)([^_\n]+?)(?<!\s|_)_(?!\w)", r"\1", text)

    text = re.sub(r"~~(?!\s)(.+?)(?<!\s)~~", r"\1", text)
    text = re.sub(r"(?<!~)~(?!\s|~|\d)([^~\n]+?)(?<!\s|~)~(?!~)", r"\1", text)
    text = re.sub(r"(?<!=)==(?!\s|=)([^=\n]+?)(?<!\s|=)==(?!=)", r"\1", text)

    # Hide an unclosed streaming bold marker at the end of partial text
    text = re.sub(r"(?<!\*)\*\*(?=\S)([^*\n]*)\Z", r"\1", text)
    return text


def _ensure_pause_punctuation(text: str, is_heading: bool = False) -> str:
    text = text.strip()
    if not text:
        return ""
    if is_heading:
        text = text.rstrip(",—–-")
        text = text.strip()
        if not text:
            return ""
        if _TTS_HEADING_END_PUNCT_RE.search(text):
            return text
        return text + "."
    if _TTS_BLOCK_END_PUNCT_RE.search(text):
        return text
    return text + "."


def _convert_markdown_blocks_for_tts(text: str) -> str:
    """Convert structural Markdown blocks into natural spoken text with pauses."""
    lines = text.split("\n")
    blocks: list[tuple[str, list]] = []

    for raw_line in lines:
        stripped = raw_line.strip()

        # Empty line separates blocks
        if not stripped:
            if blocks and blocks[-1][0] != "break":
                blocks.append(("break", []))
            continue

        # Setext heading underline immediately after a 1-line paragraph
        if (
            _TTS_SETEXT_UNDERLINE_RE.match(raw_line)
            and blocks
            and blocks[-1][0] == "paragraph"
            and len(blocks[-1][1]) == 1
        ):
            heading_line = blocks[-1][1][0]
            blocks[-1] = ("heading", [heading_line])
            blocks.append(("break", []))
            continue

        # Horizontal rule / section divider -> pause break
        if _TTS_HR_RE.match(raw_line):
            if blocks and blocks[-1][0] != "break":
                blocks.append(("break", []))
            continue

        # ATX heading (# .. ######)
        m_heading = _TTS_ATX_HEADING_RE.match(raw_line)
        if m_heading:
            heading_body = (m_heading.group(1) or "").strip()
            if blocks and blocks[-1][0] != "break":
                blocks.append(("break", []))
            if heading_body:
                blocks.append(("heading", [heading_body]))
                blocks.append(("break", []))
            continue

        # Standalone bold line (**Heading** or __Heading__) treated as a heading
        m_bold_heading = _TTS_STANDALONE_BOLD_RE.match(raw_line)
        if m_bold_heading:
            bold_body = (m_bold_heading.group(1) or m_bold_heading.group(2) or "").strip()
            trailing_colon = m_bold_heading.group(3) or ""
            if blocks and blocks[-1][0] != "break":
                blocks.append(("break", []))
            if bold_body:
                blocks.append(("heading", [bold_body + trailing_colon]))
                blocks.append(("break", []))
            continue

        # Blockquote (> ...)
        m_quote = _TTS_BLOCKQUOTE_LINE_RE.match(raw_line)
        if m_quote:
            quote_content = re.sub(r"^[ \t]*(?:>[ \t]?)+", "", raw_line).strip()
            quote_content = _TTS_GH_ALERT_RE.sub("", quote_content).strip()
            if not quote_content:
                if blocks and blocks[-1][0] == "blockquote":
                    blocks.append(("break", []))
                continue
            if blocks and blocks[-1][0] == "blockquote":
                blocks[-1][1].append(quote_content)
            else:
                if blocks and blocks[-1][0] != "break":
                    blocks.append(("break", []))
                blocks.append(("blockquote", [quote_content]))
            continue

        # Unordered list item (- / * / + / •, with optional task checkbox)
        m_ul = _TTS_UL_ITEM_RE.match(raw_line)
        if m_ul:
            item_body = m_ul.group(1).strip()
            if not item_body:
                continue
            if blocks and blocks[-1][0] == "list":
                blocks[-1][1].append(("", item_body))
            else:
                if blocks and blocks[-1][0] != "break":
                    blocks.append(("break", []))
                blocks.append(("list", [("", item_body)]))
            continue

        # Ordered list item (1. / 1), keeping number)
        m_ol = _TTS_OL_ITEM_RE.match(raw_line)
        if m_ol:
            num, item_body = m_ol.group(1), m_ol.group(2).strip()
            if not item_body:
                continue
            prefix = f"{num}. "
            if blocks and blocks[-1][0] == "list":
                blocks[-1][1].append((prefix, item_body))
            else:
                if blocks and blocks[-1][0] != "break":
                    blocks.append(("break", []))
                blocks.append(("list", [(prefix, item_body)]))
            continue

        # Indented continuation of an open list item
        if (
            blocks
            and blocks[-1][0] == "list"
            and blocks[-1][1]
            and re.match(r"^[ \t]{2,}\S", raw_line)
        ):
            prefix, prev_body = blocks[-1][1][-1]
            blocks[-1][1][-1] = (prefix, f"{prev_body} {stripped}")
            continue

        # Normal paragraph line (leave a break if transitioning out of a list/blockquote)
        if blocks and blocks[-1][0] in ("list", "blockquote"):
            blocks.append(("break", []))

        if blocks and blocks[-1][0] == "paragraph":
            blocks[-1][1].append(stripped)
        else:
            blocks.append(("paragraph", [stripped]))

    # Remove leading/trailing/duplicate breaks
    content_blocks = [b for b in blocks if b[0] != "break"]
    if not content_blocks:
        return ""

    rendered_blocks: list[str] = []
    total = len(content_blocks)
    for idx, (btype, bdata) in enumerate(content_blocks):
        has_following_block = idx < total - 1

        if btype == "heading":
            cleaned = _strip_inline_markdown_tts(bdata[0])
            spoken = _ensure_pause_punctuation(cleaned, is_heading=True)
            if spoken:
                rendered_blocks.append(spoken)

        elif btype == "list":
            rendered_items = []
            for prefix, body in bdata:
                cleaned = _strip_inline_markdown_tts(body).strip()
                if not cleaned:
                    continue
                spoken = _ensure_pause_punctuation(cleaned, is_heading=False)
                rendered_items.append(f"{prefix}{spoken}")
            if rendered_items:
                rendered_blocks.append("\n".join(rendered_items))

        elif btype in ("paragraph", "blockquote"):
            cleaned_lines = [
                _strip_inline_markdown_tts(line).strip()
                for line in bdata
            ]
            cleaned_lines = [line for line in cleaned_lines if line]
            if not cleaned_lines:
                continue

            # Add pause punctuation between consecutive Key: Value lines
            for line_idx in range(len(cleaned_lines) - 1):
                cur_line = cleaned_lines[line_idx]
                next_line = cleaned_lines[line_idx + 1]
                if _TTS_KEY_VALUE_LINE_RE.match(cur_line) and _TTS_KEY_VALUE_LINE_RE.match(next_line):
                    cleaned_lines[line_idx] = _ensure_pause_punctuation(cur_line, is_heading=False)

            if has_following_block:
                cleaned_lines[-1] = _ensure_pause_punctuation(cleaned_lines[-1], is_heading=False)

            rendered_blocks.append("\n".join(cleaned_lines))

    return "\n\n".join(rendered_blocks)


_TTS_QUOTED_PHRASE_RE = re.compile(
    r'"((?:[^"\n]|\n(?!\n))+?)"'
    r"|“((?:[^“”\n]|\n(?!\n))+?)”"
    r"|„((?:[^„“”\n]|\n(?!\n))+?)[“”]"
    r"|«((?:[^«»\n]|\n(?!\n))+?)»"
)


def extract_quoted_phrases(text: str) -> str:
    """Return only the phrases enclosed in double quotes, one per line.

    Supports straight ("..."), curly (“...”), German („...“) and guillemet
    («...») quotes. Quotes never span a blank line, so a stray unmatched quote
    cannot swallow the rest of the message.
    """
    if not text:
        return ""
    phrases = []
    for match in _TTS_QUOTED_PHRASE_RE.finditer(text):
        phrase = next(group for group in match.groups() if group is not None)
        phrase = " ".join(phrase.split())
        if phrase:
            phrases.append(phrase)
    return "\n".join(phrases)


def clean_message_tts(text: str, quoted_only: bool = False) -> str:
    """Clean a message for text-to-speech (TTS) readability.

    Converts Markdown and rich assistant output into natural spoken prose:
    - Removes reasoning/thinking blocks, inline citations, and the Sources section
    - Removes code blocks, tables (Markdown and HTML), and display LaTeX equations
    - Converts short inline LaTeX expressions into readable text
    - Converts structural Markdown (headings, standalone bold titles, lists,
      dividers, blockquotes, paragraphs) into natural spoken pauses
    - Preserves inline italics while removing standalone/detached ``*action*`` cues
    - Keeps link labels and ordered list numbering
    - Removes emojis and raw Markdown/HTML syntax

    Args:
        text: The text to clean for TTS
        quoted_only: Keep only the phrases enclosed in double quotes

    Returns:
        The cleaned text suitable for TTS playback
    """
    if not text:
        return ""

    text = str(text).replace("\r\n", "\n").replace("\r", "\n")

    # 1. Strip reasoning/thinking blocks before any codeblock or Markdown rules
    text = normalize_thinking_tags(text)
    text = remove_thinking_for_display(text)

    # 2. Strip trailing Sources section and inline citation/footnote markers
    text, _ = extract_source_section(text)
    text = _TTS_FOOTNOTE_DEF_RE.sub("", text)
    text = _TTS_FOOTNOTE_REF_RE.sub("", text)
    text = _TTS_INLINE_CITATION_RE.sub("", text)

    # 3. Remove fenced code blocks (``` and ~~~, including unclosed streaming fences)
    text = _TTS_FENCED_CODE_LINE_RE.sub("\n\n", text)
    text = _TTS_FENCED_CODE_INLINE_RE.sub("\n\n", text)

    # 4. Protect inline code spans first so code is untouched by LaTeX/Markdown rules
    placeholders: dict[str, str] = {}
    placeholder_idx = 0

    def _save_placeholder(value: str) -> str:
        nonlocal placeholder_idx
        key = f"\x00TTSPH{placeholder_idx}\x00"
        placeholder_idx += 1
        placeholders[key] = value
        return key

    text = _TTS_INLINE_CODE_RE.sub(
        lambda m: _save_placeholder((m.group(2) if m.group(2) is not None else m.group(3)).strip()),
        text,
    )
    text = _TTS_UNCLOSED_INLINE_CODE_RE.sub(lambda m: _save_placeholder(m.group(1).strip()), text)

    # 5. Remove display LaTeX and convert short inline LaTeX before backslash-escape handling
    text = _TTS_LATEX_BRACKET_DISPLAY_RE.sub("\n\n", text)
    text = _TTS_LATEX_DOLLAR_DISPLAY_RE.sub("\n\n", text)
    text = _TTS_LATEX_ENV_RE.sub("\n\n", text)
    if "$" in text or "\\(" in text:
        text = replace_inline_latex(text, _convert_inline_latex_tts)

    # 6. Protect remaining Markdown backslash escapes (\*, \_, \#, etc.)
    text = _TTS_ESCAPED_CHAR_RE.sub(lambda m: _save_placeholder(m.group(1)), text)

    # 7. Clean HTML comments, non-spoken blocks, tables, and structural/inline tags
    if "<" in text:
        text = _TTS_HTML_COMMENT_RE.sub("", text)
        text = _TTS_HTML_DROP_BLOCK_RE.sub("", text)
        text = _TTS_HTML_PRE_RE.sub("\n\n", text)
        text = _TTS_HTML_TABLE_RE.sub("\n\n", text)
        text = _TTS_HTML_TR_RE.sub("\n\n", text)
        text = _TTS_HTML_TD_RE.sub("", text)
        text = _TTS_HTML_HEADING_RE.sub(
            lambda m: f"\n\n# {' '.join(m.group(1).split())}\n\n",
            text,
        )
        text = _TTS_HTML_LI_RE.sub(
            lambda m: f"\n- {' '.join(m.group(1).split())}\n",
            text,
        )
        text = _TTS_HTML_BR_RE.sub("\n", text)
        text = _TTS_HTML_HR_RE.sub("\n\n", text)
        text = _TTS_HTML_BLOCK_TAG_RE.sub("\n\n", text)

    # 8. Remove Markdown pipe tables (both outer-piped and borderless)
    if "|" in text:
        table_chunks = extract_tables(text)
        text = "\n\n".join(
            "\n\n" if chunk.type == "table" else chunk.text
            for chunk in table_chunks
        )
        text = _TTS_PIPE_TABLE_ROW_RE.sub("", text)
        text = _TTS_PIPE_TABLE_SEP_RE.sub("", text)

    # 9. Convert Markdown images, links, reference links, and autolinks
    text = _TTS_REF_LINK_DEF_RE.sub("", text)

    def _clean_image_alt(raw_alt: str) -> str:
        alt = raw_alt.strip()
        if not alt or alt.startswith(("http://", "https://")) or _TTS_IMAGE_FILENAME_RE.fullmatch(alt):
            return ""
        return alt

    def _replace_standalone_image(match: re.Match) -> str:
        alt = _clean_image_alt(match.group(1))
        return f"\n\n# {alt}\n\n" if alt else ""

    def _replace_link(match: re.Match) -> str:
        label = match.group(1).strip()
        if label.startswith(("http://", "https://")):
            domain = urlparse(label).netloc.removeprefix("www.")
            return domain or label
        return label

    text = _TTS_MD_STANDALONE_IMAGE_RE.sub(_replace_standalone_image, text)
    text = _TTS_MD_IMAGE_RE.sub(lambda m: _clean_image_alt(m.group(1)), text)
    text = _TTS_MD_LINK_RE.sub(_replace_link, text)
    text = _TTS_MD_REF_LINK_RE.sub(r"\1", text)
    text = _TTS_AUTOLINK_URL_RE.sub(
        lambda m: urlparse(m.group(1)).netloc.removeprefix("www.") or m.group(1),
        text,
    )
    text = _TTS_AUTOLINK_MAIL_RE.sub(r"\1", text)

    if "<" in text:
        text = _TTS_HTML_INLINE_TAG_RE.sub("", text)

    # 9. Remove emojis before block punctuation so trailing emojis don't distort pauses
    text = remove_emoji(text)

    # 10. Remove standalone/detached roleplay actions (*waves at you*), keep normal italics
    if "*" in text:
        text = _remove_roleplay_actions(text)

    # 11. Convert headings, lists, dividers, blockquotes, and paragraphs with natural pauses
    text = _convert_markdown_blocks_for_tts(text)

    # 12. Restore inline code and escaped characters, unescape HTML entities, normalize spaces
    # Quotes are matched before restoring placeholders so quotes inside inline code are ignored
    if quoted_only:
        text = extract_quoted_phrases(text)

    for key, value in placeholders.items():
        text = text.replace(key, value)

    if "&" in text:
        text = html.unescape(text)
    text = text.replace("\u00a0", " ")

    # Remove emojis again in case placeholders or HTML entities introduced any
    text = remove_emoji(text)

    # Clean up horizontal whitespace and spaces stranded before punctuation
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"(?<=\S)[ \t]+([,!?;:])", r"\1", text)
    text = re.sub(r"(?<=[^\s.])[ \t]+\.(?!\.)", ".", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()

@lru_cache(maxsize=16)
def _get_encoding(model: str):
    import tiktoken
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "gpt-4o-mini") -> int:
    """
    Count the number of tokens in a string
    """
    try:
        return len(_get_encoding(model).encode(text))
    except Exception:
        return len(text) // 4

def count_message_tokens(message: str, model: str = "gpt-4o-mini") -> int:
    """Count the textual tokens in a chat message.

    Media attachments are sent to multimodal models separately from the text.
    Exclude their path or base64 payload so pasted images do not appear to use
    hundreds of thousands of text tokens.
    """
    return count_tokens(clean_prompt(message), model)

def quote_string(s):
    if "'" in s:
        return "'" + s.replace("'", "'\\''") + "'"
    else:
        return "'" + s + "'"

def markwon_to_pango(markdown_text, validate=True):
    """
    Converts a subset of Markdown text to Pango markup.

    Supports:
    - Bold: **text** -> <b>text</b>
    - Italic: *text* -> <i>text</i>
    - Monospace: `text` -> <tt>text</tt>
    - Strikethrough: ~text~ -> <span strikethrough="true">text</span>
    - Subscript: _(text) or _digit -> <sub>text</sub> or <sub>digit</sub>
    - Superscript: ^(text) or ^digit -> <sup>text</sup> or <sup>digit</sup>
    - Links: [text](url) -> <a href="url">text</a>
    - Headers: # text -> <span font_weight="bold" font_size="...">text</span> (up to H6)
    - Unordered Lists: -/*/+ item ->   • item (with indentation)
    """
    # Escape potential Pango/XML characters first to avoid issues
    # with user input containing <, >, &
    escaped_text = GLib.markup_escape_text(markdown_text)
    escaped_text = escaped_text.replace("&lt;sub&gt;", "<sub>" )
    escaped_text = escaped_text.replace("&lt;/sub&gt;", "</sub>" )

    escaped_text = escaped_text.replace("&lt;sup&gt;", "<sup>" )
    escaped_text = escaped_text.replace("&lt;/sup&gt;", "</sup>" )
    initial_string = escaped_text # Keep the escaped version as fallback

    processed_text = escaped_text

    # --- Block Formatting ---

    # Convert Unordered Lists
    processed_text = re.sub(
        r'^([ \t]*)([-*+])[ \t]+(.*)$',
        lambda m: '\u00A0' * (2 + len(m.group(1).expandtabs(4)) * 2) + '• ' + m.group(3),
        processed_text,
        flags=re.MULTILINE
    )

    # Convert Ordered Lists
    processed_text = re.sub(
        r'^([ \t]*)(\d+\.)[ \t]+(.*)$',
        lambda m: '\u00A0' * (2 + len(m.group(1).expandtabs(4)) * 2) + m.group(2) + ' ' + m.group(3),
        processed_text,
        flags=re.MULTILINE
    )
    
    # Use placeholders to protect monospace and strikethrough from nested formatting
    placeholders = {}
    placeholder_counter = [0]
    
    def make_placeholder(match, tag_start, tag_end):
        placeholder_counter[0] += 1
        key = f"PLACEHOLDER_{placeholder_counter[0]}_"
        placeholders[key] = f"{tag_start}{match.group(1)}{tag_end}"
        return key
    
    # Convert monospace text first (to protect code from other formatting)
    processed_text = re.sub(r'`([^`]*?)`', 
                           lambda m: make_placeholder(m, '<tt>', '</tt>'), 
                           processed_text)

    # Convert strikethrough text
    processed_text = re.sub(r'~([^~]*?)~', 
                           lambda m: make_placeholder(m, '<span strikethrough="true">', '</span>'), 
                           processed_text)
    
    # Handle *** (bold+italic) first
    processed_text = re.sub(r'\*\*\*(?!\s)((?:(?!\*\*\*)[^<>])*?)(?<!\s)\*\*\*', r'<b><i>\1</i></b>', processed_text)
    
    # Multiple passes for proper nesting (using safer patterns):
    # Pass 1: italic without any tags
    processed_text = re.sub(r'(?<!\*)\*(?!\s|\*)((?:(?!\*)[^<>])*?)(?<!\s|\*)\*(?!\*)', r'<i>\1</i>', processed_text)
    
    # Pass 2: bold which may contain <i> tags
    processed_text = re.sub(r'\*\*(?!\s)((?:(?!\*\*).)*?)(?<!\s)\*\*', r'<b>\1</b>', processed_text)
    
    # Pass 3: italic again but allow content with <b></b> tags (complete pairs only)
    # This handles *italic with <b>bold</b> text* after bold conversion
    # Simpler and faster version
    processed_text = re.sub(
        r'(?<!\*)\*(?!\s|\*)((?:(?!\*).)+?)(?<!\s|\*)\*(?!\*)',
        r'<i>\1</i>', 
        processed_text
    )
    
    # Restore placeholders
    for key, value in placeholders.items():
        processed_text = processed_text.replace(key, value)

    # Convert exponents and subscripts (handle digits or parenthesized text)
    processed_text = re.sub(r'_(\d+|\([^)]+\))\b', lambda m: f'<sub>{m.group(1).strip("()")}</sub>', processed_text)
    processed_text = re.sub(r'\^(\d+|\([^)]+\))', lambda m: f'<sup>{m.group(1).strip("()")}</sup>', processed_text)
    
    # Convert links
    processed_text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', processed_text)


    # Convert headers (needs to be after lists potentially, though headers usually don't have list markers)
    absolute_sizes = ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large']
    # Make sure header regex doesn't consume list items if they somehow start with #
    processed_text = re.sub(
        r'^[ \t]*(#+)[ \t]+(.*)$', 
        lambda match: f'<span font_weight="bold" font_size="{absolute_sizes[min(len(absolute_sizes)-1, 6 - len(match.group(1)))]}">{match.group(2).strip()}</span>',
        processed_text,
        flags=re.MULTILINE
    )

    if validate:
        try:
            xml.dom.minidom.parseString(f"<span>{processed_text}</span>")
            return processed_text
        except Exception:
            return simple_markdown_to_pango(initial_string)
    return processed_text

def simple_markdown_to_pango(markdown_text):
    """
    Converts a subset of Markdown text to Pango markup. (Used as a Fallback)

    Supports:
    - Bold: **text** -> <b>text</b>
    - Italic: *text* -> <i>text</i>
    - Links: [text](url) -> <a href="url">text</a>
    - Headers: # text -> <span font_weight="bold" font_size="...">text</span> (up to H6)
    """
    # Escape potential Pango/XML characters first to avoid issues
    # with user input containing <, >, &
    escaped_text = GLib.markup_escape_text(markdown_text)
    initial_string = escaped_text # Keep the escaped version as fallback

    processed_text = escaped_text
 
    # Handle *** (bold+italic) first
    processed_text = re.sub(r'\*\*\*(?!\s)((?:(?!\*\*\*)[^<>])*?)(?<!\s)\*\*\*', r'<b><i>\1</i></b>', processed_text)
    
    # Multiple passes for proper nesting (using safer patterns):
    # Pass 1: italic without any tags
    processed_text = re.sub(r'(?<!\*)\*(?!\s|\*)((?:(?!\*)[^<>])*?)(?<!\s|\*)\*(?!\*)', r'<i>\1</i>', processed_text)
    
    # Pass 2: bold which may contain <i> tags
    processed_text = re.sub(r'\*\*(?!\s)((?:(?!\*\*).)*?)(?<!\s)\*\*', r'<b>\1</b>', processed_text)
    
    # Pass 3: italic again but allow content with <b></b> tags (complete pairs only)
    # This handles *italic with <b>bold</b> text* after bold conversion
    # Simpler and faster version
    processed_text = re.sub(
        r'(?<!\*)\*(?!\s|\*)((?:(?!\*).)+?)(?<!\s|\*)\*(?!\*)',
        r'<i>\1</i>', 
        processed_text
    )

    # Convert links
    processed_text = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', processed_text)


    # Convert headers (needs to be after lists potentially, though headers usually don't have list markers)
    absolute_sizes = ['xx-small', 'x-small', 'small', 'medium', 'large', 'x-large', 'xx-large']
    # Make sure header regex doesn't consume list items if they somehow start with #
    processed_text = re.sub(
        r'^[ \t]*(#+)[ \t]+(.*)$', 
        lambda match: f'<span font_weight="bold" font_size="{absolute_sizes[min(len(absolute_sizes)-1, 6 - len(match.group(1)))]}">{match.group(2).strip()}</span>',
        processed_text,
        flags=re.MULTILINE
    )

    try:
        xml.dom.minidom.parseString(f"<span>{processed_text}</span>")
        return processed_text
    except Exception:
        return initial_string

def human_readable_size(size: float, decimal_places:int =2) -> str:
    size = int(size)
    unit = ''
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB']:
        if size < 1024.0 or unit == 'PiB':
            break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"


def extract_json(input_string: str) -> str:
    """Extracts the first valid JSON string from an input string.

    Args:
        input_string: The string to search for a JSON object or array.

    Returns:
        The first valid JSON string found, or an empty JSON object "{}" if none is found.
    """
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(input_string):
        # Find the first opening brace or bracket
        match = None
        first_brace = input_string.find('{', pos)
        first_bracket = input_string.find('[', pos)

        if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
            pos = first_brace
        elif first_bracket != -1:
            pos = first_bracket
        else:
            # No more JSON objects in the string
            break

        try:
            # Attempt to decode a JSON object from the current position
            obj, end_pos = decoder.raw_decode(input_string[pos:])
            return input_string[pos:pos + end_pos]
        except json.JSONDecodeError:
            # If decoding fails, move to the next character and try again
            pos += 1
            
    return "{}"


def remove_markdown(text: str) -> str:
    """
    Remove markdown from text

    Args:
        text: The text to remove markdown from 

    Returns:
        str: The text without markdown 
    """
    # Remove code blocks before inline code so fences are not broken
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'~~~[\s\S]*?~~~', '', text)

    # Remove inline code
    text = re.sub(r'`([^`]*)`', r'\1', text)

    # Remove images before links so ![alt](url) does not leave a leading '!'
    text = re.sub(r'!\[([^\]]*)\]\((?:[^()\s]|\([^()\s]*\))+\)', r'\1', text)

    # Remove links, keep the link text
    text = re.sub(r'\[([^\]]+)\]\((?:[^()\s]|\([^()\s]*\))+\)', r'\1', text)

    # Remove headers
    text = re.sub(r'^[ \t]*#{1,6}\s*(.*?)[ \t]*#*[ \t]*$', r'\1', text, flags=re.MULTILINE)

    # Remove horizontal rules before list/emphasis rules
    text = re.sub(r'^[ \t]*[-*_][ \t]*[-*_][ \t]*[-*_][ \t]*[-*_ ]*$', '', text, flags=re.MULTILINE)

    # Remove unordered and ordered list markers
    text = re.sub(r'^\s*[-+*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)

    # Remove emphasis (bold and italic), respecting word boundaries for '_'
    text = re.sub(r'\*\*\*(.*?)\*\*\*', r'\1', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)__(.*?)__(?!\w)', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)', r'\1', text)
    text = re.sub(r'(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)', r'\1', text)

    # Remove strikethrough
    text = re.sub(r'~~(.*?)~~', r'\1', text)

    # Remove blockquotes
    text = re.sub(r'^[ \t]*>\s*', '', text, flags=re.MULTILINE)

    # Remove extra newlines
    text = re.sub(r'\n{2,}', '\n', text)

    return text.strip()

def build_chat_title(message: str, max_length: int = 30) -> str:
    """
    Build a short chat title from the beginning of a message.

    Strip attachment codeblocks before the general markdown cleanup: the
    inline-code rule in ``remove_markdown`` would otherwise break the fences
    and leak their content into the title. Remaining markdown is removed and
    whitespace is collapsed to a single line.

    Args:
        message: The message to build the title from
        max_length: Maximum number of characters kept

    Returns:
        The title, truncated with an ellipsis when needed. Empty when the
        message has no usable text.
    """
    text = re.sub(r'```[\s\S]*?```', ' ', str(message))
    text = " ".join(remove_markdown(text).split())
    if len(text) > max_length:
        text = text[:max_length].rstrip() + "…"
    return text

def convert_think_codeblocks(text: str) -> str:
    """Convert think codeblocks to markdown

    Args:
        text (str): The text to convert 

    Returns:
        str: The converted text 
    """
    return text.replace("<think>", "```think").replace("</think>", "```")

def extract_reasoning_content(message):
    """
    Extracts the first <think> block and the remaining clean text.

    Args:
        message: The input string containing the full message.

    Returns:
        A tuple of (reasoning, text). 
        If no <think> block is found, reasoning is None.
    """
    pattern = r"<think>(.*?)</think>"
    
    # Search for the first match anywhere in the string
    match = re.search(pattern, message, flags=re.DOTALL)
    
    if match:
        # Extract the content inside the capture group
        reasoning = match.group(1)
        # Remove the entire <think>...</think> structure from the main text
        clean_text = message.replace(match.group(0), "").strip()
        return reasoning, clean_text
    
    # If no think block is found
    return None, message.strip()

# Gemma 4 style: <|channel>thought ... <channel|>
_GEMMA_THINK_OPEN = re.compile(r"<\|channel>(?:thought\b[ \t]*\n?)?")
_GEMMA_THINK_CLOSE = re.compile(r"<channel\|>")
# gpt-oss "harmony" style: <|channel|>analysis<|message|> ... <|end|><|start|>assistant<|channel|>final<|message|> ...
_HARMONY_THINK_OPEN = re.compile(r"(?:<\|start\|>assistant)?<\|channel\|>analysis<\|message\|>")
_HARMONY_THINK_CLOSE = re.compile(
    r"<\|end\|>\s*(?:<\|start\|>assistant)?<\|channel\|>final<\|message\|>"
)
_HARMONY_LEFTOVERS = re.compile(
    r"(?:<\|start\|>assistant)?<\|channel\|>final<\|message\|>|<\|return\|>|<\|end\|>"
)


def normalize_thinking_tags(text):
    """Convert model-specific reasoning markers to ``<think>`` blocks.

    Some models (Gemma, gpt-oss, ...) served through endpoints that do not
    parse their chat template emit raw channel tokens in the content stream.
    """
    if not isinstance(text, str) or ("<|" not in text and "|>" not in text):
        return text
    if "<|channel>" in text or "<channel|>" in text:
        text = _GEMMA_THINK_OPEN.sub("<think>", text)
        text = _GEMMA_THINK_CLOSE.sub("</think>", text)
    if "<|channel|>" in text:
        text = _HARMONY_THINK_OPEN.sub("<think>", text)
        if "<think>" in text:
            text = _HARMONY_THINK_CLOSE.sub("</think>", text)
        text = _HARMONY_LEFTOVERS.sub("", text)
    return text


def remove_thinking_blocks(text):
    """Remove complete and currently-streaming ``<think>`` blocks.

    LLM streaming callbacks often receive an opening tag before its closing tag.
    Treat that unfinished trailing block as private reasoning as well so it is not
    rendered in live status text or sent to text-to-speech.
    """
    complete_block = r"<think\b[^>]*>.*?</think\s*>"
    text = re.sub(
        complete_block,
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    unfinished_block = r"<think\b[^>]*>.*\Z"
    return re.sub(
        unfinished_block,
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )


_PARTIAL_THINK_OPEN = re.compile(r"<(?:t(?:h(?:i(?:n(?:k)?)?)?)?)?\Z", re.IGNORECASE)


def remove_thinking_for_display(text):
    """Return the user-visible part of a (possibly still streaming) response.

    Besides complete and unfinished ``<think>`` blocks, this hides a trailing
    partial opening tag (``<thi``) so it is never shown before the stream
    reveals it as the start of a reasoning block.
    """
    text = remove_thinking_blocks(str(text))
    return _PARTIAL_THINK_OPEN.sub("", text).lstrip()

def get_edited_messages(history: list, old_history: list) -> list | None:
    """Get the edited messages from the history

    Args:
        history (list): The history
        prompts (list): The prompts

    Returns:
        list: The edited messages IDs, or None if there are removed messages
    """
    if len(history) != len(old_history):
        return None
    edited_messages = []
    for i in range(len(history)):
        if history[i] != old_history[i]:
            edited_messages.append(i)
    return edited_messages


def add_S_to_sudo(commands_string):
    """
    Adds the -S flag to every sudo command in a string of Linux commands.

    Args:
        commands_string: A string containing Linux commands.

    Returns:
        A string with the -S flag added to all sudo commands.
    """

    def replace_sudo(match):
        command_parts = match.group(0).split()
        if "-S" in command_parts:
            return " ".join(command_parts)  # Already has -S
        
        sudo_index = command_parts.index("sudo")

        if len(command_parts) > sudo_index + 1 and command_parts[sudo_index + 1].startswith("-"):
            #sudo has options, insert -S
            command_parts.insert(sudo_index + 1, "-S")
            return " ".join(command_parts)

        elif len(command_parts) > sudo_index:
            #Insert -S after sudo, no existing option
            command_parts.insert(sudo_index + 1, "-S")
            return " ".join(command_parts)
        else:
            # this case should not happen in normal command string
            return " ".join(command_parts)


    # Use regex to find all "sudo" commands, handling different scenarios
    modified_string = re.sub(r'(^|\s)sudo(\s+[\w\/\.-]+)*', replace_sudo, commands_string)

    return modified_string

_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
    "\U0001FA00-\U0001FAFF"  # symbols & pictographs extended-A
    "\u2600-\u26FF"          # misc symbols (⚠️, ⚡, ⭐, etc.)
    "\u2700-\u27BF"          # dingbats (✅, ✨, ❌, etc.)
    "\uFE0F"                 # variation selector-16
    "\u200D"                 # zero-width joiner
    "]+",
    flags=re.UNICODE,
)


def remove_emoji(text):
    return _EMOJI_PATTERN.sub("", text)


def replace_codeblock(markdown_text, block_id, new_code):
    """
    Replaces the code block at the given ID (starting from 0) with new_code.
    
    Args:
        markdown_text (str): The full markdown text.
        block_id (int): The index of the code block to replace.
        new_code (str): The new content to put inside the code block.
    
    Returns:
        str: Modified markdown text with the code block replaced.
    """
    pattern = re.compile(r'```.*?\n.*?```', re.DOTALL)
    matches = list(pattern.finditer(markdown_text))
    
    if block_id < 0 or block_id >= len(matches):
        raise IndexError("Code block ID out of range.")
    
    match = matches[block_id]
    # Preserve the language tag from the opening line
    opening_line = match.group(0).split('\n', 1)[0]
    new_block = f"{opening_line}\n{new_code}\n```"

    # Replace the matched code block with the new one
    return markdown_text[:match.start()] + new_block + markdown_text[match.end():]

def clean_bot_response(message_label):
    """Fix the bot response

    Args:
        message_label (): text of the message 
    """
    message_label = message_label.replace('\\\\\\```', "```")
    return message_label 
