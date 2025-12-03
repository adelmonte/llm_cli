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

API_BASE = "https://openrouter.ai/api"
API_KEY = "sk-or-v1-"
MODEL = "z-ai/glm-4.6"

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

SYSTEM_PROMPT = f"""You are a helpful AI assistant with shell command execution capabilities.
Context: {datetime.now().strftime('%b %d %Y')} | {DISTRO} | {SHELL} | Editor: {EDITOR}
## Command Execution
When you need to run system commands, use: [RUN:command_here]
Examples:
- [RUN:pwd] - check current directory
- [RUN:ls -la] - list files
- [RUN:cat file.txt] - read files
- [RUN:grep -r "pattern" .] - search code
Guidelines:
- Use commands to gather information, check state, or perform actions
- You can chain commands with && or use pipes
- Prefer targeted commands over broad exploration
- After getting command output, provide concise, helpful analysis
## Response Style
- Be concise and direct - avoid unnecessary preamble
- Format code with proper syntax highlighting using markdown
- For errors, suggest fixes or next steps
- Use the context above to avoid unnecessary commands
## Common Tasks
- Code review/debugging: Ask to see relevant files first
- System questions: Check with commands rather than guessing
- File operations: Use appropriate tools (cat, grep, find, etc.)
- Scripting: Provide working, tested solutions
Always assume bash shell."""

# Conversation history
messages: List[Dict[str, str]] = []

# Initialize with system prompt
if SYSTEM_PROMPT:
    messages.append({"role": "system", "content": SYSTEM_PROMPT})

# Track current model
current_model = MODEL
model_pricing = {"prompt": 0, "completion": 0}  # dollars per million tokens

# Global flag for cancellation
cancel_event = threading.Event()
spinner_stop = threading.Event()

def fetch_model_pricing():
    """Fetch pricing for current model"""
    global model_pricing
    try:
        response = requests.get(
            f"{API_BASE}/v1/models",
            headers={"Authorization": f"Bearer {API_KEY}"},
            verify=False,
            timeout=5
        )
        models = response.json()['data']
        for m in models:
            if m['id'] == current_model:
                pricing = m.get('pricing', {})
                model_pricing['prompt'] = float(pricing.get('prompt', 0))
                model_pricing['completion'] = float(pricing.get('completion', 0))
                break
    except:
        pass  # Silently fail if can't fetch pricing

def show_greeter():
    """Display greeter with model info"""
    # Use ANSI escape to clear screen AND scrollback buffer
    # \033[2J clears visible screen, \033[3J clears scrollback buffer
    print("\033[2J\033[3J\033[H", end='')

    # Get current working directory with ~ shorthand
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    console.print(f"[bold cyan]⚡ {current_model}[/bold cyan] [dim]|[/dim] [bold yellow]📁 {cwd}[/bold yellow]")
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
    global current_model, model_pricing
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
        
        # Store models data for pricing lookup
        models_dict = {m['id']: m for m in models}
        
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
        stdout, _= proc.communicate(input=models_list)
        
        if proc.returncode == 0 and stdout.strip():
            selected = stdout.strip().split('\t')[0]
            current_model = selected
            # Update pricing info
            if selected in models_dict:
                pricing = models_dict[selected].get('pricing', {})
                model_pricing['prompt'] = float(pricing.get('prompt', 0))
                model_pricing['completion'] = float(pricing.get('completion', 0))
            
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
    # Get current working directory with ~ shorthand
    cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    # Print without Rich markup to avoid corruption
    print(f"\033[1;33m🔧 Command:\033[0m {cmd}")
    print(f"\033[2m📁 {cwd}\033[0m")
    print(f"\033[1;32m[Y]es\033[0m / \033[1;31m[n]o\033[0m / \033[1;36m[e]dit\033[0m ? ", end="")
    sys.stdout.flush()
    
    # Read line input (wait for Enter)
    reply = input().strip().lower()
    
    if reply in ['y', 'yes', '']:
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
    elif reply in ['e', 'edit']:
        # Let user edit the command
        print(f"\033[1;36mEdit command:\033[0m ", end="")
        sys.stdout.flush()
        # Use readline for editing
        try:
            import readline
            readline.set_startup_hook(lambda: readline.insert_text(cmd))
            edited_cmd = input()
            readline.set_startup_hook()
            if edited_cmd:
                return execute_command(edited_cmd)
            else:
                return None, 2  # Cancelled
        except:
            edited_cmd = input()
            if edited_cmd:
                return execute_command(edited_cmd)
            else:
                return None, 2
    return None, 2  # User cancelled

