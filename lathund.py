from __future__ import annotations
import argparse
import html
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from typing import List

# External deps
import markdown.util
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_for_filename, TextLexer
import markdown


def build_doc(toc_html: str, body_html: str) -> str:
    html_style = ""
    html_script = ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Local docs</title>
<style>{html_style}</style>
</head>
<body  onload="tocScroll()">
<a id="top"></a>

<div class="page">
  <nav id="sidebar"><div class="sidebar-inner">
      <h2>Table of contents</h2>
      {toc_html}
  </div></nav>

  <main class="container">
    {body_html}
  </main>
</div>

</body>

<script>
{html_script}
</script>
</html>"""


def main():
    ap = argparse.ArgumentParser(description="Flatten a GitHub repo to a single HTML page")
    ap.add_argument("file", type=str)
    ap.add_argument("-o", "--out", help="Output HTML file path (default: temporary file derived from repo name)")
    args = ap.parse_args()

    if args.out is None:
        args.out = "out.html"

    with open(args.file) as f:
        md_text = f.read()
        md_text = "[TOC]\n" + md_text
        md_html = markdown.markdown(md_text, extensions=["extra", "toc"])  # type: ignore

        end_tag = "</div>"
        toc_end = md_html.find(end_tag) + len(end_tag)

        toc_html = md_html[0:toc_end]
        body_html = md_html[toc_end:]

        html = build_doc(toc_html, body_html)
        out_path = pathlib.Path(args.out)
        out_path.write_text(html)
        # webbrowser.open(f"file://{out_path.resolve()}")


if __name__ == "__main__":
    main()
