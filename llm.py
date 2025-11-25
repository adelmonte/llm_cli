#!/usr/bin/env python3

import os
import sys
import json
import time
import subprocess
import signal
import threading
import itertools
from datetime import datetime
from typing import List, Dict, Optional
import requests
from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from rich.theme import Theme

# Configuration
#API_BASE = "https://llm.ai/api"
#API_KEY = "sk-XXXXXXXXXXXXXXXXXXXXX"
#MODEL = "x-ai/grok-4.1-fast:free"

# Nord theme for markdown only
nord_theme = Theme({
    "markdown.paragraph": "#D8DEE9",
    "markdown.h1": "bold #88C0D0",
    "markdown.h2": "bold #81A1C1",
    "markdown.h3": "bold #5E81AC",
    "markdown.h4": "bold #B48EAD",
    "markdown.h5": "bold #A3BE8C",
    "markdown.h6": "bold #EBCB8B",
    "markdown.code": "#EBCB8B on #3B4252",
    "markdown.code_block": "#D8DEE9",
    "markdown.block_quote": "italic #4C566A",
    "markdown.list": "#88C0D0",
    "markdown.item": "#D8DEE9",
    "markdown.link": "#88C0D0 underline",
    "markdown.link_url": "#5E81AC",
    "markdown.em": "italic #D8DEE9",
    "markdown.strong": "bold #ECEFF4",
    "markdown.s": "strike #D8DEE9",
    "markdown.hr": "#4C566A",
})

console = Console(theme=nord_theme)

# Get system info
def get_distro():
    try:
        with open('/etc/os-release') as f:
            for line in f:
                if line.startswith('ID='):
                    return line.split('=')[1].strip().strip('"')
    except:
        return "unknown"

DISTRO = get_distro()
SHELL = os.path.basename(os.environ.get('SHELL', 'bash'))
EDITOR = os.environ.get('EDITOR', 'vi')

SYSTEM_PROMPT = f"""Current context: {datetime.now().strftime('%b %d %Y')} | Distro: {DISTRO} | Shell: {SHELL} | Editor: {EDITOR}

IMPORTANT: Use the context information above to answer questions when possible. Only run commands when you need information NOT already provided in the context.

To run system commands when needed, use this exact format: [RUN:your_command_here]

Examples:
- Check date: [RUN:date]
- Current directory: [RUN:pwd]
- List files: [RUN:ls -la]
- Chain commands: [RUN:date && whoami]

Run ONLY ONE command per user request. After receiving command output, provide a helpful response but DO NOT run additional verification commands.

Always assume bash. Before running any command, consider if you can answer using the context provided above."""

# Conversation history
messages: List[Dict[str, str]] = []

# Initialize with system prompt
if SYSTEM_PROMPT:
    messages.append({"role": "system", "content": SYSTEM_PROMPT})

# Track current model
current_model = MODEL

# Global flag for cancellation
cancel_event = threading.Event()
spinner_stop = threading.Event()

def show_greeter():
    """Display greeter with model info"""
    console.clear()
    console.print(f"[bold cyan]⚡ {current_model}[/bold cyan]")
    console.print("[dim]/models | /clear | /exit | ctrl+l to clear | ctrl+d to exit | ctrl+c to cancel[/dim]\n")

def clear_conversation():
    """Clear conversation history"""
    global messages
    messages = []
    if SYSTEM_PROMPT:
        messages.append({"role": "system", "content": SYSTEM_PROMPT})
    show_greeter()
    console.print("[bold yellow]Conversation cleared[/bold yellow]\n")

