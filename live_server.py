#!/usr/bin/env python3
"""Live server for lathund.py that watches for file changes and updates the browser."""

import asyncio
import json
import os
import pathlib
import time
import re
import subprocess
import tempfile
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
    }} else if (data.type === 'code_execution_result') {{
        handleCodeExecutionResult(data);
    }}
}};

function handleCodeExecutionResult(data) {{
    if (window.pendingExecution) {{
        const {{ outputArea, runButton, originalText }} = window.pendingExecution;
        
        // Display the result
        if (data.success) {{
            outputArea.textContent = data.output || '(no output)';
            if (data.stderr) {{
                outputArea.textContent += '\\n\\nStderr:\\n' + data.stderr;
            }}
        }} else {{
            outputArea.textContent = 'Error: ' + (data.error || 'Unknown error');
            outputArea.style.color = '#ff6b6b';
        }}
        
        // Reset button
        runButton.innerHTML = originalText;
        runButton.disabled = false;
        runButton.style.background = '#28a745';
        
        // Clear pending execution
        window.pendingExecution = null;
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

let isEditMode = true;
let isDarkMode = false;

function enableEditing() {{
    const mainContent = document.querySelector('main.container');
    if (mainContent) {{
        // Create top bar
        createTopBar();
        
        // Create editing toolbar
        createEditingToolbar();
        
        // Set initial mode
        setEditMode(true);
        
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

function createTopBar() {{
    const mainContent = document.querySelector('main.container');
    
    // Create top bar container
    const topBar = document.createElement('div');
    topBar.id = 'top-bar';
    topBar.style.cssText = `
        position: sticky;
        top: 0;
        left: 0;
        right: 0;
        height: 50px;
        background: #f8f9fa;
        border-bottom: 1px solid #dee2e6;
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 0 16px;
        z-index: 1000;
        font-family: system-ui, -apple-system, sans-serif;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        margin: -10px -10px 20px -10px;
    `;
    
    // Left side - Mode toggles
    const leftControls = document.createElement('div');
    leftControls.style.cssText = `
        display: flex;
        align-items: center;
        gap: 8px;
    `;
    
    // Edit/Read mode toggle
    const editToggle = document.createElement('button');
    editToggle.id = 'edit-toggle';
    editToggle.innerHTML = '✏️ Edit';
    editToggle.style.cssText = `
        background: #007bff;
        color: white;
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
        font-weight: 500;
        transition: background 0.2s;
    `;
    
    editToggle.addEventListener('click', () => {{
        toggleEditMode();
    }});
    
    // Light/Dark mode toggle
    const themeToggle = document.createElement('button');
    themeToggle.id = 'theme-toggle';
    themeToggle.innerHTML = '🌙 Dark';
    themeToggle.style.cssText = `
        background: #6c757d;
        color: white;
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 13px;
        font-weight: 500;
        transition: background 0.2s;
    `;
    
    themeToggle.addEventListener('click', () => {{
        toggleTheme();
    }});
    
    leftControls.appendChild(editToggle);
    leftControls.appendChild(themeToggle);
    
    // Center - Filename
    const filename = document.createElement('div');
    filename.id = 'filename-display';
    filename.style.cssText = `
        font-weight: 600;
        color: #495057;
        font-size: 14px;
        flex: 1;
        text-align: center;
    `;
    filename.textContent = window.location.pathname.split('/').pop() || 'Document';
    
    // Right side - Status indicator (moved from corner)
    const statusIndicator = document.createElement('div');
    statusIndicator.id = 'status-indicator';
    statusIndicator.innerHTML = '✏️ Ready';
    statusIndicator.style.cssText = `
        background: #28a745;
        color: white;
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 500;
    `;
    
    // Assemble top bar
    topBar.appendChild(leftControls);
    topBar.appendChild(filename);
    topBar.appendChild(statusIndicator);
    
    // Insert at the top of main content
    mainContent.insertBefore(topBar, mainContent.firstChild);
    
    // Update main content padding to account for top bar
    mainContent.style.paddingTop = '10px';
}}

function toggleEditMode() {{
    setEditMode(!isEditMode);
}}

function setEditMode(enabled) {{
    isEditMode = enabled;
    const mainContent = document.querySelector('main.container');
    const editToggle = document.getElementById('edit-toggle');
    const toolbar = document.getElementById('editing-toolbar');
    const statusIndicator = document.getElementById('status-indicator');
    
    if (enabled) {{
        // Enable editing
        mainContent.setAttribute('contenteditable', 'true');
        mainContent.style.outline = 'none';
        mainContent.style.border = '1px dashed #ccc';
        
        // Show toolbar
        if (toolbar) {{
            toolbar.style.display = 'flex';
        }}
        
        editToggle.innerHTML = '✏️ Edit';
        editToggle.style.background = '#007bff';
        statusIndicator.innerHTML = '✏️ Edit Mode';
        statusIndicator.style.background = '#007bff';
    }} else {{
        // Disable editing
        mainContent.setAttribute('contenteditable', 'false');
        mainContent.style.outline = 'none';
        mainContent.style.border = '1px solid transparent';
        
        // Hide toolbar
        if (toolbar) {{
            toolbar.style.display = 'none';
        }}
        
        // Clear highlighting
        if (currentHighlightedBlock) {{
            currentHighlightedBlock.style.outline = '';
            currentHighlightedBlock = null;
        }}
        
        editToggle.innerHTML = '👁️ Read';
        editToggle.style.background = '#28a745';
        statusIndicator.innerHTML = '👁️ Read Mode';
        statusIndicator.style.background = '#28a745';
    }}
}}

function toggleTheme() {{
    isDarkMode = !isDarkMode;
    applyTheme();
}}

function applyTheme() {{
    const body = document.body;
    const mainContent = document.querySelector('main.container');
    const topBar = document.getElementById('top-bar');
    const themeToggle = document.getElementById('theme-toggle');
    const filenameDisplay = document.getElementById('filename-display');
    const sidebar = document.getElementById('sidebar');
    const sidebarInner = document.querySelector('.sidebar-inner');
    
    if (isDarkMode) {{
        // Dark theme
        body.style.background = '#1a1a1a';
        body.style.color = '#e0e0e0';
        
        if (mainContent) {{
            mainContent.style.background = '#2d2d2d';
            mainContent.style.color = '#e0e0e0';
        }}
        
        if (topBar) {{
            topBar.style.background = '#343a40';
            topBar.style.borderBottomColor = '#495057';
        }}
        
        if (filenameDisplay) {{
            filenameDisplay.style.color = '#e0e0e0';
        }}
        
        // Update sidebar
        if (sidebar) {{
            sidebar.style.background = '#2d2d2d';
            sidebar.style.borderRightColor = '#495057';
        }}
        
        if (sidebarInner) {{
            sidebarInner.style.color = '#e0e0e0';
            
            // Update sidebar heading
            const sidebarH2 = sidebarInner.querySelector('h2');
            if (sidebarH2) {{
                sidebarH2.style.color = '#e0e0e0';
                sidebarH2.style.borderBottomColor = '#495057';
            }}
            
            // Update sidebar links
            const sidebarLinks = sidebarInner.querySelectorAll('a');
            sidebarLinks.forEach(link => {{
                link.style.color = '#9db4d0';
            }});
            
            // Update list items
            const sidebarItems = sidebarInner.querySelectorAll('li');
            sidebarItems.forEach(item => {{
                item.style.borderBottomColor = '#495057';
            }});
        }}
        
        themeToggle.innerHTML = '☀️ Light';
        themeToggle.style.background = '#ffc107';
        themeToggle.style.color = '#212529';
        
        // Update toolbar
        const toolbar = document.getElementById('editing-toolbar');
        if (toolbar) {{
            toolbar.style.background = '#343a40';
            toolbar.style.borderBottomColor = '#495057';
        }}
        
        // Update code blocks
        const codeBlocks = document.querySelectorAll('.executable-code-block');
        codeBlocks.forEach(block => {{
            const header = block.querySelector('div');
            if (header) {{
                header.style.background = '#495057';
                header.style.color = '#e0e0e0';
            }}
        }});
        
    }} else {{
        // Light theme
        body.style.background = '#ffffff';
        body.style.color = '#212529';
        
        if (mainContent) {{
            mainContent.style.background = '#ffffff';
            mainContent.style.color = '#212529';
        }}
        
        if (topBar) {{
            topBar.style.background = '#f8f9fa';
            topBar.style.borderBottomColor = '#dee2e6';
        }}
        
        if (filenameDisplay) {{
            filenameDisplay.style.color = '#495057';
        }}
        
        // Reset sidebar to original styles
        if (sidebar) {{
            sidebar.style.background = '';
            sidebar.style.borderRightColor = '';
        }}
        
        if (sidebarInner) {{
            sidebarInner.style.color = '';
            
            // Reset sidebar heading
            const sidebarH2 = sidebarInner.querySelector('h2');
            if (sidebarH2) {{
                sidebarH2.style.color = '';
                sidebarH2.style.borderBottomColor = '';
            }}
            
            // Reset sidebar links
            const sidebarLinks = sidebarInner.querySelectorAll('a');
            sidebarLinks.forEach(link => {{
                link.style.color = '';
            }});
            
            // Reset list items
            const sidebarItems = sidebarInner.querySelectorAll('li');
            sidebarItems.forEach(item => {{
                item.style.borderBottomColor = '';
            }});
        }}
        
        themeToggle.innerHTML = '🌙 Dark';
        themeToggle.style.background = '#6c757d';
        themeToggle.style.color = 'white';
        
        // Reset toolbar
        const toolbar = document.getElementById('editing-toolbar');
        if (toolbar) {{
            toolbar.style.background = '';
            toolbar.style.borderBottomColor = '';
        }}
        
        // Update code blocks
        const codeBlocks = document.querySelectorAll('.executable-code-block');
        codeBlocks.forEach(block => {{
            const header = block.querySelector('div');
            if (header) {{
                header.style.background = '#e9ecef';
                header.style.color = '#495057';
            }}
        }});
    }}
}}

function createEditingToolbar() {{
    const mainContent = document.querySelector('main.container');
    const toolbar = document.createElement('div');
    toolbar.id = 'editing-toolbar';
    toolbar.style.cssText = `
        position: sticky;
        top: 50px;
        left: 0;
        right: 0;
        background: #f8f9fa;
        border-bottom: 1px solid #dee2e6;
        padding: 8px 16px;
        display: flex;
        align-items: center;
        gap: 4px;
        z-index: 999;
        font-family: system-ui, -apple-system, sans-serif;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        margin: 0 -10px 20px -10px;
    `;
    
    const buttons = [
        {{ icon: 'B', title: 'Bold', command: 'bold', style: 'font-weight: bold;' }},
        {{ icon: 'I', title: 'Italic', command: 'italic', style: 'font-style: italic;' }},
        {{ icon: 'U', title: 'Underline', command: 'underline', style: 'text-decoration: underline;' }},
        {{ icon: '&lt;/&gt;', title: 'Code', command: 'code', style: 'font-family: monospace;' }},
        {{ icon: '🔗', title: 'Link', command: 'link', style: 'color: #3498db;' }},
        {{ icon: '📋', title: 'Table', command: 'table', style: 'color: #e74c3c;' }},
        {{ icon: '#', title: 'Header', command: 'header', style: 'font-weight: bold; color: #9b59b6;' }},
        {{ icon: '•', title: 'List', command: 'list', style: 'color: #f39c12;' }},
        {{ icon: '▶', title: 'Executable Code', command: 'exec_code', style: 'color: #27ae60; font-size: 14px;' }}
    ];
    
    buttons.forEach(btn => {{
        const button = document.createElement('button');
        button.innerHTML = btn.icon;
        button.title = btn.title;
        button.style.cssText = `
            background: #e9ecef;
            color: #495057;
            border: 1px solid #ced4da;
            padding: 6px 12px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 500;
            transition: all 0.2s;
            ${{btn.style}}
        `;
        
        button.addEventListener('mouseenter', () => {{
            button.style.background = '#007bff';
            button.style.color = 'white';
            button.style.borderColor = '#007bff';
        }});
        button.addEventListener('mouseleave', () => {{
            button.style.background = '#e9ecef';
            button.style.color = '#495057';
            button.style.borderColor = '#ced4da';
        }});
        
        button.addEventListener('click', (e) => {{
            e.preventDefault();
            executeCommand(btn.command);
        }});
        
        toolbar.appendChild(button);
    }});
    
    // Insert toolbar after the top bar
    const topBar = document.getElementById('top-bar');
    if (topBar && topBar.nextSibling) {{
        mainContent.insertBefore(toolbar, topBar.nextSibling);
    }} else {{
        mainContent.insertBefore(toolbar, mainContent.children[1]);
    }}
}}

let currentHighlightedBlock = null;

function handleCursorPosition() {{
    const selection = window.getSelection();
    const mainContent = document.querySelector('main.container');
    
    // Clear previous highlighting
    if (currentHighlightedBlock) {{
        currentHighlightedBlock.style.outline = '';
        currentHighlightedBlock = null;
    }}
    
    // Don't highlight in read mode
    if (!isEditMode) {{
        return;
    }}
    
    if (!selection.rangeCount) {{
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
        return;
    }}
    
    // Highlight the block
    currentHighlightedBlock = element;
    element.style.outline = '2px solid #3498db';
    element.style.outlineOffset = '2px';
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
        case 'exec_code':
            insertExecutableCodeBlock();
            break;
    }}
    
    handleContentChange();
    handleCursorPosition();
}}

function insertExecutableCodeBlock() {{
    const selection = window.getSelection();
    const range = selection.getRangeAt(0);
    
    // Create executable code block container
    const codeContainer = document.createElement('div');
    codeContainer.className = 'executable-code-block';
    codeContainer.style.cssText = `
        position: relative;
        margin: 20px 0;
        border: 1px solid #ddd;
        border-radius: 8px;
        background: #f8f9fa;
    `;
    
    // Create header with run button
    const header = document.createElement('div');
    header.style.cssText = `
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 12px;
        background: #e9ecef;
        border-bottom: 1px solid #ddd;
        border-radius: 8px 8px 0 0;
        font-size: 12px;
        color: #495057;
    `;
    
    const langSelect = document.createElement('select');
    langSelect.style.cssText = `
        border: 1px solid #ccc;
        border-radius: 4px;
        padding: 4px 8px;
        font-size: 12px;
        background: white;
    `;
    
    const languages = [
        {{value: 'python', label: 'Python'}},
        {{value: 'javascript', label: 'JavaScript (Node.js)'}},
        {{value: 'bash', label: 'Bash'}},
        {{value: 'c', label: 'C'}},
        {{value: 'cpp', label: 'C++'}},
        {{value: 'java', label: 'Java'}},
        {{value: 'rust', label: 'Rust'}},
        {{value: 'go', label: 'Go'}}
    ];
    
    languages.forEach(lang => {{
        const option = document.createElement('option');
        option.value = lang.value;
        option.textContent = lang.label;
        langSelect.appendChild(option);
    }});
    
    const runButton = document.createElement('button');
    runButton.innerHTML = '▶ Run';
    runButton.style.cssText = `
        background: #28a745;
        color: white;
        border: none;
        padding: 6px 12px;
        border-radius: 4px;
        cursor: pointer;
        font-size: 12px;
        display: flex;
        align-items: center;
        gap: 4px;
    `;
    
    runButton.addEventListener('mouseenter', () => {{
        runButton.style.background = '#218838';
    }});
    runButton.addEventListener('mouseleave', () => {{
        runButton.style.background = '#28a745';
    }});
    
    header.appendChild(langSelect);
    header.appendChild(runButton);
    
    // Create code editor
    const codeEditor = document.createElement('pre');
    codeEditor.contentEditable = true;
    codeEditor.style.cssText = `
        margin: 0;
        padding: 16px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 14px;
        line-height: 1.4;
        background: #f8f9fa;
        color: #212529;
        border: none;
        outline: none;
        white-space: pre-wrap;
        min-height: 100px;
        resize: none;
    `;
    
    // Add placeholder text
    codeEditor.textContent = `# Write your code here
print("Hello, World!")`;
    
    // Create output area
    const outputArea = document.createElement('div');
    outputArea.className = 'code-output';
    outputArea.style.cssText = `
        background: #1e1e1e;
        color: #d4d4d4;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 13px;
        padding: 12px;
        border-top: 1px solid #ddd;
        border-radius: 0 0 8px 8px;
        white-space: pre-wrap;
        display: none;
        max-height: 300px;
        overflow-y: auto;
    `;
    
    // Add click handler for run button
    runButton.addEventListener('click', (e) => {{
        e.preventDefault();
        e.stopPropagation();
        executeCode(codeEditor, langSelect, outputArea, runButton);
    }});
    
    // Prevent the code editor from triggering block selection
    codeEditor.addEventListener('click', (e) => {{
        e.stopPropagation();
    }});
    
    // Assemble the block
    codeContainer.appendChild(header);
    codeContainer.appendChild(codeEditor);
    codeContainer.appendChild(outputArea);
    
    // Insert at cursor position
    range.deleteContents();
    range.insertNode(codeContainer);
    
    // Add some space after the block
    const br = document.createElement('br');
    codeContainer.parentNode.insertBefore(br, codeContainer.nextSibling);
    
    selection.removeAllRanges();
    
    // Focus the code editor
    codeEditor.focus();
}}

function executeCode(codeEditor, langSelect, outputArea, runButton) {{
    const code = codeEditor.textContent;
    const language = langSelect.value;
    
    if (!code.trim()) {{
        return;
    }}
    
    // Update button state
    const originalText = runButton.innerHTML;
    runButton.innerHTML = '⏳ Running...';
    runButton.disabled = true;
    runButton.style.background = '#6c757d';
    
    // Show output area
    outputArea.style.display = 'block';
    outputArea.textContent = 'Executing...';
    
    // Send code execution request
    if (ws.readyState === WebSocket.OPEN) {{
        ws.send(JSON.stringify({{
            type: 'execute_code',
            code: code,
            language: language,
            timestamp: Date.now()
        }}));
        
        // Store reference for response handling
        window.pendingExecution = {{
            outputArea: outputArea,
            runButton: runButton,
            originalText: originalText
        }};
    }} else {{
        outputArea.textContent = 'Error: WebSocket connection not available';
        runButton.innerHTML = originalText;
        runButton.disabled = false;
        runButton.style.background = '#28a745';
    }}
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
    const statusIndicator = document.getElementById('status-indicator');
    if (statusIndicator && isEditMode) {{
        statusIndicator.style.background = '#FF9800';
        statusIndicator.innerHTML = '💾 Saving...';
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
        
        const statusIndicator = document.getElementById('status-indicator');
        if (statusIndicator && isEditMode) {{
            statusIndicator.style.background = '#28a745';
            statusIndicator.innerHTML = '✅ Saved';
            
            setTimeout(() => {{
                statusIndicator.style.background = '#007bff';
                statusIndicator.innerHTML = '✏️ Edit Mode';
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
                    elif data.get('type') == 'execute_code':
                        await self.handle_code_execution(websocket, data)
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
    
    async def handle_code_execution(self, websocket, data):
        """Handle code execution requests."""
        code = data.get('code', '')
        language = data.get('language', 'python')
        
        try:
            result = await self._execute_code(code, language)
            await websocket.send(json.dumps({
                "type": "code_execution_result",
                "success": result['success'],
                "output": result['output'],
                "stderr": result.get('stderr', ''),
                "error": result.get('error', '')
            }))
        except Exception as e:
            await websocket.send(json.dumps({
                "type": "code_execution_result",
                "success": False,
                "output": '',
                "stderr": '',
                "error": str(e)
            }))
    
    async def _execute_code(self, code: str, language: str) -> dict:
        """Execute code in the specified language."""
        try:
            # Create temporary file for the code
            with tempfile.NamedTemporaryFile(mode='w', suffix=self._get_file_extension(language), delete=False) as f:
                f.write(code)
                temp_file = f.name
            
            try:
                # Get the command to execute
                cmd = self._get_execution_command(language, temp_file)
                
                if not cmd:
                    return {
                        'success': False,
                        'output': '',
                        'error': f'Unsupported language: {language}'
                    }
                
                # Execute the code with timeout
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=tempfile.gettempdir()
                )
                
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
                    
                    return {
                        'success': process.returncode == 0,
                        'output': stdout.decode('utf-8', errors='replace'),
                        'stderr': stderr.decode('utf-8', errors='replace'),
                        'error': '' if process.returncode == 0 else f'Process exited with code {process.returncode}'
                    }
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
                    return {
                        'success': False,
                        'output': '',
                        'error': 'Code execution timed out (30 seconds)'
                    }
                    
            finally:
                # Clean up temp file
                try:
                    os.unlink(temp_file)
                except OSError:
                    pass
                    
        except Exception as e:
            return {
                'success': False,
                'output': '',
                'error': str(e)
            }
    
    def _get_file_extension(self, language: str) -> str:
        """Get file extension for the given language."""
        extensions = {
            'python': '.py',
            'javascript': '.js',
            'bash': '.sh',
            'c': '.c',
            'cpp': '.cpp',
            'java': '.java',
            'rust': '.rs',
            'go': '.go'
        }
        return extensions.get(language, '.txt')
    
    def _get_execution_command(self, language: str, filename: str) -> list:
        """Get the command to execute code for the given language."""
        commands = {
            'python': ['python3', filename],
            'javascript': ['node', filename],
            'bash': ['bash', filename],
            'c': self._get_c_command(filename),
            'cpp': self._get_cpp_command(filename),
            'java': self._get_java_command(filename),
            'rust': self._get_rust_command(filename),
            'go': ['go', 'run', filename]
        }
        return commands.get(language)
    
    def _get_c_command(self, filename: str) -> list:
        """Get command to compile and run C code."""
        executable = filename.replace('.c', '')
        return ['sh', '-c', f'gcc -o {executable} {filename} && {executable}']
    
    def _get_cpp_command(self, filename: str) -> list:
        """Get command to compile and run C++ code."""
        executable = filename.replace('.cpp', '')
        return ['sh', '-c', f'g++ -o {executable} {filename} && {executable}']
    
    def _get_java_command(self, filename: str) -> list:
        """Get command to compile and run Java code."""
        classname = os.path.basename(filename).replace('.java', '')
        return ['sh', '-c', f'javac {filename} && java -cp {os.path.dirname(filename)} {classname}']
    
    def _get_rust_command(self, filename: str) -> list:
        """Get command to compile and run Rust code."""
        executable = filename.replace('.rs', '')
        return ['sh', '-c', f'rustc -o {executable} {filename} && {executable}']
    
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