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
            sub = "\n".join("  " + str(sc) for sc in self.subchunks) if self.subchunks else ""
            return f"<InlineChunks>\n{sub}\n</InlineChunks>"
        elif self.type == "thinking":
            return f"<Thinking>{self.text}</Thinking>"
        elif self.type == "text":
            return f"<Text>{self.text}</Text>"
        else:
            return f"<{self.type}>{self.text}</{self.type}>"

def append_chunk(chunks: List[MessageChunk], new_chunk: MessageChunk):
    """Appends a chunk, merging consecutive text chunks."""
    if new_chunk.type == "text" and chunks and chunks[-1].type == "text":
        # Merge consecutive text chunks, separated by a newline.
        # This handles cases like Text("A"), Text("") -> Text("A\n")
        # And Text("A\n"), Text("B") -> Text("A\n\nB")
        # And Text("A"), Text("B") -> Text("A\nB")
        # Simplest merge: always add a newline before appending non-empty new text
        # If the new text is empty, add a newline to represent the blank line
        # Correct logic: Append newline *then* the new text content
        chunks[-1].text += "\n" + new_chunk.text
    elif new_chunk.type == "text" and new_chunk.text == "" and chunks and chunks[-1].type != "text":
         # Avoid adding a single empty text chunk if the previous wasn't text,
         # unless it's the very first chunk. Handle this potential edge case?
         # Let's append it for now, it represents a newline.
         chunks.append(new_chunk)
    # Don't merge if the new chunk is not text or the last chunk wasn't text
    else:
        # Append non-text chunks or the first text chunk
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
    """
    chunks = []
    lines = text.splitlines() # Split into lines, removes trailing newline implicitly
    last_line_processed = 0 # Index in the `lines` list

    i = 0
    while i < len(lines):
        # Check if line 'i' could be a table header and 'i+1' a separator
        potential_header = lines[i].strip()
        if '|' in potential_header and i + 1 < len(lines):
            potential_separator = lines[i+1].strip()
            # Basic separator check (must contain dashes/pipes/colons)
            if re.match(r'^\|? *[-:| ]+ *\|?$', potential_separator):
                # Tentatively start building table
                potential_table_lines = [lines[i], lines[i+1]]
                j = i + 2
                # Continue adding lines that look like table rows (contain '|')
                while j < len(lines) and '|' in lines[j].strip():
                    potential_table_lines.append(lines[j])
                    j += 1

                potential_table_block = '\n'.join(potential_table_lines)
                # Validate the entire block as a table
                if is_markdown_table(potential_table_block):
                    # Add preceding text chunk if any
                    if last_line_processed < i:
                        pre_text = '\n'.join(lines[last_line_processed:i])
                        # Keep even if just whitespace, append_chunk will handle merging
                        chunks.append(MessageChunk(type="text", text=pre_text))

                    # Add the table chunk
                    chunks.append(MessageChunk(type="table", text=potential_table_block))
                    last_line_processed = j # Update index past the table
                    i = j # Continue search after the table
                    continue # Skip the increment at the end loop

        # If not a table start or part of a valid table, move to the next line
        i += 1

    # Add any remaining text after the last table
    if last_line_processed < len(lines):
        remaining_text = '\n'.join(lines[last_line_processed:])
        # Append remaining text, even if it's just whitespace/newlines
        chunks.append(MessageChunk(type="text", text=remaining_text))

    # If the original text ended with a newline, splitlines might have dropped it.
    # Re-add it to the last text chunk if necessary.
    if text.endswith('\n') and chunks and chunks[-1].type == "text":
         if not chunks[-1].text.endswith('\n'): # Avoid double newline if join already added one
              chunks[-1].text += '\n'

    return chunks


_display_latex_pattern = re.compile(r'(\$\$(.+?)\$\$)|(\\\[(.+?)\\\])', re.DOTALL)

def process_text_with_display_latex(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes text for display latex, then passes segments to inline processor."""
    if not allow_latex:
        # If latex is disabled entirely, treat the whole block as text
        # Pass through inline processor which will just create text chunks
        return process_inline_by_line(text, allow_latex=False)

    chunks = []
    last_index = 0
    for match in _display_latex_pattern.finditer(text):
        start, end = match.span()
        if start > last_index:
            intermediate_text = text[last_index:start]
            # Process the intermediate text for inline latex and text structure
            chunks.extend(process_inline_by_line(intermediate_text, allow_latex))

        # Add the display latex chunk
        content = match.group(2) or match.group(4)
        if content is not None: # Ensure content exists
             # Append directly, don't use append_chunk for non-text types usually
             chunks.append(MessageChunk(type="latex", text=content.strip()))
        last_index = end

    # Process any remaining text after the last display latex block
    if last_index < len(text):
        remaining_text = text[last_index:]
        chunks.extend(process_inline_by_line(remaining_text, allow_latex))

    return chunks


