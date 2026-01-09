import re
import json
from dataclasses import dataclass, field
from typing import List, Optional, Any

@dataclass
class MessageChunk:
    type: str  # "codeblock", "table", "latex", "latex_inline", "inline_chunks", "thinking", "text", "tool_call"
    text: str
    lang: str = ''  # Only used for codeblocks
    tool_name: str = '' # Only used for tool_call
    tool_args: dict = field(default_factory=dict) # Only used for tool_call
    subchunks: Optional[List['MessageChunk']] = field(default_factory=list)

    def __str__(self):
        if self.type == "codeblock":
            return f"<CodeBlock lang='{self.lang}'>\n{self.text}\n</CodeBlock>"
        elif self.type == "tool_call":
            return f"<ToolCall name='{self.tool_name}'>\n{json.dumps(self.tool_args, indent=2)}\n</ToolCall>"
        elif self.type == "table":
            return f"<Table>\n{self.text}\n</Table>"
        elif self.type == "latex":
            return f"<LatexDisplay>\n{self.text}\n</LatexDisplay>"
        elif self.type == "latex_inline":
            return f"<LatexInline>{self.text}</LatexInline>"
        elif self.type == "inline_chunks":
            # Represent subchunks indented for clarity
            sub_repr = "\n".join("  " + str(sc).replace("\n", "\n  ") for sc in self.subchunks) if self.subchunks else ""
            return f"<InlineChunks>\n{sub_repr}\n</InlineChunks>"
        elif self.type == "thinking":
            return f"<Thinking>{self.text}</Thinking>"
        elif self.type == "text":
            return f"<Text>{self.text}</Text>"
        else:
            return f"<{self.type}>{self.text}</{self.type}>"

# ============================================================
# Constants & Patterns
# ============================================================

# Matches ```lang\ncontent``` (non-greedy content).
# Relaxed to allow codeblocks that don't end with a newline before the closing fence.
_CODEBLOCK_PATTERN = re.compile(r'```(\w*)\s*\n(.*?)\n\s*```', re.DOTALL)

_DISPLAY_LATEX_PATTERN = re.compile(r'(\$\$(.+?)\$\$)|(\\\[(.+?)\\\])', re.DOTALL)

_INLINE_LATEX_PATTERN = re.compile(
    r'(?<![\$\\])\$(?!\$)(.+?)(?<![\$\\])\$(?!\$)|' 
    r'\\\((.+?)\\\)'                              
)

_THINK_PATTERN = re.compile(r'<think>(.*?)</think>', re.DOTALL)

_TOOL_START_PATTERN = re.compile(r'\{\s*"(?:tool|name|function)"\s*:', re.MULTILINE)

# ============================================================
# Chunk Processing Logic
# ============================================================

def append_chunk(chunks: List[MessageChunk], new_chunk: MessageChunk):
    """Appends a chunk, merging consecutive text chunks."""
    if not chunks:
        chunks.append(new_chunk)
        return

    last_chunk = chunks[-1]
    if new_chunk.type == "text" and last_chunk.type == "text":
        if new_chunk.text:
            last_chunk.text += "\n" + new_chunk.text
        else:
            last_chunk.text += "\n"
    else:
        chunks.append(new_chunk)


