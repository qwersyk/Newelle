import re
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class MessageChunk:
    type: str  # One of "codeblock", "table", "latex", "latex_inline", "text", or "inline_chunks"
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
        elif self.type == "text":
            return f"<Text>{self.text}</Text>"
        else:
            return f"<{self.type}>{self.text}</{self.type}>"

def append_chunk(chunks: List[MessageChunk], new_chunk: MessageChunk):
    """
    Append new_chunk to chunks. If the last chunk is a plain text chunk and new_chunk
    is also a plain text chunk, merge their text.
    """
    if chunks and chunks[-1].type == "text" and new_chunk.type == "text":
        chunks[-1].text += "\n" + new_chunk.text
    else:
        chunks.append(new_chunk)

def is_display_latex(block: str) -> bool:
    """
    Returns True if the entire block is a display LaTeX equation.
    Display equations are those that start and end with one of:
      - $$ ... $$
      - \[ ... \]
      - \( ... \)  (if the whole block is just that)
    """
    block = block.strip()
    if block.startswith("$$") and block.endswith("$$"):
        return True
    if block.startswith(r"\[") and block.endswith(r"\]"):
        return True
    if block.startswith(r"\(") and block.endswith(r"\)"):
        return True
    return False

def extract_display_latex(block: str) -> str:
    """
    Removes the display LaTeX delimiters from the block.
    """
    block = block.strip()
    if block.startswith("$$") and block.endswith("$$"):
        return block[2:-2].strip()
    if block.startswith(r"\[") and block.endswith(r"\]"):
        return block[2:-2].strip()
    if block.startswith(r"\(") and block.endswith(r"\)"):
        return block[2:-2].strip()
    return block

def process_inline_by_line(text: str) -> List[MessageChunk]:
    """
    Processes inline LaTeX for each line in the given text.
    For each line, it extracts pieces of plain text and inline LaTeX (using $...$ or \( ... \)).
    If more than one piece is found on the same line (or if inline LaTeX is present),
    group them into a single MessageChunk of type "inline_chunks" (using its 'subchunks' attribute).
    If the line is pure text, a plain text chunk is returned.
    """
    chunks = []
    # Regex to match inline LaTeX (using single $ delimiters—not $$—or \( ... \))
    inline_latex_pattern = re.compile(
        r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)|\\\((.+?)\\\)'
    )
    lines = text.splitlines()
    for line in lines:
        if not line:
            continue
        subchunks = []
        last_index = 0
        for m in inline_latex_pattern.finditer(line):
            start, end = m.span()
            # Text before the inline LaTeX match
            if start > last_index:
                plain = line[last_index:start]
                if plain:
                    subchunks.append(MessageChunk(type="text", text=plain))
            latex_str = m.group(0)
            if latex_str.startswith("$"):
                content = latex_str[1:-1].strip()
            elif latex_str.startswith(r"\("):
                content = latex_str[2:-2].strip()
            else:
                content = latex_str
            subchunks.append(MessageChunk(type="latex_inline", text=content))
            last_index = end
        # Any remaining text after the last match.
        if last_index < len(line):
            remaining = line[last_index:]
            if remaining:
                subchunks.append(MessageChunk(type="text", text=remaining))
        # If the line contains only a single plain text segment, return it as a text chunk.
        # Otherwise, group the pieces into an inline_chunks chunk.
        if len(subchunks) == 1 and subchunks[0].type == "text":
            chunks.append(MessageChunk(type="text", text=subchunks[0].text))
        else:
            chunks.append(MessageChunk(type="inline_chunks", text="", subchunks=subchunks))
    return chunks

def is_markdown_table(block: str) -> bool:
    """
    A simple heuristic for detecting markdown tables.
    The block must have at least two lines and the second line should be a separator
    made up of dashes, colons, pipes, and whitespace.
    """
    lines = block.splitlines()
    if len(lines) < 2:
        return False
    sep_line = lines[1].strip()
    if re.fullmatch(r'[\s\-\:\|]+', sep_line) and '|' in lines[0]:
        return True
    return False

def process_text_segment(text: str) -> List[MessageChunk]:
    """
    Processes a segment of text (outside of codeblocks) and splits it into chunks.
    The function first splits the text by double newlines (to separate blocks).
    Then, for each block:
      - If it is a markdown table, returns a table chunk.
      - If it is a display LaTeX equation, returns a latex chunk.
      - Otherwise, it is processed line by line to extract inline elements.
    """
    chunks = []
    blocks = re.split(r'\n\s*\n', text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if is_markdown_table(block):
            chunks.append(MessageChunk(type="table", text=block))
        elif is_display_latex(block):
            latex_content = extract_display_latex(block)
            chunks.append(MessageChunk(type="latex", text=latex_content))
        else:
            # Process inline elements line by line.
            inline_line_chunks = process_inline_by_line(block)
            chunks.extend(inline_line_chunks)
    return chunks

def get_message_chunks(message: str) -> List[MessageChunk]:
    """
    Splits the input message into a list of MessageChunk objects.
    
    Processing order:
      1. Extract codeblocks (fenced with ``` ... ```).
      2. For remaining text, split by double newlines.
         Each block is then checked:
           - If it's a markdown table → type "table".
           - If it's a display LaTeX equation → type "latex".
           - Otherwise, process line by line for inline elements.
    
    Plain text chunks on the same line that contain inline elements are grouped
    into an "inline_chunks" chunk with a 'subchunks' list.
    """
    chunks = []
    codeblock_pattern = re.compile(r'```(\w+)?\n(.*?)\n```', re.DOTALL)
    
    last_end = 0
    for match in codeblock_pattern.finditer(message):
        start, end = match.span()
        # Process any text before the codeblock.
        if start > last_end:
            pre_text = message[last_end:start]
            for chunk in process_text_segment(pre_text):
                append_chunk(chunks, chunk)
        lang = match.group(1) if match.group(1) else ""
        code = match.group(2)
        append_chunk(chunks, MessageChunk(type="codeblock", text=code, lang=lang))
        last_end = end
        
    # Process any remaining text after the last codeblock.
    if last_end < len(message):
        post_text = message[last_end:]
        for chunk in process_text_segment(post_text):
            append_chunk(chunks, chunk)
            
    return chunks
