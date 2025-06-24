import re
from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class MessageChunk:
    type: str  # "codeblock", "table", "latex", "latex_inline", "inline_chunks", "thinking", or "text"
    text: str
    lang: str = ''  # Only used for codeblocks
    subchunks: Optional[List['MessageChunk']] = field(default_factory=list)

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
            # Represent subchunks indented for clarity
            sub_repr = "\n".join("  " + str(sc).replace("\n", "\n  ") for sc in self.subchunks) if self.subchunks else ""
            return f"<InlineChunks>\n{sub_repr}\n</InlineChunks>"
        elif self.type == "thinking":
            return f"<Thinking>{self.text}</Thinking>"
        elif self.type == "text":
            # Ensure text content doesn't have unintended newlines for simple strings
            # For multi-line text, keep them. Let's just represent the text directly.
            return f"<Text>{self.text}</Text>"
        else:
            # Fallback for unknown types
            return f"<{self.type}>{self.text}</{self.type}>"

# ============================================================
# Chunk Processing Logic (Mostly unchanged, but crucial for context)
# ============================================================

def append_chunk(chunks: List[MessageChunk], new_chunk: MessageChunk):
    """Appends a chunk, merging consecutive text chunks."""
    if new_chunk.type == "text" and chunks and chunks[-1].type == "text":
        # Merge consecutive text chunks. Add a newline ONLY if the previous
        # text didn't already end with one AND the new text doesn't start with one.
        # Simplest reliable merge: always add a newline separator, assuming
        # append_chunk is called when a logical separation (like end of processing
        # a line or segment) occurs.
        chunks[-1].text += "\n" + new_chunk.text
    elif new_chunk.type == "text" and new_chunk.text == "" and chunks and chunks[-1].type == "text":
        # Handle adding a blank line explicitly. If last was text, add a newline.
         chunks[-1].text += "\n"
    elif new_chunk.type == "text" and new_chunk.text == "":
         # If it's an empty text chunk and the previous wasn't text, or it's the first chunk,
         # it represents a blank line. Append it, but maybe filter later? Let's keep it for now.
         chunks.append(new_chunk)
    else:
        # Append non-text chunks or the first text chunk, or text after non-text
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
    # More robust check for separator structure
    # Remove leading/trailing pipe and whitespace for splitting
    sep_line_stripped = sep_line.strip().strip('|').strip()
    parts = [part.strip() for part in sep_line_stripped.split('|')]
    # All parts must match the pattern of dashes and optional colons
    if not all(re.match(r':?-+:?$', part) for part in parts if part):
         # Allow for simpler patterns like just '---' if split resulted in single element
         if not re.match(r'[-:| ]+', sep_line): # Fallback basic check
              return False

    # Verify consistent number of columns more carefully
    header_parts = [p.strip() for p in lines[0].strip('|').split('|')]
    sep_parts_check = [p.strip() for p in sep_line.strip('|').split('|')]

    num_header_cols = len(header_parts)
    num_sep_cols = len(sep_parts_check)

    # Ensure header and separator have the same number of columns > 0
    return num_header_cols == num_sep_cols and num_header_cols > 0


def extract_tables(text: str) -> List[MessageChunk]:
    """
    Extracts markdown tables from text and returns chunks with remaining text.
    Uses append_chunk logic internally now for better text merging.
    """
    chunks = []
    lines = text.splitlines() # Split into lines
    last_line_processed = 0 # Index in the `lines` list

    i = 0
    while i < len(lines):
        # Check if line 'i' could be a table header and 'i+1' a separator
        potential_header = lines[i].strip()
        if '|' in potential_header and i + 1 < len(lines):
            potential_separator = lines[i+1].strip()
            # Basic separator check
            if re.match(r'^\|? *[-:| ]+ *\|?$', potential_separator):
                potential_table_lines = [lines[i], lines[i+1]]
                j = i + 2
                while j < len(lines) and '|' in lines[j].strip():
                    potential_table_lines.append(lines[j])
                    j += 1

                potential_table_block = '\n'.join(potential_table_lines)
                if is_markdown_table(potential_table_block):
                    # Add preceding text chunk if any
                    if last_line_processed < i:
                        pre_text = '\n'.join(lines[last_line_processed:i])
                        append_chunk(chunks, MessageChunk(type="text", text=pre_text))

                    # Add the table chunk (don't use append_chunk for non-text)
                    chunks.append(MessageChunk(type="table", text=potential_table_block))
                    last_line_processed = j
                    i = j
                    continue # Skip increment

        # Not a table start
        i += 1

    # Add any remaining text after the last table
    if last_line_processed < len(lines):
        remaining_text = '\n'.join(lines[last_line_processed:])
        append_chunk(chunks, MessageChunk(type="text", text=remaining_text))

    # Re-add trailing newline if original text had one and it got lost
    if text.endswith('\n') and chunks and chunks[-1].type == "text":
         if not chunks[-1].text.endswith('\n'):
              chunks[-1].text += '\n'
    # Filter out potentially fully empty text chunks added by mistake?
    # Let's keep them for now, append_chunk tries to handle structure.
    return [c for c in chunks if c.type != "text" or c.text != ""]


