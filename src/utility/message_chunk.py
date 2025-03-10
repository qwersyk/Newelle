import re
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class MessageChunk:
    type: str  # "codeblock", "table", "latex", "latex_inline", "inline_chunks", "thinking", or "text"
    text: str
    lang: str = ''  # Only used for codeblocks
    subchunks: Optional[List['MessageChunk']] = None  # Only used for inline_chunks

    def __str__(self):
        if self.type == "codeblock":
            return f"<CodeBlock lang='{self.lang}'>\n{self.text}\n</CodeBlock>"
        elif self.type == "table":
            return f"<Table>\n{self.text}\n</Table>"
        elif self.type == "latex":
            return f"<LatexDisplay>\n{self.text}\n</LatexDisplay>"
        elif self.type == "latex_inline":
            return f"<LatexInline>{self.text}</LatexInline>"
        elif self.type == "inline_chunks":
            sub = "\n".join("  " + str(sc) for sc in self.subchunks) if self.subchunks else ""
            return f"<InlineChunks>\n{sub}\n</InlineChunks>"
        elif self.type == "thinking":
            return f"<Thinking>{self.text}</Thinking>"
        elif self.type == "text":
            return f"<Text>{self.text}</Text>"
        else:
            return f"<{self.type}>{self.text}</{self.type}>"

def append_chunk(chunks: List[MessageChunk], new_chunk: MessageChunk):
    if chunks and chunks[-1].type == "text" and new_chunk.type == "text":
        chunks[-1].text += "\n" + new_chunk.text
    else:
        chunks.append(new_chunk)

def is_markdown_table(block: str) -> bool:
    """
    Improved detection for markdown tables.
    Checks for header row with pipes and a separator row with dashes.
    """
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    
    # Check if first line has pipes (header)
    if '|' not in lines[0]:
        return False
    
    # Check if second line is a separator (contains dashes between pipes)
    sep_line = lines[1]
    if not re.match(r'^\|?[-:\s\|]+$', sep_line):
        return False
        
    # Verify consistent number of columns
    header_cols = len(lines[0].split('|')) - 1  # Subtract 1 for empty ends
    sep_cols = len(sep_line.split('|')) - 1
    return header_cols == sep_cols and header_cols > 0

def extract_tables(text: str) -> List[MessageChunk]:
    """
    Extracts markdown tables from text and returns chunks with remaining text.
    """
    chunks = []
    last_pos = 0
    lines = text.splitlines()
    
    i = 0
    while i < len(lines):
        # Look for potential table start
        if '|' in lines[i] and i + 1 < len(lines):
            # Try to build a table starting from this line
            table_lines = [lines[i]]
            j = i + 1
            while j < len(lines) and '|' in lines[j]:
                table_lines.append(lines[j])
                j += 1
            
            potential_table = '\n'.join(table_lines)
            if is_markdown_table(potential_table):
                # Add text before table
                if last_pos < i:
                    pre_text = '\n'.join(lines[last_pos:i])
                    if pre_text.strip():
                        chunks.append(MessageChunk(type="text", text=pre_text))
                
                # Add table chunk
                chunks.append(MessageChunk(type="table", text=potential_table))
                i = j
                last_pos = i
                continue
        i += 1
    
    # Add remaining text
    if last_pos < len(lines):
        remaining = '\n'.join(lines[last_pos:])
        if remaining.strip():
            chunks.append(MessageChunk(type="text", text=remaining))
    
    return chunks

_display_latex_pattern = re.compile(r'(\$\$(.+?)\$\$)|(\\\[(.+?)\\\])', re.DOTALL)

def process_text_with_display_latex(text: str, allow_latex: bool) -> List[MessageChunk]:
    chunks = []
    last_index = 0
    for match in _display_latex_pattern.finditer(text):
        start, end = match.span()
        if start > last_index:
            intermediate = text[last_index:start]
            chunks.extend(process_inline_by_line(intermediate, allow_latex))
        content = match.group(2) or match.group(4)
        if content:
            chunks.append(MessageChunk(type="latex", text=content.strip()))
        last_index = end
    if last_index < len(text):
        remaining = text[last_index:]
        chunks.extend(process_inline_by_line(remaining, allow_latex))
    return chunks

