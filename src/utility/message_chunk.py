import re
from dataclasses import dataclass

@dataclass
class MessageChunk:
    """Class representing a chunk of a message

    Attributes: 
        type: can be one of "codeblock", "table", "latex_inline", "text" 
        text: the text content (for codeblocks, the code inside the block) 
        lang: (only for codeblock) the language specified (or empty string) 
    """
    type: str   
    text: str
    lang: str = '' 

    def __str__(self):
        if self.type == "codeblock":
            return f"<CodeBlock lang='{self.lang}'>\n{self.text}\n</CodeBlock>"
        elif self.type == "table":
            return f"<Table>\n{self.text}\n</Table>"
        elif self.type == "latex_inline":
            return f"<LatexInline>{self.text}</LatexInline>"
        elif self.type == "text":
            return f"<Text>{self.text}</Text>"
        else:
            return f"<{self.type}>{self.text}</{self.type}>"

def append_chunk(chunks: list[MessageChunk], new_chunk: MessageChunk):
    """
    Append new_chunk to chunks. If both the last chunk in chunks and new_chunk are
    of type 'text', merge them.
    """
    if chunks and chunks[-1].type == "text" and new_chunk.type == "text":
        # Merge the text
        chunks[-1].text += new_chunk.text
    else:
        chunks.append(new_chunk)

def get_message_chunks(message: str) -> list[MessageChunk]:
    """
    Splits the input message into a list of MessageChunks.
    
    A MessageChunk is a MessageChunk object with:
      - type: one of "codeblock", "table", "latex_inline", "text"
      - text: the text content (for codeblocks, the code inside the block)
      - lang: (only for codeblock) the language specified (or empty string)
    
    Consecutive text chunks are merged.
    """
    chunks = []
    # Pattern for fenced codeblocks (```language\ncode\n```)
    codeblock_pattern = re.compile(r'```(\w+)?\n(.*?)\n```', re.DOTALL)
    
    last_end = 0
    for match in codeblock_pattern.finditer(message):
        start, end = match.span()
        # Process any text before the codeblock
        if start > last_end:
            pre_text = message[last_end:start]
            for c in process_text_segment(pre_text):
                append_chunk(chunks, c)
        lang = match.group(1) if match.group(1) else ""
        code = match.group(2)
        append_chunk(chunks, MessageChunk(type="codeblock", text=code, lang=lang))
        last_end = end
        
    # Process any text after the last codeblock.
    if last_end < len(message):
        post_text = message[last_end:]
        for c in process_text_segment(post_text):
            append_chunk(chunks, c)
    
    return chunks

def process_text_segment(text: str) -> list[MessageChunk]:
    """
    Process a block of text (which is not inside a codeblock) and
    further split it into either a markdown table, inline LaTeX equations, 
    or plain text chunks.
    
    Consecutive text chunks in this processing will be merged by the caller.
    """
    chunks = []
    # Split by double newlines to get separate blocks.
    blocks = re.split(r'\n\s*\n', text)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if is_markdown_table(block):
            chunks.append(MessageChunk(type="table", text=block))
        else:
            # Process inline LaTeX in the block.
            chunks.extend(process_inline(block))
    return chunks

def is_markdown_table(block: str) -> bool:
    """
    A simple heuristic for markdown tables:
    - The block has at least two lines.
    - The second line contains mostly dashes, colons, pipes, and whitespace.
    """
    lines = block.splitlines()
    if len(lines) < 2:
        return False
    sep_line = lines[1].strip()
    # Check if the separator line is made up of '-', ':', '|', and spaces.
    if re.fullmatch(r'[\s\-\:\|]+', sep_line):
        # Also check that the first line contains at least one pipe character.
        if '|' in lines[0]:
            return True
    return False

def process_inline(text: str) -> list[MessageChunk]:
    """
    Splits the text on inline LaTeX equations.
    Inline equations are defined as text enclosed in either:
      - $...$
      - \\( ... \\)
      - \\[ ... \\]
    
    Returns a list of MessageChunks of type "latex_inline" or "text".
    Consecutive text parts are left to be merged by the caller.
    """
    chunks = []
    # Regex that matches any of the inline LaTeX patterns.
    # Note: This simple pattern does not handle escaped delimiters.
    latex_pattern = re.compile(r'(\$.*?\$|\\\(.+?\\\)|\\\[.+?\\\])')
    
    last_index = 0
    for m in latex_pattern.finditer(text):
        start, end = m.span()
        # Any text before the LaTeX equation becomes a normal text chunk.
        if start > last_index:
            normal_text = text[last_index:start]
            if normal_text:
                chunks.append(MessageChunk(type="text", text=normal_text))
        latex_equation = m.group(0)
        # Remove the surrounding delimiters.
        if latex_equation.startswith("$"):
            content = latex_equation.strip("$")
        elif latex_equation.startswith("\\("):
            content = latex_equation[2:-2]
        elif latex_equation.startswith("\\["):
            content = latex_equation[2:-2]
        else:
            content = latex_equation
        chunks.append(MessageChunk(type="latex_inline", text=content))
        last_index = end
    # Any remaining text after the last LaTeX match.
    if last_index < len(text):
        remainder = text[last_index:]
        if remainder:
            chunks.append(MessageChunk(type="text", text=remainder))
    return chunks