def process_inline_by_line(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes text line by line, handling inline latex and preserving blank lines/structure."""
    chunks = []
    lines = text.splitlines() # Gives empty strings for blank lines

    # Pattern for $...$ (excluding $$) and \(...\)
    inline_latex_pattern = re.compile(
        r'(?<![\$\\])\$(?!\$)(.+?)(?<![\$\\])\$(?!\$)|' # $...$ (no $$)
        r'\\\((.+?)\\\)',                               # \(...\)
        re.DOTALL
    )

    is_first_line = True
    for line in lines:
        # --- Logic to reconstruct newlines using append_chunk ---
        # Instead of adding newline in append_chunk unconditionally,
        # we rely on adding Text chunks for each line (even empty ones)
        # and let append_chunk handle the merge with just ONE newline between.

        if not allow_latex:
            # Append the raw line as a text chunk. append_chunk adds the necessary newline.
            append_chunk(chunks, MessageChunk(type="text", text=line))
            continue

        subchunks_for_line = []
        last_index_in_line = 0
        for m in inline_latex_pattern.finditer(line):
            start, end = m.span()
            # Add text before the match
            if start > last_index_in_line:
                plain_text = line[last_index_in_line:start]
                subchunks_for_line.append(MessageChunk(type="text", text=plain_text))

            # Add the inline latex chunk
            content = m.group(1) or m.group(2) # Group 1 for $..$, Group 2 for \(..\)
            if content is not None: # Check if content was captured
                subchunks_for_line.append(MessageChunk(type="latex_inline", text=content.strip()))
            last_index_in_line = end

        # Add any remaining text after the last match on the line
        if last_index_in_line < len(line):
            remaining_text = line[last_index_in_line:]
            subchunks_for_line.append(MessageChunk(type="text", text=remaining_text))

        # Now add the processed chunks for this line to the main list
        if not subchunks_for_line:
            # If the line was empty or contained no parsable content (e.g., only "$$")
            # Add an empty text chunk to represent the line break structure.
            append_chunk(chunks, MessageChunk(type="text", text="")) # Represents blank line
        else:
            # Add the subchunks directly. append_chunk will handle merging
            # consecutive text parts correctly, including across lines.
            for sc in subchunks_for_line:
                append_chunk(chunks, sc)

    # Handle potential trailing newline from original text lost by splitlines()
    # This is complex to get perfectly right without index mapping.
    # Let's rely on the structure built by adding chunks for each line.
    # If text ended with \n, splitlines produces a final "" if content before it.
    # That "" gets added as Text(""), append_chunk adds "\n", seems okay.

    return chunks


def process_text_segment_no_think(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes a text segment without think tags, handling tables and latex."""
    if not text: # Handle empty string or None
        return []

    final_chunks = []
    # Extract tables first, preserving text segments between/around them
    table_and_text_parts = extract_tables(text)

    for chunk in table_and_text_parts:
        if chunk.type == "table":
            # Add table directly, don't merge with text via append_chunk
            final_chunks.append(chunk)
        elif chunk.type == "text":
            # Process this text part for display/inline latex
            # This function (process_text_with_display_latex -> process_inline_by_line)
            # now uses append_chunk internally to build its result list.
            processed_sub_chunks = process_text_with_display_latex(chunk.text, allow_latex)
            # Extend the final list, ensuring correct merging at the boundary
            # E.g. if final_chunks ends with Text and processed_sub_chunks starts with Text
            for sub_chunk in processed_sub_chunks:
                append_chunk(final_chunks, sub_chunk) # Merge needed here

    return final_chunks


def process_text_segment(text: str, allow_latex: bool) -> List[MessageChunk]:
    """Processes text segment potentially containing <think> tags."""
    chunks = []
    think_pattern = re.compile(r'<think>(.*?)</think>', re.DOTALL)
    last_index = 0
    for m in think_pattern.finditer(text):
        start, end = m.span()
        if start > last_index:
            # Process text before the think block
            pre_text = text[last_index:start]
            processed_pre_chunks = process_text_segment_no_think(pre_text, allow_latex)
            # Add these chunks, ensuring merge with previous if applicable
            for chunk in processed_pre_chunks:
                append_chunk(chunks, chunk)

        # Add the think chunk (don't merge with text)
        think_content = m.group(1).strip()
        chunks.append(MessageChunk(type="thinking", text=think_content))
        last_index = end

    # Process text after the last think block
    if last_index < len(text):
        remainder = text[last_index:]
        processed_post_chunks = process_text_segment_no_think(remainder, allow_latex)
        # Add remaining chunks, ensuring merge
        for chunk in processed_post_chunks:
            append_chunk(chunks, chunk)

    return chunks

# ============================================================
# Main Function with Fix
# ============================================================
def get_message_chunks(message: str, allow_latex: bool = True) -> List[MessageChunk]:
    """
    Main function to parse message into chunks, including code blocks,
    tables, latex, and thinking tags. Handles indented code fences.
    """
    chunks = []

    # CORRECTED REGEX for code blocks:
    # - ^\s*: Matches optional leading whitespace at the start of a line.
    # - ```: Matches the opening fence.
    # - (\w*): Captures the language identifier (optional).
    # - \s*: Matches optional whitespace after the language identifier.
    # - \n: Matches the newline after the opening fence line.
    # - (.*?): Captures the code content non-greedily.
    # - \n: Matches the newline before the closing fence line.
    # - ^\s*: Matches optional leading whitespace at the start of the closing line.
    # - ```: Matches the closing fence.
    # - \s*$: Matches optional trailing whitespace on the closing fence line.
    # - re.DOTALL: Makes '.' match newlines (for multi-line content).
    # - re.MULTILINE: Makes '^' and '$' match start/end of lines, not just string.
    codeblock_pattern = re.compile(r'^\s*```(\w*)\s*\n(.*?)\n^\s*```\s*$', re.DOTALL | re.MULTILINE)

    last_end = 0
    for match in codeblock_pattern.finditer(message):
        start, end = match.span()
        # Process text before the code block
        if start > last_end:
            pre_text = message[last_end:start]
            # process_text_segment handles nested parsing and uses append_chunk internally
            processed_pre_chunks = process_text_segment(pre_text, allow_latex)
            # Add these processed chunks, ensuring merge with the last chunk if needed
            for chunk in processed_pre_chunks:
                 append_chunk(chunks, chunk)

        # Extract code block details
        lang = match.group(1).strip() if match.group(1) else "" # Group 1 is the language
        code = match.group(2) # Group 2 is the code content

        # Add the code block chunk (don't merge with text via append_chunk)
        chunks.append(MessageChunk(type="codeblock", text=code, lang=lang))
        last_end = end

    # Process any remaining text after the last code block
    if last_end < len(message):
        post_text = message[last_end:]
        processed_post_chunks = process_text_segment(post_text, allow_latex)
        # Add remaining chunks, ensuring merge
        for chunk in processed_post_chunks:
             append_chunk(chunks, chunk)

    # Filter out completely empty text chunks that might remain if merge logic isn't perfect?
    # Let's reconsider the append_chunk logic slightly. It might add extra newlines.
    # Test Case: Text("A"), append(Text("")), append(Text("B")) -> Text("A\n\nB") ?
    # current append_chunk: chunks[-1].text += "\n" + new_chunk.text
    # A + \n + "" -> "A\n"
    # "A\n" + \n + "B" -> "A\n\nB" -- Looks correct for representing a blank line between A and B.

    # Final clean-up: Remove leading/trailing empty text chunks? Generally no.
    # They might represent intentional spacing.

    return chunks