def process_inline_by_line(text: str, allow_latex: bool) -> List[MessageChunk]:
    chunks = []
    lines = text.splitlines()
    inline_latex_pattern = re.compile(
        r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)|\\\((.+?)\\\)|\\\[(.+?)\\\]',
        re.DOTALL
    )
    for line in lines:
        if not line.strip():
            continue
        if not allow_latex:
            chunks.append(MessageChunk(type="text", text=line))
            continue

        subchunks = []
        last_index = 0
        for m in inline_latex_pattern.finditer(line):
            start, end = m.span()
            if start > last_index:
                plain = line[last_index:start]
                if plain:
                    subchunks.append(MessageChunk(type="text", text=plain))
            latex_str = m.group(0)
            content = latex_str[1:-1].strip() if latex_str.startswith("$") else latex_str[2:-2].strip()
            subchunks.append(MessageChunk(type="latex_inline", text=content))
            last_index = end
        if last_index < len(line):
            remaining = line[last_index:]
            if remaining:
                subchunks.append(MessageChunk(type="text", text=remaining))
        if len(subchunks) == 1 and subchunks[0].type == "text":
            chunks.append(subchunks[0])
        elif subchunks:
            chunks.append(MessageChunk(type="inline_chunks", text="", subchunks=subchunks))
    return chunks

def process_text_segment_no_think(text: str, allow_latex: bool) -> List[MessageChunk]:
    text = text.strip()
    if not text:
        return []
    
    # First extract tables, then process remaining text
    chunks = []
    for chunk in extract_tables(text):
        if chunk.type == "table":
            chunks.append(chunk)
        elif chunk.type == "text" and allow_latex:
            chunks.extend(process_text_with_display_latex(chunk.text, allow_latex))
        else:
            chunks.extend(process_inline_by_line(chunk.text, allow_latex))
    return chunks

def process_text_segment(text: str, allow_latex: bool) -> List[MessageChunk]:
    chunks = []
    think_pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
    last_index = 0
    for m in think_pattern.finditer(text):
        start, end = m.span()
        if start > last_index:
            pre_text = text[last_index:start]
            chunks.extend(process_text_segment_no_think(pre_text, allow_latex))
        think_content = m.group(1).strip()
        chunks.append(MessageChunk(type="thinking", text=think_content))
        last_index = end
    if last_index < len(text):
        remainder = text[last_index:]
        chunks.extend(process_text_segment_no_think(remainder, allow_latex))
    return chunks

def get_message_chunks(message: str, allow_latex: bool = True) -> List[MessageChunk]:
    chunks = []
    codeblock_pattern = re.compile(r'```(\w+)?\n(.*?)\n```', re.DOTALL)
    last_end = 0
    for match in codeblock_pattern.finditer(message):
        start, end = match.span()
        if start > last_end:
            pre_text = message[last_end:start]
            for chunk in process_text_segment(pre_text, allow_latex):
                append_chunk(chunks, chunk)
        lang = match.group(1) if match.group(1) else ""
        code = match.group(2)
        append_chunk(chunks, MessageChunk(type="codeblock", text=code, lang=lang))
        last_end = end
    if last_end < len(message):
        post_text = message[last_end:]
        for chunk in process_text_segment(post_text, allow_latex):
            append_chunk(chunks, chunk)
    return chunks

# Test the code
if __name__ == "__main__":
    test_message = """
Here's a table:

| Header 1 | Header 2 |
|----------|----------|
| Cell 1   | Cell 2   |

Some text after the table.
"""
    chunks = get_message_chunks(test_message)
    for chunk in chunks:
        print(str(chunk))