def is_markdown_table(block: str) -> bool:
    lines = [line.strip() for line in block.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    if '|' not in lines[0]:
        return False
    sep_line = lines[1]
    sep_line_stripped = sep_line.strip().strip('|').strip()
    parts = [part.strip() for part in sep_line_stripped.split('|')]
    if not all(re.match(r':?-+:?$', part) for part in parts if part):
         if not re.match(r'[-:| ]+', sep_line):
              return False
    header_parts = [p.strip() for p in lines[0].strip('|').split('|')]
    sep_parts_check = [p.strip() for p in sep_line.strip('|').split('|')]
    num_header_cols = len(header_parts)
    num_sep_cols = len(sep_parts_check)
    return num_header_cols == num_sep_cols and num_header_cols > 0


def extract_tables(text: str) -> List[MessageChunk]:
    chunks = []
    lines = text.splitlines()
    last_line_processed = 0

    i = 0
    while i < len(lines):
        potential_header = lines[i].strip()
        if '|' in potential_header and i + 1 < len(lines):
            potential_separator = lines[i+1].strip()
            if re.match(r'^\|? *[-:| ]+ *\|?$', potential_separator):
                potential_table_lines = [lines[i], lines[i+1]]
                j = i + 2
                while j < len(lines) and '|' in lines[j].strip():
                    potential_table_lines.append(lines[j])
                    j += 1

                potential_table_block = '\n'.join(potential_table_lines)
                if is_markdown_table(potential_table_block):
                    if last_line_processed < i:
                        pre_text = '\n'.join(lines[last_line_processed:i])
                        append_chunk(chunks, MessageChunk(type="text", text=pre_text))

                    chunks.append(MessageChunk(type="table", text=potential_table_block))
                    last_line_processed = j
                    i = j
                    continue 

        i += 1

    if last_line_processed < len(lines):
        remaining_text = '\n'.join(lines[last_line_processed:])
        append_chunk(chunks, MessageChunk(type="text", text=remaining_text))

    if text.endswith('\n') and chunks and chunks[-1].type == "text":
         if not chunks[-1].text.endswith('\n'):
              chunks[-1].text += '\n'
    return [c for c in chunks if c.type != "text" or c.text != ""]


def process_text_with_display_latex(text: str, allow_latex: bool) -> List[MessageChunk]:
    if not allow_latex:
        return process_inline_elements(text, allow_latex=False)

    chunks = []
    last_index = 0
    for match in _DISPLAY_LATEX_PATTERN.finditer(text):
        start, end = match.span()
        if start > last_index:
            intermediate_text = text[last_index:start]
            processed_intermediate = process_inline_elements(intermediate_text, allow_latex)
            for chunk in processed_intermediate:
                append_chunk(chunks, chunk)

        content = match.group(2) or match.group(4)
        if content is not None:
             chunks.append(MessageChunk(type="latex", text=content.strip()))
        last_index = end

    if last_index < len(text):
        remaining_text = text[last_index:]
        processed_remaining = process_inline_elements(remaining_text, allow_latex)
        for chunk in processed_remaining:
            append_chunk(chunks, chunk)

    return chunks


def process_inline_elements(text: str, allow_latex: bool) -> List[MessageChunk]:
    chunks = []
    if not allow_latex:
        if text: 
            chunks.append(MessageChunk(type="text", text=text))
        return chunks

    last_index = 0
    for m in _INLINE_LATEX_PATTERN.finditer(text):
        start, end = m.span()
        if start > last_index:
            plain_text = text[last_index:start]
            chunks.append(MessageChunk(type="text", text=plain_text))

        content = m.group(1) or m.group(2)
        if content is not None:
            if len(content) < 40:
                chunks.append(MessageChunk(type="latex_inline", text=content.strip()))
            else:
                chunks.append(MessageChunk(type="latex", text=content.strip()))
        last_index = end

    if last_index < len(text):
        remaining_text = text[last_index:]
        chunks.append(MessageChunk(type="text", text=remaining_text))

    return [c for c in chunks if c.type != "text" or c.text != ""]


def process_text_segment_no_think(text: str, allow_latex: bool) -> List[MessageChunk]:
    if not text:
        return []

    final_chunks = []
    table_and_text_parts = extract_tables(text) 

    for chunk in table_and_text_parts:
        if chunk.type == "table":
            final_chunks.append(chunk) 
        elif chunk.type == "text":
            latex_processed_parts = process_text_with_display_latex(chunk.text, allow_latex)
            final_chunks.extend(latex_processed_parts) 

    return final_chunks


def process_text_segment(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes text segment potentially containing <think> tags."""
    flat_chunks = []
    last_index = 0

    for m in _THINK_PATTERN.finditer(text):
        start, end = m.span()
        if start > last_index:
            pre_text = text[last_index:start]
            processed_pre_chunks = process_text_segment_no_think(pre_text, allow_latex)
            flat_chunks.extend(processed_pre_chunks)

        think_content = m.group(1).strip()
        if think_content: 
            flat_chunks.append(MessageChunk(type="thinking", text=think_content))
        last_index = end

    if last_index < len(text):
        remainder = text[last_index:].lstrip("\n")
        processed_post_chunks = process_text_segment_no_think(remainder, allow_latex)
        flat_chunks.extend(processed_post_chunks)

    return flat_chunks

# ============================================================
# Tool Call Parsing Logic
# ============================================================

def parse_potential_tool_json(text: str) -> Optional[dict]:
    """
    Attempts to parse text as a JSON tool call. 
    Returns the dict if it's a valid JSON object containing 'name' or 'tool'.
    """
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            # Check for standard tool call indicators
            if "name" in obj or "tool" in obj or "function" in obj:
                return obj
    except json.JSONDecodeError:
        pass
    return None

def find_tool_calls(text: str) -> List[MessageChunk]:
    """
    Extracts tool calls in JSON format from raw text.
    Handles { "name": ... } or { "tool": ... }
    """
    chunks = []
    last_end = 0
    
    for match in _TOOL_START_PATTERN.finditer(text):
        start_index = match.start()
        
        if start_index < last_end:
            continue
        
        if start_index > last_end:
            chunks.append(MessageChunk(type="text", text=text[last_end:start_index]))
            
        stack = 0
        found = False
        for i in range(start_index, len(text)):
            if text[i] == '{':
                stack += 1
            elif text[i] == '}':
                stack -= 1
                if stack == 0:
                    candidate = text[start_index:i+1]
                    # Use shared helper to validate
                    tool_obj = parse_potential_tool_json(candidate)
                    if tool_obj:
                        tool_name = tool_obj.get("name", tool_obj.get("tool", tool_obj.get("function")))
                        tool_args = tool_obj.get("arguments", tool_obj.get("arguements", tool_obj.get("parameters", {})))
                        
                        chunks.append(MessageChunk(
                            type="tool_call",
                            text=candidate,
                            tool_name=tool_name,
                            tool_args=tool_args
                        ))
                        last_end = i + 1
                        found = True
                        break
        
        if not found and stack > 0:
            candidate = text[start_index:] + "}" * stack
            tool_obj = parse_potential_tool_json(candidate)
            if tool_obj:
                tool_name = tool_obj.get("name", tool_obj.get("tool", tool_obj.get("function")))
                tool_args = tool_obj.get("arguments", tool_obj.get("arguements", tool_obj.get("parameters", {})))
                
                chunks.append(MessageChunk(
                    type="tool_call",
                    text=candidate,
                    tool_name=tool_name,
                    tool_args=tool_args
                ))
                last_end = len(text)
                found = True

    if last_end < len(text):
        chunks.append(MessageChunk(type="text", text=text[last_end:]))
        
    return chunks

# ============================================================
# Main Function
# ============================================================

def get_message_chunks(message: str, allow_latex: bool = True) -> List[MessageChunk]:
    """
    Main function to parse message into chunks.
    Priority: CodeBlocks (checked for Tools) -> Naked Tools -> Markdown/Latex.
    """
    
    flat_chunks = []
    last_end = 0
    
    for match in _CODEBLOCK_PATTERN.finditer(message):
        start, end = match.span()
        
        # 1. Process text BEFORE the code block
        if start > last_end:
            pre_text = message[last_end:start]
            
            # Find "naked" tool calls in this text segment
            naked_tool_chunks = find_tool_calls(pre_text)
            
            for chunk in naked_tool_chunks:
                if chunk.type == "tool_call":
                    flat_chunks.append(chunk)
                else:
                    # Normal text: process for Tables, Latex, Thinking
                    flat_chunks.extend(process_text_segment(chunk.text, allow_latex))

        # 2. Process the CODE BLOCK content
        lang = match.group(1).strip() if match.group(1) else ""
        code_content = match.group(2)
        
        # Check if this code block is actually a tool call
        tool_obj = parse_potential_tool_json(code_content)
        
        if tool_obj:
            # It IS a tool call. Return as ToolCall chunk (removing codeblock formatting)
            tool_name = tool_obj.get("name", tool_obj.get("tool", tool_obj.get("function", "unknown")))
            tool_args = tool_obj.get("arguments", tool_obj.get("arguements", tool_obj.get("parameters", {})))
            flat_chunks.append(MessageChunk(
                type="tool_call",
                text=code_content,
                tool_name=tool_name,
                tool_args=tool_args
            ))
        else:
            # It is a regular code block
            flat_chunks.append(MessageChunk(type="codeblock", text=code_content, lang=lang))
            
        last_end = end

    # 3. Process remaining text after the last code block
    if last_end < len(message):
        post_text = message[last_end:]
        naked_tool_chunks = find_tool_calls(post_text)
        
        for chunk in naked_tool_chunks:
            if chunk.type == "tool_call":
                flat_chunks.append(chunk)
            else:
                flat_chunks.extend(process_text_segment(chunk.text, allow_latex))

    return _group_inline_chunks(flat_chunks)


def _group_inline_chunks(flat_chunks: List[MessageChunk]) -> List[MessageChunk]:
    """Groups consecutive Text and LatexInline chunks."""
    grouped_chunks = []
    current_inline_sequence = []

    # First pass: merge adjacent text chunks
    merged_flat_chunks = []
    for chunk in flat_chunks:
         if chunk.type == "text" and merged_flat_chunks and merged_flat_chunks[-1].type == "text":
             merged_flat_chunks[-1].text += "\n" + chunk.text
         elif chunk.type == "text" and chunk.text == "" and merged_flat_chunks and merged_flat_chunks[-1].type == "text":
             merged_flat_chunks[-1].text += "\n"
         elif chunk.type == "text" and chunk.text == "":
             if not merged_flat_chunks or merged_flat_chunks[-1].type != "text":
                  merged_flat_chunks.append(chunk)
         else:
             merged_flat_chunks.append(chunk)
             
    merged_flat_chunks = [c for c in merged_flat_chunks if c.type != "text" or c.text != ""]

    def _finalize_sequence():
        if not current_inline_sequence:
            return
        is_mixed_or_multiple = (len(current_inline_sequence) > 1 or
                                any(c.type == "latex_inline" for c in current_inline_sequence))
        if is_mixed_or_multiple:
            grouped_chunks.append(MessageChunk(type="inline_chunks", text="", subchunks=list(current_inline_sequence)))
        else:
            grouped_chunks.append(current_inline_sequence[0])
        current_inline_sequence.clear()

    # Second pass: group into InlineChunks
    i = 0
    while i < len(merged_flat_chunks):
        chunk = merged_flat_chunks[i]
        append_next = None
        is_inline_constituent = chunk.type in ("text", "latex_inline")

        if chunk.type == "text":
            # Heuristic: if next is latex_inline, and text ends with newline, maybe split it?
            # Replicating original logic: if next is latex_inline, check if text has multiple lines.
            if i + 1 < len(merged_flat_chunks):
                next_chunk = merged_flat_chunks[i+1]
                if next_chunk.type == "latex_inline":
                    lines = chunk.text.split("\n")
                    # If we have content lines, keep them in text, put last line as start of inline sequence?
                    non_empty_lines = [l for l in lines if l != ""]
                    if len(non_empty_lines) > 1:
                        # Original logic: "if len([line for line in lines if line != "" ]) > 1"
                        # It splits off the LAST line into `append_next`.
                        # The MAIN part (lines[:-1]) is modified in `chunk.text`.
                        # `is_inline_constituent` becomes False (so the main part is pushed to grouped_chunks immediately).
                        # `append_next` (the last line) is added to `current_inline_sequence`.
                        
                        chunk.text = "\n".join(lines[:-1])
                        append_next = MessageChunk(type="text", text=lines[-1])
                        is_inline_constituent = False
                        
        if is_inline_constituent:
            current_inline_sequence.append(chunk)
        else:
            _finalize_sequence()
            grouped_chunks.append(chunk)
            
            if append_next:
                current_inline_sequence.append(append_next)
                
        i += 1

    _finalize_sequence()

    return grouped_chunks
