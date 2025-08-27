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
        
        // Listen for content changes
        mainContent.addEventListener('input', handleContentChange);
        mainContent.addEventListener('paste', function(e) {{
            setTimeout(handleContentChange, 100);
        }});
    }}
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
        
        # Convert lists
        content = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1', content, flags=re.DOTALL)
        content = re.sub(r'<ul[^>]*>(.*?)</ul>', r'\1\n', content, flags=re.DOTALL)
        content = re.sub(r'<ol[^>]*>(.*?)</ol>', r'\1\n', content, flags=re.DOTALL)
        
        # Convert links
        content = re.sub(r'<a[^>]*href=["\']([^"\']*)["\'][^>]*>(.*?)</a>', r'[\2](\1)', content, flags=re.DOTALL)
        
        # Remove remaining HTML tags
        content = re.sub(r'<[^>]+>', '', content)
        
        # Unescape HTML entities
        content = unescape(content)
        
        # Clean up whitespace
        content = re.sub(r'\n\s*\n\s*\n', '\n\n', content)
        content = content.strip()
        
        return content
    
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