"""Unified diff parser for applying patches to files atomically."""

import re
from dataclasses import dataclass


@dataclass
class Hunk:
    """A single hunk from a unified diff."""
    old_start: int  # 1-indexed line number where this hunk starts in original
    old_count: int  # number of lines removed
    new_start: int  # 1-indexed line number where this hunk starts in result
    new_count: int  # number of lines added
    lines: list[str]  # raw diff lines (without +/- prefix)


@dataclass
class DiffResult:
    """Parsed diff result."""
    hunks: list[Hunk]
    source_path: str = ""
    target_path: str = ""


def parse_unified_diff(diff_text: str) -> DiffResult:
    """Parse a unified diff string into structured hunks.

    Handles standard unified diff format:
      --- a/path/to/file
      +++ b/path/to/file
      @@ -old_start,old_count +new_start,new_count @@
      context line
      -removed line
      +added line

    Line numbers are converted to 1-indexed.
    """
    lines = diff_text.splitlines()

    source_path = ""
    target_path = ""
    hunks: list[Hunk] = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Parse --- / +++ headers
        if line.startswith("--- "):
            parts = line[4:].split("\t", 1)
            source_path = parts[0].strip()
            if source_path.startswith("a/"):
                source_path = source_path[2:]
            i += 1
            continue

        if line.startswith("+++ "):
            parts = line[4:].split("\t", 1)
            target_path = parts[0].strip()
            if target_path.startswith("b/"):
                target_path = target_path[2:]
            i += 1
            continue

        # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
        hunk_match = re.match(
            r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@",
            line
        )
        if hunk_match:
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) else 1

            hunk_lines: list[str] = []
            i += 1

            # Collect hunk body lines
            while i < len(lines):
                hline = lines[i]

                # Check if we've hit another hunk header or file header
                if hline.startswith("@@") or hline.startswith("--- ") or hline.startswith("+++ "):
                    break

                if hline == "" and i < len(lines) - 1:
                    # Empty line could be end of hunk or trailing space stripped
                    if not _looks_like_hunk_content(lines[i + 1]):
                        break

                if hline.startswith("\\ No newline at end of file"):
                    hunk_lines.append(hline)
                    i += 1
                    continue

                hunk_lines.append(hline)
                i += 1

            hunks.append(Hunk(
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                lines=hunk_lines,
            ))
            continue

        i += 1

    return DiffResult(hunks=hunks, source_path=source_path, target_path=target_path)


def _looks_like_hunk_content(line: str) -> bool:
    """Check if a line looks like hunk body content."""
    if not line:
        return False
    return line.startswith(" ") or line.startswith("+") or line.startswith("-")


def apply_diff(content: str, diff_result: DiffResult) -> str:
    """Apply a parsed unified diff to file content.

    Returns the modified content, or raises ValueError on mismatch.
    Lines are 1-indexed as specified in the diff.
    """
    lines = content.splitlines(keepends=True)

    # Process hunks in reverse order so earlier line numbers stay valid
    for hunk in reversed(diff_result.hunks):
        old_start_idx = hunk.old_start - 1
        old_end_idx = old_start_idx + hunk.old_count

        if old_start_idx < 0 or old_end_idx > len(lines):
            raise ValueError(
                f"Hunk range [{hunk.old_start}, {hunk.old_start + hunk.old_count}] "
                f"out of bounds for file with {len(lines)} lines"
            )

        # Verify the old content matches
        old_section = "".join(lines[old_start_idx:old_end_idx])
        expected_old = _build_old_section(hunk.lines)

        if old_section != expected_old:
            raise ValueError(
                f"Hunk at line {hunk.old_start} does not match. "
                f"Expected ({hunk.old_count} lines):\n{expected_old!r}\n"
                f"Got ({old_end_idx - old_start_idx} lines):\n{old_section!r}"
            )

        # Build the new lines
        new_lines = _build_new_section(hunk.lines)

        # Replace the section
        lines[old_start_idx:old_end_idx] = new_lines

    return "".join(lines)


def _build_old_section(hunk_lines: list[str]) -> str:
    """Build the expected old content from hunk lines.

    Strips -/+ prefixes and reconstructs file lines with trailing \n.
    The last line does NOT get a trailing \n if followed by
    '\\ No newline at end of file'.
    """
    result: list[str] = []
    has_no_nl = False
    for line in hunk_lines:
        if line.startswith("\\ No newline at end of file"):
            has_no_nl = True
            continue
        if line.startswith("-"):
            result.append(line[1:])
        elif line.startswith(" "):
            result.append(line[1:])
        # Skip + lines (only in new version)
    if has_no_nl and result:
        # Last line has no trailing newline
        return "".join(ln + "\n" for ln in result[:-1]) + result[-1]
    return "".join(ln + "\n" for ln in result)


def _build_new_section(hunk_lines: list[str]) -> list[str]:
    """Build the new lines from hunk lines.

    Keeps + and space-prefixed lines, strips prefix, adds trailing \n.
    The last line does NOT get a trailing \n if followed by
    '\\ No newline at end of file'.
    """
    result: list[str] = []
    has_no_nl = False
    for line in hunk_lines:
        if line.startswith("\\ No newline at end of file"):
            has_no_nl = True
            continue
        if line.startswith("+"):
            result.append(line[1:] + "\n")
        elif line.startswith(" "):
            result.append(line[1:] + "\n")
        # Skip - lines (only in old version)
    if has_no_nl and result:
        result[-1] = result[-1][:-1]  # Remove trailing \n from last line
    return result
