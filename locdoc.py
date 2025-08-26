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


def build_style() -> str:
    return f"""
body {{
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, 'Apple Color Emoji','Segoe UI Emoji';
  margin: 0; padding: 0; line-height: 1.45;
}}
.container {{ max-width: 1100px; margin: 0 auto; padding: 0 1rem; padding-bottom: 20px; }}
.meta small {{ color: #666; }}
.counts {{ margin-top: 0.25rem; color: #333; }}
.muted {{ color: #777; font-weight: normal; font-size: 0.9em; }}

/* Layout with sidebar */
.page {{ display: grid; grid-template-columns: 320px minmax(0,1fr); gap: 0; }}
#sidebar {{
  position: sticky; top: 0; align-self: start;
  height: 100vh; overflow: auto;
  border-right: 1px solid #eee; background: #fafbfc;
}}
#sidebar .sidebar-inner {{ padding: 0.75rem; }}
#sidebar h2 {{ margin: 0 0 0.5rem 0; font-size: 1rem; }}

.toc {{ list-style: none; padding-left: 0; margin: 0; overflow-x: auto; }}
.toc li {{ padding: 0.15rem 0; white-space: nowrap; }}
.toc a {{ text-decoration: none; color: black; display: inline-block; text-decoration: none; }}
.toc a.active {{ color: blue; }}
.toc a:hover {{ text-decoration: underline; }}

main.container {{ padding-top: 1rem; }}

.toc > ul {{ padding: 0; }}
ul {{ list-style-type: none; padding-left: 15px; }}
pre {{ background: #f6f8fa; padding: 0.75rem; overflow: auto; border-radius: 6px; }}
code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono','Courier New', monospace; }}
"""


def build_script() -> str:
    return """
    function tocScroll() {
        const main = document.getElementsByTagName("main")[0];
        const anchors = main.querySelectorAll("h1, h2, h3");
        const toc = document.getElementsByClassName("toc");

        if (toc.length == 0) return;

        const nav = toc[0];
        const links = nav.querySelectorAll("a");

        // Make first element active
        if (links.length > 0) {
          links[0].classList.add("active");
        }

        window.addEventListener('scroll', (event) => {
          if (
            typeof anchors != 'undefined' &&
            anchors != null &&
            typeof links != 'undefined' &&
            links != null
          ) {
            let scrollTop = window.scrollY;

            // highlight the last scrolled-to: set everything inactive first
            links.forEach((link, index) => {
              link.classList.remove('active');
            });

            // then iterate backwards, on the first match highlight it and break
            for (var i = anchors.length - 1; i >= 0; i--) {
              const anchor = anchors[i];
              if (anchor != null && scrollTop >= anchor.offsetTop - anchors[0].offsetTop) {
                links[i].classList.add("active");
                break;
              }
            }
          }
        });
    }"""


def build_doc(toc_html: str, body_html: str) -> str:
    html_style = build_style()
    html_script = build_script()

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