def stream_response(response, cancel_event):
    """Stream and parse response in a thread-safe way"""
    full_content = ""
    usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
    last_chunk_time = time.time()
    
    try:
        for line in response.iter_lines(chunk_size=None, decode_unicode=False):
            if cancel_event.is_set():
                break
            
            # Check for stalled stream (no data for 30 seconds)
            if time.time() - last_chunk_time > 30:
                break
                
            if not line:
                continue
                
            last_chunk_time = time.time()
            
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
                    
                    # Get usage info if available (often in final chunk)
                    usage = chunk.get('usage', {})
                    if usage:
                        usage_data['prompt_tokens'] = usage.get('prompt_tokens', 0)
                        usage_data['completion_tokens'] = usage.get('completion_tokens', 0)
                        usage_data['total_tokens'] = usage.get('total_tokens', 0)
                        usage_data['cost'] = usage.get('cost', 0.0)
                        
                    # Check for finish reason (stream ending)
                    finish_reason = chunk.get('choices', [{}])[0].get('finish_reason')
                    
                    delta = chunk.get('choices', [{}])[0].get('delta', {})
                    content = delta.get('content', '')
                    
                    if content:
                        full_content += content
                        yield content, usage_data
                    elif usage or finish_reason:
                        # Yield usage data or finish signal even without content
                        yield '', usage_data
                        
                except json.JSONDecodeError:
                    continue
                except (KeyError, IndexError, TypeError):
                    # Malformed chunk, skip it
                    continue
        
        # Ensure we yield final usage data if we have it
        if usage_data.get('total_tokens', 0) > 0:
            yield '', usage_data
            
    finally:
        response.close()

