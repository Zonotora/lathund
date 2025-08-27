#!/usr/bin/env python3
"""Live server for lathund.py that watches for file changes and updates the browser."""

import asyncio
import json
import os
import pathlib
import time
import re
from typing import Set
import websockets
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from threading import Thread
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from html import unescape

from lathund import build_doc
import markdown


class FileChangeHandler(FileSystemEventHandler):
    """Handler for file system events."""
    
    def __init__(self, callback):
        self.callback = callback
        self.last_modified = {}
        
    def on_modified(self, event):
        if event.is_directory:
            return
            
        # Debounce rapid file changes
        now = time.time()
        if event.src_path in self.last_modified:
            if now - self.last_modified[event.src_path] < 0.5:
                return
        
        self.last_modified[event.src_path] = now
        
        if event.src_path.endswith('.md'):
            print(f"File changed: {event.src_path}")
            self.callback(event.src_path)


class LiveServer:
    """Live server that serves HTML and provides WebSocket updates."""
    
    def __init__(self, markdown_file: str, output_file: str = "index.html", port: int = 8000):
        self.markdown_file = pathlib.Path(markdown_file).resolve()
        self.output_file = pathlib.Path(output_file).resolve()
        self.port = port
        self.websocket_port = port + 1
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        
        # Ensure we have the CSS and JS content
        self.css_content = self._load_css()
        self.js_content = self._load_js()
        
    def _load_css(self) -> str:
        """Load CSS content from src/style.css or use default."""
        css_path = pathlib.Path(__file__).parent / "src" / "style.css"
        if css_path.exists():
            return css_path.read_text()
        return ""
    
    def _load_js(self) -> str:
        """Load JavaScript content from src/script.js or use default."""
        js_path = pathlib.Path(__file__).parent / "src" / "script.js"
        if js_path.exists():
            return js_path.read_text()
        return ""
        
    def generate_html(self, markdown_path: str = None) -> str:
        """Generate HTML from markdown file."""
        if markdown_path is None:
            markdown_path = self.markdown_file
        else:
            markdown_path = pathlib.Path(markdown_path)
            
        with open(markdown_path) as f:
            md_text = f.read()
            md_text = "[TOC]\n" + md_text
            md_html = markdown.markdown(md_text, extensions=["extra", "toc"])
            
            end_tag = "</div>"
            toc_end = md_html.find(end_tag) + len(end_tag)
            
            toc_html = md_html[0:toc_end]
            body_html = md_html[toc_end:]
            
            # Enhanced HTML with WebSocket support and live reload
            return self._build_live_doc(toc_html, body_html)
    
    def _build_live_doc(self, toc_html: str, body_html: str) -> str:
        """Build HTML document with live reload and editing support."""
        live_reload_script = f"""
// Live reload and editing functionality
const ws = new WebSocket('ws://localhost:{self.websocket_port}');
let isUpdatingFromServer = false;
let saveTimeout = null;

ws.onmessage = function(event) {{
    const data = JSON.parse(event.data);
    if (data.type === 'reload') {{
        location.reload();
    }} else if (data.type === 'content_updated') {{
        // Update was successful, no need to reload
        console.log('Content saved to markdown file');
    }}
}};

ws.onopen = function() {{
    console.log('Live reload connected');
    enableEditing();
}};

ws.onclose = function() {{
    console.log('Live reload disconnected');
    // Try to reconnect after a delay
    setTimeout(() => {{
        location.reload();
    }}, 1000);
}};

function enableEditing() {{
    const mainContent = document.querySelector('main.container');
    if (mainContent) {{
        // Make content editable
        mainContent.setAttribute('contenteditable', 'true');
        mainContent.style.outline = 'none';
        mainContent.style.border = '1px dashed #ccc';
        mainContent.style.padding = '10px';
        
        // Add editing indicator
        const indicator = document.createElement('div');
        indicator.id = 'edit-indicator';
        indicator.innerHTML = '✏️ Content is editable - changes auto-save';
        indicator.style.cssText = `
            position: fixed;
            top: 10px;
            right: 10px;
            background: #4CAF50;
            color: white;
            padding: 8px 12px;
            border-radius: 4px;
            font-size: 12px;
            z-index: 1000;
            box-shadow: 0 2px 4px rgba(0,0,0,0.2);
        `;
        document.body.appendChild(indicator);
        
        // Create editing toolbar
        createEditingToolbar();
        
        // Listen for content changes
        mainContent.addEventListener('input', handleContentChange);
        mainContent.addEventListener('paste', function(e) {{
            setTimeout(handleContentChange, 100);
        }});
        
        // Listen for cursor position changes to highlight blocks
        mainContent.addEventListener('click', handleCursorPosition);
        mainContent.addEventListener('keyup', handleCursorPosition);
        mainContent.addEventListener('focus', handleCursorPosition);
        document.addEventListener('selectionchange', handleCursorPosition);
    }}
}}

function createEditingToolbar() {{
    const toolbar = document.createElement('div');
    toolbar.id = 'editing-toolbar';
    toolbar.style.cssText = `
        position: absolute;
        background: #2c3e50;
        border-radius: 6px;
        padding: 8px;
        display: none;
        z-index: 1001;
        box-shadow: 0 4px 12px rgba(0,0,0,0.3);
        font-family: system-ui, -apple-system, sans-serif;
    `;
    
    const buttons = [
        {{ icon: 'B', title: 'Bold', command: 'bold', style: 'font-weight: bold;' }},
        {{ icon: 'I', title: 'Italic', command: 'italic', style: 'font-style: italic;' }},
        {{ icon: 'U', title: 'Underline', command: 'underline', style: 'text-decoration: underline;' }},
        {{ icon: '&lt;/&gt;', title: 'Code', command: 'code', style: 'font-family: monospace; background: #f1f1f1; padding: 2px 4px; border-radius: 3px;' }},
        {{ icon: '🔗', title: 'Link', command: 'link', style: 'color: #3498db;' }},
        {{ icon: '📋', title: 'Table', command: 'table', style: 'color: #e74c3c;' }},
        {{ icon: '#', title: 'Header', command: 'header', style: 'font-weight: bold; color: #9b59b6;' }},
        {{ icon: '•', title: 'List', command: 'list', style: 'color: #f39c12;' }}
    ];
    
    buttons.forEach(btn => {{
        const button = document.createElement('button');
        button.innerHTML = btn.icon;
        button.title = btn.title;
        button.style.cssText = `
            background: #34495e;
            color: white;
            border: none;
            padding: 6px 10px;
            margin: 0 2px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 12px;
            transition: background 0.2s;
            ${{btn.style}}
        `;
        
        button.addEventListener('mouseenter', () => {{
            button.style.background = '#4a6741';
        }});
        button.addEventListener('mouseleave', () => {{
            button.style.background = '#34495e';
        }});
        
        button.addEventListener('click', (e) => {{
            e.preventDefault();
            executeCommand(btn.command);
        }});
        
        toolbar.appendChild(button);
    }});
    
    document.body.appendChild(toolbar);
}}

let currentHighlightedBlock = null;

function handleCursorPosition() {{
    const selection = window.getSelection();
    const toolbar = document.getElementById('editing-toolbar');
    const mainContent = document.querySelector('main.container');
    
    // Clear previous highlighting
    if (currentHighlightedBlock) {{
        currentHighlightedBlock.style.outline = '';
        currentHighlightedBlock = null;
    }}
    
    if (!selection.rangeCount) {{
        toolbar.style.display = 'none';
        return;
    }}
    
    const range = selection.getRangeAt(0);
    let element = range.commonAncestorContainer;
    
    // Find the closest block element
    while (element && element.nodeType !== Node.ELEMENT_NODE) {{
        element = element.parentNode;
    }}
    
    // Find the actual block element (p, h1-h6, li, td, th, div, etc.)
    while (element && element !== mainContent) {{
        const tagName = element.tagName;
        if (tagName && tagName.match(/^(P|H[1-6]|LI|TD|TH|DIV|PRE|BLOCKQUOTE|TABLE)$/)) {{
            break;
        }}
        element = element.parentNode;
    }}
    
    // Check if we found a valid block and it's within the editable content
    if (!element || element === mainContent || !mainContent.contains(element)) {{
        toolbar.style.display = 'none';
        return;
    }}
    
    // Highlight the block
    currentHighlightedBlock = element;
    element.style.outline = '2px solid #3498db';
    element.style.outlineOffset = '2px';
    
    // Position toolbar above the block
    const rect = element.getBoundingClientRect();
    toolbar.style.display = 'block';
    toolbar.style.left = `${{rect.left + window.scrollX}}px`;
    toolbar.style.top = `${{rect.top + window.scrollY - toolbar.offsetHeight - 10}}px`;
    
    // Ensure toolbar stays within viewport
    const toolbarRect = toolbar.getBoundingClientRect();
    if (toolbarRect.right > window.innerWidth) {{
        toolbar.style.left = `${{window.innerWidth - toolbarRect.width - 10}}px`;
    }}
    if (toolbarRect.top < 0) {{
        toolbar.style.top = `${{rect.bottom + window.scrollY + 10}}px`;
    }}
}}

function executeCommand(command) {{
    const selection = window.getSelection();
    if (!selection.rangeCount) return;
    
    switch (command) {{
        case 'bold':
            document.execCommand('bold', false, null);
            break;
        case 'italic':
            document.execCommand('italic', false, null);
            break;
        case 'underline':
            document.execCommand('underline', false, null);
            break;
        case 'code':
            wrapSelectionWithTag('code');
            break;
        case 'link':
            const url = prompt('Enter URL:');
            if (url) {{
                document.execCommand('createLink', false, url);
            }}
            break;
        case 'table':
            insertTable();
            break;
        case 'header':
            toggleHeader();
            break;
        case 'list':
            document.execCommand('insertUnorderedList', false, null);
            break;
    }}
    
    handleContentChange();
    handleCursorPosition();
}}

function wrapSelectionWithTag(tag) {{
    const selection = window.getSelection();
    if (!selection.rangeCount) return;
    
    const range = selection.getRangeAt(0);
    const selectedText = range.toString();
    
    if (selectedText) {{
        const wrapper = document.createElement(tag);
        try {{
            range.surroundContents(wrapper);
        }} catch (e) {{
            // If surroundContents fails, create new element with content
            wrapper.textContent = selectedText;
            range.deleteContents();
            range.insertNode(wrapper);
        }}
        
        // Clear selection
        selection.removeAllRanges();
    }}
}}

function toggleHeader() {{
    const selection = window.getSelection();
    if (!selection.rangeCount) return;
    
    const range = selection.getRangeAt(0);
    let element = range.commonAncestorContainer;
    
    // Find the containing block element
    while (element && element.nodeType !== Node.ELEMENT_NODE) {{
        element = element.parentNode;
    }}
    
    // Check if already a header
    if (element.tagName && element.tagName.match(/^H[1-6]$/)) {{
        // Convert to paragraph
        const p = document.createElement('p');
        p.innerHTML = element.innerHTML;
        element.parentNode.replaceChild(p, element);
    }} else {{
        // Find paragraph or div to convert to header
        while (element && !element.tagName.match(/^(P|DIV|H[1-6])$/)) {{
            element = element.parentNode;
        }}
        
        if (element) {{
            const h2 = document.createElement('h2');
            h2.innerHTML = element.innerHTML;
            element.parentNode.replaceChild(h2, element);
        }}
    }}
}}

function insertTable() {{
    const rows = parseInt(prompt('Number of rows:', '3')) || 3;
    const cols = parseInt(prompt('Number of columns:', '3')) || 3;
    
    const selection = window.getSelection();
    const range = selection.getRangeAt(0);
    
    const table = document.createElement('table');
    table.style.cssText = `
        border-collapse: collapse;
        width: 100%;
        margin: 10px 0;
        border: 1px solid #ddd;
    `;
    
    for (let i = 0; i < rows; i++) {{
        const row = document.createElement('tr');
        for (let j = 0; j < cols; j++) {{
            const cell = document.createElement(i === 0 ? 'th' : 'td');
            cell.style.cssText = `
                border: 1px solid #ddd;
                padding: 8px;
                text-align: left;
                min-width: 80px;
            `;
            cell.contentEditable = true;
            cell.textContent = i === 0 ? `Header ${{j + 1}}` : `Cell ${{i}},${{j + 1}}`;
            row.appendChild(cell);
        }}
        table.appendChild(row);
    }}
    
    // Insert table at cursor position
    range.deleteContents();
    range.insertNode(table);
    
    // Add some space after table
    const br = document.createElement('br');
    table.parentNode.insertBefore(br, table.nextSibling);
    
    selection.removeAllRanges();
}}

function handleContentChange() {{
    const indicator = document.getElementById('edit-indicator');
    if (indicator) {{
        indicator.style.background = '#FF9800';
        indicator.innerHTML = '💾 Saving...';
    }}
    
    // Debounce saves
    if (saveTimeout) {{
        clearTimeout(saveTimeout);
    }}
    
    saveTimeout = setTimeout(() => {{
        saveContent();
    }}, 1000);
}}

function saveContent() {{
    const mainContent = document.querySelector('main.container');
    if (mainContent && ws.readyState === WebSocket.OPEN) {{
        const htmlContent = mainContent.innerHTML;
        
        ws.send(JSON.stringify({{
            type: 'save_content',
            content: htmlContent
        }}));
        
        const indicator = document.getElementById('edit-indicator');
        if (indicator) {{
            indicator.style.background = '#4CAF50';
            indicator.innerHTML = '✅ Saved';
            
            setTimeout(() => {{
                indicator.style.background = '#4CAF50';
                indicator.innerHTML = '✏️ Content is editable - changes auto-save';
            }}, 2000);
        }}
    }}
}}
"""
        
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Local docs - Live</title>
<style>{self.css_content}</style>
</head>
<body onload="tocScroll()">
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
{self.js_content}
{live_reload_script}
</script>
</html>"""
        
    def on_file_change(self, filepath: str):
        """Handle file change event."""
        try:
            html = self.generate_html(filepath)
            self.output_file.write_text(html)
            print(f"Updated: {self.output_file}")
            
            # Notify all connected clients
            asyncio.create_task(self.notify_clients())
        except Exception as e:
            print(f"Error updating HTML: {e}")
            
    async def notify_clients(self):
        """Notify all WebSocket clients to reload."""
        if self.clients:
            message = json.dumps({"type": "reload"})
            # Create list copy to avoid modification during iteration
            clients_copy = list(self.clients)
            for client in clients_copy:
                try:
                    await client.send(message)
                except websockets.exceptions.ConnectionClosed:
                    self.clients.discard(client)
    
    async def websocket_handler(self, websocket):
        """Handle WebSocket connections and messages."""
        self.clients.add(websocket)
        print(f"WebSocket client connected. Total: {len(self.clients)}")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get('type') == 'save_content':
                        await self.handle_content_save(websocket, data.get('content', ''))
                except json.JSONDecodeError:
                    print(f"Invalid JSON received: {message}")
                except Exception as e:
                    print(f"Error handling message: {e}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.discard(websocket)
            print(f"WebSocket client disconnected. Total: {len(self.clients)}")
    
    def html_to_markdown(self, html_content: str) -> str:
        """Convert HTML content back to markdown (simplified conversion)."""
        # This is a basic HTML to markdown conversion
        # For production use, consider using libraries like html2text or markdownify
        
        # Remove extra whitespace and normalize
        content = html_content.strip()
        
        # Convert tables first (more complex)
        content = self._convert_tables_to_markdown(content)
        
        # Convert headers
        content = re.sub(r'<h1[^>]*>(.*?)</h1>', r'# \1', content, flags=re.DOTALL)
        content = re.sub(r'<h2[^>]*>(.*?)</h2>', r'## \1', content, flags=re.DOTALL)
        content = re.sub(r'<h3[^>]*>(.*?)</h3>', r'### \1', content, flags=re.DOTALL)
        content = re.sub(r'<h4[^>]*>(.*?)</h4>', r'#### \1', content, flags=re.DOTALL)
        content = re.sub(r'<h5[^>]*>(.*?)</h5>', r'##### \1', content, flags=re.DOTALL)
        content = re.sub(r'<h6[^>]*>(.*?)</h6>', r'###### \1', content, flags=re.DOTALL)
        
        # Convert paragraphs
        content = re.sub(r'<p[^>]*>(.*?)</p>', r'\1\n\n', content, flags=re.DOTALL)
        
        # Convert code blocks
        content = re.sub(r'<pre[^>]*><code[^>]*>(.*?)</code></pre>', r'```\n\1\n```\n\n', content, flags=re.DOTALL)
        content = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', content, flags=re.DOTALL)
        
        # Convert bold and italic
        content = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', content, flags=re.DOTALL)
        content = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', content, flags=re.DOTALL)
        content = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', content, flags=re.DOTALL)
        content = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', content, flags=re.DOTALL)
        content = re.sub(r'<u[^>]*>(.*?)</u>', r'<u>\1</u>', content, flags=re.DOTALL)  # Keep underline as HTML
        
        # Convert lists
        content = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', content, flags=re.DOTALL)
        content = re.sub(r'<ul[^>]*>(.*?)</ul>', r'\1\n', content, flags=re.DOTALL)
        content = re.sub(r'<ol[^>]*>(.*?)</ol>', r'\1\n', content, flags=re.DOTALL)
        
        # Convert links
        content = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', content, flags=re.DOTALL)
        
        # Remove remaining HTML tags (except those we want to keep)
        content = re.sub(r'<(?!/?u\b)[^>]+>', '', content)
        
        # Unescape HTML entities
        content = unescape(content)
        
        # Clean up whitespace
        content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
        content = content.strip()
        
        return content
    
    def _convert_tables_to_markdown(self, html_content: str) -> str:
        """Convert HTML tables to markdown format."""
        def table_replacer(match):
            table_html = match.group(0)
            
            # Extract rows
            rows = re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL)
            if not rows:
                return table_html
            
            markdown_rows = []
            
            for i, row in enumerate(rows):
                # Extract cells (th or td)
                cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row, re.DOTALL)
                if cells:
                    # Clean cell content
                    clean_cells = []
                    for cell in cells:
                        # Remove HTML tags from cell content
                        clean_cell = re.sub(r'<[^>]+>', '', cell).strip()
                        clean_cell = unescape(clean_cell)
                        clean_cells.append(clean_cell)
                    
                    # Create markdown row
                    markdown_row = '| ' + ' | '.join(clean_cells) + ' |'
                    markdown_rows.append(markdown_row)
                    
                    # Add separator after header row
                    if i == 0:
                        separator = '| ' + ' | '.join(['---'] * len(clean_cells)) + ' |'
                        markdown_rows.append(separator)
            
            if markdown_rows:
                return '\n\n' + '\n'.join(markdown_rows) + '\n\n'
            else:
                return table_html
        
        # Replace tables with markdown
        return re.sub(r'<table[^>]*>.*?</table>', table_replacer, html_content, flags=re.DOTALL)
    
    async def handle_content_save(self, websocket, html_content: str):
        """Handle saving edited content back to markdown file."""
        try:
            # Convert HTML back to markdown
            markdown_content = self.html_to_markdown(html_content)
            
            # Temporarily disable file watcher to prevent recursion
            # (This is a simple approach - in production you might want more sophisticated handling)
            
            # Write to markdown file
            with open(self.markdown_file, 'w') as f:
                f.write(markdown_content)
            
            print(f"Updated markdown file: {self.markdown_file}")
            
            # Send confirmation back to client
            await websocket.send(json.dumps({
                "type": "content_updated",
                "status": "success"
            }))
            
        except Exception as e:
            print(f"Error saving content: {e}")
            await websocket.send(json.dumps({
                "type": "content_updated", 
                "status": "error",
                "message": str(e)
            }))
    
    def start_http_server(self):
        """Start HTTP server in a separate thread."""
        os.chdir(self.output_file.parent)
        
        class CustomHandler(SimpleHTTPRequestHandler):
            def end_headers(self):
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Expires', '0')
                super().end_headers()
        
        httpd = HTTPServer(('localhost', self.port), CustomHandler)
        print(f"HTTP server running on http://localhost:{self.port}")
        httpd.serve_forever()
    
    async def start_websocket_server(self):
        """Start WebSocket server."""
        server = await websockets.serve(
            self.websocket_handler, 
            'localhost', 
            self.websocket_port
        )
        print(f"WebSocket server running on ws://localhost:{self.websocket_port}")
        await server.wait_closed()
        
    def start_file_watcher(self):
        """Start watching for file changes."""
        event_handler = FileChangeHandler(self.on_file_change)
        observer = Observer()
        
        # Watch the directory containing the markdown file
        watch_dir = self.markdown_file.parent
        observer.schedule(event_handler, str(watch_dir), recursive=True)
        
        observer.start()
        print(f"Watching for changes in: {watch_dir}")
        return observer
    
    async def run(self):
        """Run the live server."""
        # Generate initial HTML
        html = self.generate_html()
        self.output_file.write_text(html)
        print(f"Generated initial HTML: {self.output_file}")
        
        # Start file watcher
        observer = self.start_file_watcher()
        
        # Start HTTP server in thread
        http_thread = Thread(target=self.start_http_server, daemon=True)
        http_thread.start()
        
        # Open browser
        webbrowser.open(f"http://localhost:{self.port}/{self.output_file.name}")
        
        # Start WebSocket server
        try:
            await self.start_websocket_server()
        except KeyboardInterrupt:
            print("\nShutting down...")
        finally:
            observer.stop()
            observer.join()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Live server for lathund.py")
    parser.add_argument("file", help="Markdown file to watch")
    parser.add_argument("-o", "--out", default="index.html", help="Output HTML file")
    parser.add_argument("-p", "--port", type=int, default=8000, help="HTTP server port")
    
    args = parser.parse_args()
    
    if not pathlib.Path(args.file).exists():
        print(f"Error: File {args.file} does not exist")
        return 1
        
    server = LiveServer(args.file, args.out, args.port)
    
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\nGoodbye!")
        return 0


if __name__ == "__main__":
    exit(main())