def list_models():
    """Interactive model selection with fzf"""
    global current_model
    
    # Check if fzf is available
    if subprocess.run(['which', 'fzf'], capture_output=True).returncode != 0:
        console.print("[red]fzf not found. Install it first.[/red]")
        return False
    
    try:
        # Get models from API
        response = requests.get(
            f"{API_BASE}/v1/models",
            headers={"Authorization": f"Bearer {API_KEY}"},
            verify=False
        )
        models = response.json()['data']
        
        # Format for fzf
        models_list = '\n'.join([f"{m['id']}\t{m.get('name', m['id'])}" for m in models])
        
        # Run fzf
        proc = subprocess.Popen(
            ['fzf', '--height=40%', '--reverse', '--prompt=Select model: ', 
             '--delimiter=\t', '--with-nth=2'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        
        stdout, _ = proc.communicate(input=models_list)
        
        if proc.returncode == 0 and stdout.strip():
            selected = stdout.strip().split('\t')[0]
            current_model = selected
            console.print(f"\n[bold green]✓ Switched to: {current_model}[/bold green]\n")
            time.sleep(1)
            show_greeter()
            return True
        else:
            console.print()
        
    except Exception as e:
        console.print(f"[red]Error fetching models: {e}[/red]")
        console.print()
    
    return False

def spinner():
    """Show spinner animation"""
    chars = "◜◝◞◟"
    for c in itertools.cycle(chars):
        if spinner_stop.is_set():
            break
        sys.stdout.write(f"\r\033[2K{c} ")
        sys.stdout.flush()
        time.sleep(0.08)
    sys.stdout.write("\r\033[2K")
    sys.stdout.flush()

def extract_run_command(content: str) -> Optional[str]:
    """Extract command from [RUN:...] tag"""
    import re
    
    # Look for [RUN:command] or [RUN command]
    match = re.search(r'\[RUN:?\s*([^\]]+)\]', content)
    if match:
        return match.group(1).strip()
    return None

def execute_command(cmd: str) -> tuple[Optional[str], int]:
    """Execute command with user confirmation"""
    # Print without Rich markup to avoid corruption
    print(f"\033[1;33m🔧 Command:\033[0m {cmd}")
    print(f"\033[1;32m[Y]es\033[0m / \033[1;31m[n]o\033[0m / \033[1;36m[e]dit\033[0m ? ", end="")
    sys.stdout.flush()
    
    # Read single character without adding to history
    import termios
    import tty
    
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        reply = sys.stdin.read(1).lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    
    print(reply)  # Echo the character
    print()
    
    if reply in ['y', '\r', '\n', '']:
        # Execute command
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            output = result.stdout + result.stderr
            
            if result.returncode != 0:
                return f"[Command failed with exit code {result.returncode}]\n{output}", 1
            else:
                return output, 0
                
        except subprocess.TimeoutExpired:
            return "[Command timed out after 30 seconds]", 1
        except Exception as e:
            return f"[Command failed: {e}]", 1
    
    elif reply == 'e':
        # Let user edit the command
        print(f"\033[1;36mEdit command:\033[0m ", end="")
        sys.stdout.flush()
        
        # Restore normal terminal for editing
        try:
            import readline
            readline.set_startup_hook(lambda: readline.insert_text(cmd))
            # Use raw input to avoid history
            edited_cmd = sys.stdin.readline().strip()
            readline.set_startup_hook()
            
            if edited_cmd:
                return execute_command(edited_cmd)
            else:
                return None, 2  # Cancelled
        except:
            edited_cmd = sys.stdin.readline().strip()
            if edited_cmd:
                return execute_command(edited_cmd)
            else:
                return None, 2
    
    return None, 2  # User cancelled

def stream_response(response, cancel_event):
    """Stream and parse response in a thread-safe way"""
    full_content = ""
    total_tokens = 0
    
    try:
        for line in response.iter_lines():
            if cancel_event.is_set():
                break
                
            if not line:
                continue
            
            # Explicitly decode as UTF-8
            try:
                line = line.decode('utf-8') if isinstance(line, bytes) else line
            except UnicodeDecodeError:
                continue
            
            if line.startswith('data: '):
                data = line[6:]
                
                if data.strip() == '[DONE]':
                    break
                
                try:
                    chunk = json.loads(data)
                    
                    # Get usage info if available
                    usage = chunk.get('usage', {})
                    if usage:
                        total_tokens = usage.get('total_tokens', 0)
                    
                    delta = chunk.get('choices', [{}])[0].get('delta', {})
                    content = delta.get('content', '')
                    
                    if content:
                        full_content += content
                        yield content, total_tokens
                
                except json.JSONDecodeError:
                    continue
    finally:
        response.close()

def make_api_call(recursive: bool = False) -> bool:
    """Make API call with streaming and handle tool execution"""
    global messages
    
    cancel_event.clear()
    spinner_stop.clear()
    start_time = time.time()
    total_tokens = 0
    
    # Start spinner in background
    spinner_thread = threading.Thread(target=spinner, daemon=True)
    spinner_thread.start()
    
    response = None
    response_ready = threading.Event()
    response_container = {'response': None, 'error': None}
    
    def make_request():
        try:
            resp = requests.post(
                f"{API_BASE}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": current_model,
                    "messages": messages,
                    "stream": True
                },
                stream=True,
                verify=False
            )
            if not cancel_event.is_set():
                response_container['response'] = resp
        except Exception as e:
            if not cancel_event.is_set():
                response_container['error'] = e
        finally:
            response_ready.set()
    
    # Start request in thread
    request_thread = threading.Thread(target=make_request, daemon=True)
    request_thread.start()
    
    # Wait for response with cancellation check
    while not response_ready.is_set():
        if cancel_event.is_set():
            spinner_stop.set()
            return False
        time.sleep(0.05)
    
    # Stop spinner
    spinner_stop.set()
    spinner_thread.join(timeout=0.2)
    
    # Check for errors
    if response_container['error']:
        console.print(f"\n[red]Error: {response_container['error']}[/red]\n")
        return False
    
    response = response_container['response']
    if not response:
        console.print("\n[red]Error: No response received[/red]\n")
        return False
    
    try:
        full_content = ""
        buffer = ""  # Buffer for detecting [RUN:] tags
        displayed_content = ""  # Track what we've displayed
        command_detected = False
        
        import re
        
        with Live("", refresh_per_second=20, console=console) as live:
            for content_chunk, tokens in stream_response(response, cancel_event):
                if cancel_event.is_set():
                    return False
                
                full_content += content_chunk
                buffer += content_chunk
                total_tokens = tokens if tokens > 0 else total_tokens
                
                # Check if we have a complete [RUN:...] tag in buffer
                run_match = re.search(r'\[RUN:?\s*([^\]]+)\]', buffer)
                
                if run_match:
                    # Command detected! Stop streaming display
                    command_detected = True
                    live.update("")
                    break
                
                # Check if buffer might be starting a [RUN tag
                # Only hold back if we see potential start of tag
                if '[RUN' in buffer[-10:] or '[RU' in buffer[-10:] or '[R' in buffer[-10:]:
                    # Hold back potential tag start, display the rest
                    safe_index = buffer.rfind('[', max(0, len(buffer) - 10))
                    if safe_index > len(displayed_content):
                        to_display = buffer[len(displayed_content):safe_index]
                        if to_display:
                            displayed_content += to_display
                            live.update(Markdown(displayed_content, code_theme="nord"))
                else:
                    # Safe to display everything
                    if len(buffer) > len(displayed_content):
                        displayed_content = buffer
                        live.update(Markdown(displayed_content, code_theme="nord"))
            
            # After stream ends, handle remaining buffer
            if not command_detected and not cancel_event.is_set():
                # Check one more time for command in final buffer
                run_match = re.search(r'\[RUN:?\s*([^\]]+)\]', full_content)
                if run_match:
                    command_detected = True
                else:
                    # Display any remaining content
                    if len(full_content) > len(displayed_content):
                        live.update(Markdown(full_content, code_theme="nord"))
        
        # Check if cancelled
        if cancel_event.is_set():
            return False
        
        elapsed = time.time() - start_time
        
        # Check if response contains a command to run
        command = extract_run_command(full_content)
        
        if command and not recursive:
            # Execute command directly without showing [RUN:...] output
            cmd_output, exec_status = execute_command(command)
            
            # Handle cancellation
            if exec_status == 2:
                # Remove the user's unanswered question from history
                messages.pop()
                return True
            
            # Add assistant's message to history
            messages.append({"role": "assistant", "content": full_content})
            
            # Handle command failure
            if exec_status != 0:
                messages.append({"role": "user", "content": "Command failed."})
                return make_api_call(recursive=True)
            
            # Show command output
            console.print(f"[dim]{cmd_output}[/dim]")
            
            # Add command output as user message
            messages.append({"role": "user", "content": f"Command output:\n{cmd_output}"})
            
            # Get final response from AI
            return make_api_call(recursive=True)
        
        # No command detected - response already displayed via Live
        # Just show stats
        cols = console.width
        stats = f"{elapsed:.2f}s | {total_tokens} tokens"
        stats_len = len(stats)
        padding = cols - stats_len
        console.print(f"{' ' * padding}[dim]{stats}[/dim]")
        console.print()
        
        # Add to history (strip [RUN:] tags if this is a recursive call)
        if recursive and command:
            full_content = re.sub(r'\[RUN:?\s*[^\]]+\]', '', full_content).strip()
        
        messages.append({"role": "assistant", "content": full_content})
        
        return True
        
    except KeyboardInterrupt:
        cancel_event.set()
        if response:
            response.close()
        return False
    except Exception as e:
        if response:
            response.close()
        if not cancel_event.is_set():
            console.print(f"\n[red]Error: {e}[/red]\n")
        return False

def main():
    """Main loop"""
    # Disable SSL warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    # Disable Ctrl+C echo
    import termios
    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    new_attrs = old_attrs[:]
    new_attrs[3] = new_attrs[3] & ~termios.ECHOCTL
    termios.tcsetattr(fd, termios.TCSANOW, new_attrs)
    
    # Setup readline WITHOUT persistent history file
    try:
        import readline
        # Just set history length for this session only
        readline.set_history_length(1000)
        
        # Bind Ctrl+L to clear conversation
        readline.parse_and_bind('"\\C-l": "\\C-a\\C-k/clear\\n"')
        
    except ImportError:
        pass
    
    # Setup signal handler for Ctrl+C
    def handle_sigint(sig, frame):
        cancel_event.set()
        spinner_stop.set()
    
    signal.signal(signal.SIGINT, handle_sigint)
    
    # Show greeter
    show_greeter()
    
    # Main loop
    while True:
        cancel_event.clear()
        
        try:
            # Use input() with prompt - readline handles it properly
            user_input = input("\033[1;36m>\033[0m ").strip()
            
            if not user_input:
                continue
            
            # Handle commands
            if user_input == "/models":
                list_models()
                continue
            
            if user_input == "/clear":
                clear_conversation()
                continue
            
            if user_input == "/exit":
                print()
                break
            
            # Add user message
            messages.append({"role": "user", "content": user_input})
            
            # Make API call
            result = make_api_call(recursive=False)
            
            if not result:
                # If cancelled, remove the unanswered message
                if cancel_event.is_set() and messages and messages[-1]["role"] == "user":
                    messages.pop()
                    console.print()
            
        except KeyboardInterrupt:
            print()
            continue
        except EOFError:
            print()
            break

if __name__ == "__main__":
    main()