_display_latex_pattern = re.compile(r'(\$\$(.+?)\$\$)|(\\\[(.+?)\\\])', re.DOTALL)

def process_text_with_display_latex(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes text for display latex, passing segments to inline processor."""
    if not allow_latex:
        return process_inline_elements(text, allow_latex=False)

    chunks = []
    last_index = 0
    for match in _display_latex_pattern.finditer(text):
        start, end = match.span()
        if start > last_index:
            intermediate_text = text[last_index:start]
            # Use append_chunk logic when extending
            processed_intermediate = process_inline_elements(intermediate_text, allow_latex)
            for chunk in processed_intermediate:
                append_chunk(chunks, chunk)


        content = match.group(2) or match.group(4)
        if content is not None:
             # Add latex chunk directly (not via append_chunk)
             chunks.append(MessageChunk(type="latex", text=content.strip()))
        last_index = end

    if last_index < len(text):
        remaining_text = text[last_index:]
        processed_remaining = process_inline_elements(remaining_text, allow_latex)
        for chunk in processed_remaining:
            append_chunk(chunks, chunk)

    return chunks


def process_inline_elements(text: str, allow_latex: bool) -> List[MessageChunk]:
    """
    Processes text segment for inline latex ($...$ or \\(...\\)).
    Returns a flat list of Text and LatexInline chunks for this segment.
    The calling function will decide how to integrate these.
    """
    chunks = []
    if not allow_latex:
        if text: # Avoid adding chunk for empty string
            chunks.append(MessageChunk(type="text", text=text))
        return chunks

    # Pattern for $...$ (excluding $$) and \(...\)
    # Ensures $ is not preceded/followed by $ or backslash (for $$ or \$)
    inline_latex_pattern = re.compile(
        r'(?<![\$\\])\$(?!\$)(.+?)(?<![\$\\])\$(?!\$)|' # $...$ (no $$ or escaped \$)
        r'\\\((.+?)\\\)',                               # \(...\)
        re.DOTALL # Allow matching across newlines? Usually inline latex is single line. Let's remove DOTALL.
    )
    # Corrected inline_latex_pattern without DOTALL
    inline_latex_pattern = re.compile(
        r'(?<![\$\\])\$(?!\$)(.+?)(?<![\$\\])\$(?!\$)|' # $...$ (no $$ or escaped \$)
        r'\\\((.+?)\\\)'                               # \(...\)
    )


    last_index = 0
    for m in inline_latex_pattern.finditer(text):
        start, end = m.span()
        # Add text before the match
        if start > last_index:
            plain_text = text[last_index:start]
            chunks.append(MessageChunk(type="text", text=plain_text))

        # Add the inline latex chunk
        content = m.group(1) or m.group(2) # Group 1 for $..$, Group 2 for \(..\)
        if content is not None:
            equation = content.strip()
            if len(content) < 40:
                chunks.append(MessageChunk(type="latex_inline", text=content.strip()))
            else:
                chunks.append(MessageChunk(type="latex", text=content.strip()))
        last_index = end

    # Add any remaining text after the last match
    if last_index < len(text):
        remaining_text = text[last_index:]
        chunks.append(MessageChunk(type="text", text=remaining_text))

    # Filter out empty text chunks that might result from regex artifacts
    return [c for c in chunks if c.type != "text" or c.text != ""]


def process_text_segment_no_think(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes a text segment without think tags, handling tables and latex."""
    if not text:
        return []

    # This function produces a FLAT list of basic chunks (Text, Table, Latex, LatexInline)
    # The grouping into InlineChunks happens later.
    final_chunks = []
    table_and_text_parts = extract_tables(text) # Returns Text and Table chunks

    for chunk in table_and_text_parts:
        if chunk.type == "table":
            final_chunks.append(chunk) # Add table directly
        elif chunk.type == "text":
            # Process this text part for display and inline latex
            latex_processed_parts = process_text_with_display_latex(chunk.text, allow_latex)
            # Extend the final list. These parts are already Text, Latex, LatexInline
            final_chunks.extend(latex_processed_parts) # Extend, don't use append_chunk here

    return final_chunks


def process_text_segment(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes text segment potentially containing <think> tags."""
    # This function also produces a FLAT list, handling think tags.
    flat_chunks = []
    think_pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
    last_index = 0

    for m in think_pattern.finditer(text):
        start, end = m.span()
        if start > last_index:
            pre_text = text[last_index:start]
            # Get flat list for segment before think tag
            processed_pre_chunks = process_text_segment_no_think(pre_text, allow_latex)
            flat_chunks.extend(processed_pre_chunks)

        # Add the think chunk directly
        think_content = m.group(1).strip()
        if think_content: # Avoid empty think tags? Or keep them? Keep for now.
            flat_chunks.append(MessageChunk(type="thinking", text=think_content))
        last_index = end

    # Process text after the last think block
    if last_index < len(text):
        remainder = text[last_index:].lstrip("\n")
        processed_post_chunks = process_text_segment_no_think(remainder, allow_latex)
        flat_chunks.extend(processed_post_chunks)

    return flat_chunks


# ============================================================
# Main Function with Fix (Grouping Step Added)
# ============================================================
def get_message_chunks(message: str, allow_latex: bool = True) -> List[MessageChunk]:
    """
    Main function to parse message into chunks. Includes a final step
    to group consecutive Text and LatexInline chunks into InlineChunks.
    """
    # Step 1: Parse Code Blocks and process intermediate text segments
    flat_chunks = []
    codeblock_pattern = re.compile(r'^\s*```(\w*)\s*\n(.*?)\n^\s*```\s*$', re.DOTALL | re.MULTILINE)
    last_end = 0

    for match in codeblock_pattern.finditer(message):
        start, end = match.span()
        # Process text before the code block -> returns flat list
        if start > last_end:
            pre_text = message[last_end:start]
            processed_pre_chunks = process_text_segment(pre_text, allow_latex)
            flat_chunks.extend(processed_pre_chunks)

        # Add the code block chunk
        lang = match.group(1).strip() if match.group(1) else ""
        code = match.group(2)
        flat_chunks.append(MessageChunk(type="codeblock", text=code, lang=lang))
        last_end = end

    # Process any remaining text after the last code block -> returns flat list
    if last_end < len(message):
        post_text = message[last_end:]
        processed_post_chunks = process_text_segment(post_text, allow_latex)
        flat_chunks.extend(processed_post_chunks)

    # Step 2: Group consecutive Text and LatexInline chunks into InlineChunks
    grouped_chunks = []
    current_inline_sequence = []

    # Merge adjacent text chunks in the flat list *first* before grouping
    # This simplifies the grouping logic slightly. Use append_chunk's logic.
    merged_flat_chunks = []
    for chunk in flat_chunks:
         # Apply merging logic as if we were building the list with append_chunk
         if chunk.type == "text" and merged_flat_chunks and merged_flat_chunks[-1].type == "text":
             merged_flat_chunks[-1].text += "\n" + chunk.text # Simple newline merge for now
         elif chunk.type == "text" and chunk.text == "" and merged_flat_chunks and merged_flat_chunks[-1].type == "text":
             merged_flat_chunks[-1].text += "\n" # Represent blank line
         elif chunk.type == "text" and chunk.text == "":
             # Skip adding completely empty text chunks if they stand alone initially?
             # Let's keep them for now, might represent structure. Append if list is empty or prev is non-text
             if not merged_flat_chunks or merged_flat_chunks[-1].type != "text":
                  merged_flat_chunks.append(chunk)
         else:
             merged_flat_chunks.append(chunk)
    # Filter truly empty text chunks that might remain after merging
    merged_flat_chunks = [c for c in merged_flat_chunks if c.type != "text" or c.text != ""]

    # Now group the merged flat list
    for chunk in merged_flat_chunks:
        append_next = None
        is_inline_constituent = chunk.type in ("text", "latex_inline")

        # Check if the next block is a latex_inline
        if chunk.type == "text":
            current_index = merged_flat_chunks.index(chunk)
            if current_index < len(merged_flat_chunks) - 2:
                next_chunk = merged_flat_chunks[current_index + 1]
                if next_chunk.type == "latex_inline":
                    lines = chunk.text.split("\n")
                    if len([line for line in lines if line != "" ]) > 1:
                        chunk.text = "\n".join(lines[:-1])
                        append_next = MessageChunk(type="text", text=lines[-1])
                        is_inline_constituent = False
        if is_inline_constituent:
            current_inline_sequence.append(chunk)
        else:
            # End of an inline sequence (or none existed)
            if current_inline_sequence:
                # Check if the sequence qualifies for wrapping
                is_mixed_or_multiple = (len(current_inline_sequence) > 1 or
                                        any(c.type == "latex_inline" for c in current_inline_sequence))

                if is_mixed_or_multiple:
                    grouped_chunks.append(MessageChunk(type="inline_chunks", text="", subchunks=current_inline_sequence))
                else:
                    # Only a single text chunk, add it directly
                    grouped_chunks.append(current_inline_sequence[0])
                current_inline_sequence = [] # Reset sequence
            if append_next is not None:
                current_inline_sequence.append(append_next)
                
            # Add the non-inline chunk
            grouped_chunks.append(chunk)

    # After the loop, handle any remaining inline sequence
    if current_inline_sequence:
        is_mixed_or_multiple = (len(current_inline_sequence) > 1 or
                                any(c.type == "latex_inline" for c in current_inline_sequence))
        if is_mixed_or_multiple:
            grouped_chunks.append(MessageChunk(type="inline_chunks", text="", subchunks=current_inline_sequence))
        else:
             # Only a single text chunk
            grouped_chunks.append(current_inline_sequence[0])

    return grouped_chunks