def make_api_call(recursive: bool = False) -> bool:
    """Make API call with streaming and handle tool execution"""
    global messages
    
    cancel_event.clear()
    spinner_stop.clear()
    start_time = time.time()
    usage_data = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0}
    
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
            
            # Check for HTTP errors
            if resp.status_code != 200:
                try:
                    error_data = resp.json()
                    error_msg = error_data.get('error', {}).get('message', resp.text)
                except:
                    error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                
                if not cancel_event.is_set():
                    response_container['error'] = Exception(error_msg)
            elif not cancel_event.is_set():
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
        
    # Check for errors (keep spinner running)
    if response_container['error']:
        spinner_stop.set()
        spinner_thread.join(timeout=0.2)
        console.print(f"\n[red]Error: {response_container['error']}[/red]\n")
        return False
        
    response = response_container['response']
    if not response:
        spinner_stop.set()
        spinner_thread.join(timeout=0.2)
        console.print("\n[red]Error: No response received[/red]\n")
        return False

    try:
        full_content = ""
        buffer = ""  # Buffer for detecting [RUN:] tags
        displayed_content = ""  # Track what we've displayed
        command_detected = False
        import re
        
        # Create iterator and wait for first chunk before stopping spinner
        stream_iter = stream_response(response, cancel_event)
        
        try:
            first_chunk, first_usage = next(stream_iter)
            usage_data = first_usage if first_usage.get('total_tokens', 0) > 0 else usage_data
        except StopIteration:
            # Stream ended immediately - empty response
            spinner_stop.set()
            spinner_thread.join(timeout=0.2)
            if recursive:
                # In recursive calls, empty responses might be OK (command already shown)
                console.print("[dim][Empty response from model][/dim]\n")
                return True
            else:
                console.print("\n[red]No content received from API[/red]\n")
                return False
        except Exception as e:
            # Error in stream
            spinner_stop.set()
            spinner_thread.join(timeout=0.2)
            console.print(f"\n[red]Stream error: {e}[/red]\n")
            return False
            
        # Check for cancellation (but allow empty first chunks - they might have metadata)
        if cancel_event.is_set():
            spinner_stop.set()
            spinner_thread.join(timeout=0.2)
            return False
            
        # Got first chunk, now stop spinner and start displaying
        spinner_stop.set()
        spinner_thread.join(timeout=0.2)
        
        # Initialize with first chunk
        full_content = first_chunk
        buffer = first_chunk
        last_render_length = 0
        last_update_time = time.time()
        update_interval = 0.05  # Update every 50ms for smooth streaming
        
        # Process first chunk
        if cancel_event.is_set():
            return False
            
        # Check if first chunk has command
        run_match = re.search(r'\[RUN:?\s*([^\]]+)\]', buffer)
        if run_match:
            command_detected = True
            
        if not command_detected:
            # Use default vertical_overflow to prevent duplication issues
            with Live("", console=console, refresh_per_second=20, transient=False) as live:
                # Update with first chunk
                if first_chunk.strip():
                    live.update(Markdown(full_content, code_theme="nord"))
                    last_render_length = len(full_content)
                    last_update_time = time.time()
                
                # Continue streaming chunks
                for content_chunk, chunk_usage in stream_iter:
                    if cancel_event.is_set():
                        return False
                        
                    full_content += content_chunk
                    buffer += content_chunk
                    
                    if chunk_usage.get('total_tokens', 0) > 0:
                        usage_data = chunk_usage
                    
                    # Check if we have a complete [RUN:...] tag in buffer
                    run_match = re.search(r'\[RUN:?\s*([^\]]+)\]', buffer)
                    if run_match:
                        # Command detected! Stop streaming
                        command_detected = True
                        break
                        
                    # Update display at intervals or when significant content added
                    current_time = time.time()
                    content_delta = len(full_content) - last_render_length
                    
                    if (current_time - last_update_time >= update_interval) or (content_delta > 50):
                        live.update(Markdown(full_content, code_theme="nord"))
                        last_render_length = len(full_content)
                        last_update_time = current_time
                        
                # Final update with all content
                if not command_detected and not cancel_event.is_set():
                    run_match = re.search(r'\[RUN:?\s*([^\]]+)\]', full_content)
                    if run_match:
                        command_detected = True
                    elif full_content.strip():
                        live.update(Markdown(full_content, code_theme="nord"))
                        
            # Live exited - add newline for spacing
            print()
            
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
                # User cancelled - add assistant's message without command tag, but keep user message
                clean_content = re.sub(r'\[RUN:?\s*[^\]]+\]', '', full_content).strip()
                if clean_content:
                    messages.append({"role": "assistant", "content": clean_content})
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
        
        # Show stats if we have content or tokens
        if full_content.strip() or usage_data['total_tokens'] > 0:
            cols = console.width
            # Use OpenRouter's provided cost (they calculate it for us!)
            cost = usage_data.get('cost', 0.0)
            
            # Format stats
            total_tokens = usage_data['total_tokens']
            
            if cost > 0:
                if cost < 0.01:
                    cost_str = f" | ${cost:.6f}"
                else:
                    cost_str = f" | ${cost:.4f}"
                stats = f"{elapsed:.2f}s | {total_tokens} tokens{cost_str}"
            else:
                stats = f"{elapsed:.2f}s | {total_tokens} tokens"
                
            stats_len = len(stats)
            padding = cols - stats_len
            console.print(f"{' ' * padding}[dim]{stats}[/dim]")
            console.print()
            
        elif recursive:
            # Recursive call with no response - this shouldn't happen but handle it
            console.print("[dim][No response received][/dim]\n")
            
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
        # Remove apostrophe from completer delimiters so it's treated as normal text
        readline.set_completer_delims(readline.get_completer_delims().replace("'", ""))
    except ImportError:
        pass
        
    # Setup signal handler for Ctrl+C
    def handle_sigint(sig, frame):
        cancel_event.set()
        spinner_stop.set()
        
    signal.signal(signal.SIGINT, handle_sigint)
    
    # Fetch pricing for default model
    fetch_model_pricing()
    
    # Show greeter
    show_greeter()
    
    # Main loop
    while True:
        cancel_event.clear()
        try:
            # Use input() with prompt - readline handles it properly
            # Wrap ANSI codes in \x01 and \x02 so readline ignores them for cursor positioning
            user_input = input("\x01\033[1;36m\x02>\x01\033[0m\x02 ").strip()
            
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
