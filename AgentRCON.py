import os
import sys

# Configure stdout and stderr to use UTF-8 and replace encoding errors to prevent crashes on Windows console
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass

import re
import socket
import subprocess
import threading
import time
import json
import zipfile
import urllib.request
import urllib.parse
from openai import OpenAI
import psutil
from mcrcon import MCRcon
from http.server import BaseHTTPRequestHandler, HTTPServer

# Rich imports for high-end TUI styling
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# --- ARGUMENT PARSING & PATH RELOCALIZATION ---
server_dir = os.path.dirname(os.path.abspath(__file__))
for i, arg in enumerate(sys.argv):
    if arg == "--server-dir" and i + 1 < len(sys.argv):
        server_dir = sys.argv[i+1].strip('"').strip("'")
        break

RCON_PASS = "1234"
RCON_PORT = 25575
MC_PORT = 25565
JAR_NAME = "fabric-server-launch.jar"

def parse_server_properties(sd):
    global RCON_PASS, RCON_PORT, MC_PORT
    props_path = os.path.join(sd, "server.properties")
    if os.path.exists(props_path):
        try:
            with open(props_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key, val = key.strip(), val.strip()
                        if key == "rcon.password":
                            RCON_PASS = val
                        elif key == "rcon.port":
                            RCON_PORT = int(val)
                        elif key == "server-port":
                            MC_PORT = int(val)
        except Exception:
            pass

def detect_jar_name(sd):
    for name in ["fabric-server-launch.jar", "server.jar", "minecraft_server.jar"]:
        if os.path.exists(os.path.join(sd, name)):
            return name
    try:
        if os.path.exists(sd):
            for f in os.listdir(sd):
                if f.endswith(".jar") and f != "server.jar":
                    return f
    except Exception:
        pass
    return "server.jar"

parse_server_properties(server_dir)
JAR_NAME = detect_jar_name(server_dir)

LOG_PATH = os.path.join(server_dir, "logs", "latest.log")
MEMORY_PATH = os.path.join(server_dir, "player_memories.json")
REF_PATH = os.path.join(server_dir, "minecraft_reference.txt")
MODS_DIR = os.path.join(server_dir, "mods")
SERVER_JAR = os.path.join(server_dir, "server.jar")

# Security guardrails
BLACKLISTED_COMMANDS = {"stop", "op", "deop", "ban", "ban-ip", "pardon", "pardon-ip", "kick", "whitelist", "save-all"}

# Ollama API Configuration
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e2b-it-qat")

# Initialize Rich Console and OpenAI client
console = Console()
client = OpenAI(base_url=OLLAMA_URL, api_key="ollama")

# Global states
server_process = None
server_status = "SLEEPING"  # SLEEPING, STARTING, RUNNING, STOPPING
server_uptime_start = None
player_sessions = {}
player_count = 0
online_players_list = []
empty_start_time = None
idle_time_limit = 300  # 5 minutes auto-sleep

model_loaded_and_ready = False
model_loading_in_progress = False

def load_memories():
    global player_sessions
    if os.path.exists(MEMORY_PATH):
        try:
            with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                player_sessions = json.load(f)
        except Exception:
            player_sessions = {}
    else:
        player_sessions = {}

def save_memories():
    try:
        with open(MEMORY_PATH, "w", encoding="utf-8") as f:
            json.dump(player_sessions, f, indent=2)
    except Exception:
        pass

def get_loaded_model():
    try:
        url = OLLAMA_URL.rstrip('/') + "/models"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data and "data" in data and len(data["data"]) > 0:
                model_ids = [m["id"] for m in data["data"]]
                if OLLAMA_MODEL in model_ids:
                    return OLLAMA_MODEL
                for mid in model_ids:
                    if OLLAMA_MODEL.split(':')[0] == mid.split(':')[0]:
                        return mid
                return model_ids[0]
    except Exception:
        pass
    return None

def warmup_model():
    global model_loaded_and_ready, model_loading_in_progress
    if model_loaded_and_ready or model_loading_in_progress:
        return
    model_loading_in_progress = True
    console.print("[bold yellow][*] Warming up AI model in background...[/bold yellow]")
    try:
        active_model = get_loaded_model() or OLLAMA_MODEL
        # Send a dummy quick request to load it into memory
        client.chat.completions.create(
            model=active_model,
            messages=[{"role": "user", "content": "warmup"}],
            max_tokens=1
        )
        model_loaded_and_ready = True
        console.print("[bold green][+] AI model is fully loaded and ready in memory![/bold green]")
    except Exception as e:
        console.print(f"[bold red][-] Failed to warm up AI model: {e}[/bold red]")
    finally:
        model_loading_in_progress = False

def extract_player_name(line):
    try:
        match = re.search(r"<([^>]+)>", line)
        if match:
            name = match.group(1).strip()
            if name.lower() not in {"server", "system", "rcon", "agentrcon", "computah", "computa"}:
                return name
    except Exception:
        pass
    return None


def search_item_by_name(query):
    query = query.lower().strip()
    if not query:
        return "Query is empty."
    
    results = []
    jars_to_scan = []
    
    if os.path.exists(MODS_DIR):
        for f in os.listdir(MODS_DIR):
            if f.endswith(".jar"):
                jars_to_scan.append(os.path.join(MODS_DIR, f))
    if os.path.exists(SERVER_JAR):
        jars_to_scan.append(SERVER_JAR)
        
    console.print(f"[bold grey53][*] Scanning {len(jars_to_scan)} jar archives for '{query}'...[/bold grey53]")
    
    for jar_path in jars_to_scan:
        try:
            with zipfile.ZipFile(jar_path, 'r') as z:
                for name in z.namelist():
                    if "assets/" in name and "/lang/" in name and name.endswith(".json"):
                        if "en_us" in name.lower() or len(z.namelist()) < 100:
                            try:
                                lang_data = json.loads(z.read(name).decode("utf-8", errors="ignore"))
                                for key, val in lang_data.items():
                                    if query in val.lower():
                                        parts = key.split('.')
                                        if len(parts) >= 3 and parts[0] in ["item", "block", "entity"]:
                                            modid = parts[1]
                                            itemid = "_".join(parts[2:])
                                            res_id = f"{modid}:{itemid}"
                                            results.append(f"- Name: \"{val}\" -> ID: {res_id} (Type: {parts[0]})")
                            except Exception:
                                pass
        except Exception:
            pass
            
        if len(results) >= 20:
            break
            
    if results:
        return "\n".join(results[:15])
    return f"No items or blocks matching '{query}' found in mod translation files."

def list_installed_mods():
    console.print("[bold grey53][*] Listing installed mods...[/bold grey53]")
    mods = []
    if os.path.exists(MODS_DIR):
        try:
            for f in os.listdir(MODS_DIR):
                if f.endswith(".jar"):
                    jar_path = os.path.join(MODS_DIR, f)
                    try:
                        with zipfile.ZipFile(jar_path, 'r') as z:
                            if "fabric.mod.json" in z.namelist():
                                mod_info = json.loads(z.read("fabric.mod.json").decode("utf-8", errors="ignore"))
                                name = mod_info.get("name") or mod_info.get("id") or f.replace(".jar", "")
                                version = mod_info.get("version", "")
                                mods.append(f"{name} ({version})" if version else name)
                            else:
                                mods.append(f.replace(".jar", ""))
                    except Exception:
                        mods.append(f.replace(".jar", ""))
        except Exception as e:
            return f"Error listing mods: {e}"
    if mods:
        mods.sort()
        return "\n".join(mods)
    return "No mods found in the mods directory."

def web_search(query):
    console.print(f"[bold grey53][*] Searching the web for: '{query}'...[/bold grey53]")
    try:
        url = "https://www.mojeek.com/search?q=" + urllib.parse.quote(query)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as response:
            html = response.read().decode("utf-8", errors="ignore")
            
        results = []
        snippets = re.findall(r'<p class="s">(.*?)</p>', html, re.DOTALL)
        
        def clean_html(text):
            text = re.sub(r'<[^>]+>', '', text)
            html_entities = {
                "&amp;": "&", "&quot;": '"', "&lt;": "<", "&gt;": ">",
                "&#39;": "'", "&#x27;": "'", "&#8217;": "'", "&#8216;": "'",
                "&ldquo;": '"', "&rdquo;": '"', "&ndash;": "-", "&mdash;": "-"
            }
            for entity, replacement in html_entities.items():
                text = text.replace(entity, replacement)
            return text.strip()
            
        for i in range(min(5, len(snippets))):
            snippet = clean_html(snippets[i])
            if snippet:
                results.append(f"- {snippet}")
                
        if results:
            return "\n".join(results)
    except Exception as e:
        console.print(f"[bold red][-] Search failed: {e}[/bold red]")
    return "No search results found."

def clean_command(cmd, player_name="Console"):
    cmd = cmd.strip()
    if cmd.startswith("/"):
        cmd = cmd[1:]
    # Fix tilde spacing: e.g., ~ -4 -> ~-4, ^ 10 -> ^10
    cmd = re.sub(r'([~^])\s+([+-]?\d+)', r'\1\2', cmd)
    # Replace placeholder player names and wrap coordinates only if not Console
    if player_name != "Console":
        cmd = re.sub(r'\b(player|player_name|playername)\b', player_name, cmd, flags=re.IGNORECASE)
        # Auto-wrap relative coordinate commands if not already executed at a target
        if ("~" in cmd or "^" in cmd) and not cmd.lower().startswith("execute"):
            cmd = f"execute at {player_name} run {cmd}"
    return cmd

def run_rcon_command(cmd, player_name="Console"):
    cmd = clean_command(cmd, player_name)
    if not cmd:
        return "Command is empty."
    
    root_cmd = cmd.split(" ")[0].lower()
    if root_cmd in BLACKLISTED_COMMANDS:
        return f"Blocked: Command '{root_cmd}' is unauthorized for safety reasons."
    
    console.print(f"[bold grey53][*] Executing RCON: {cmd}[/bold grey53]")
    try:
        with MCRcon("127.0.0.1", RCON_PASS, port=RCON_PORT) as mcr:
            resp = mcr.command(cmd)
            return resp if resp else "Command executed successfully (no output)."
    except Exception as e:
        return f"RCON Connection Error: {e}"

def run_rcon_commands(commands, player_name="Console"):
    if not isinstance(commands, list):
        return "Error: commands must be a list of strings."
    
    results = []
    try:
        with MCRcon("127.0.0.1", RCON_PASS, port=RCON_PORT) as mcr:
            for cmd in commands:
                cmd = clean_command(cmd, player_name)
                if not cmd:
                    continue
                root_cmd = cmd.split(" ")[0].lower()
                if root_cmd in BLACKLISTED_COMMANDS:
                    results.append(f"Blocked: Command '{root_cmd}' is unauthorized.")
                    continue
                console.print(f"[bold grey53][*] Executing RCON: {cmd}[/bold grey53]")
                resp = mcr.command(cmd)
                results.append(f"Command '{cmd}': {resp if resp else 'Success'}")
                time.sleep(0.05)
    except Exception as e:
        return f"RCON Connection Error: {e}"
    return "\n".join(results)

def execute_python_code(code):
    console.print(f"[bold grey53][*] Executing Python code...[/bold grey53]")
    try:
        temp_dir = os.path.join(server_dir, "scratch")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, "temp_tool_execution.py")
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(code)
            
        res = subprocess.run(
            [sys.executable, temp_file],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=server_dir
        )
        output = res.stdout + res.stderr
        return output if output.strip() else "Python code executed successfully (no output)."
    except subprocess.TimeoutExpired:
        return "Error: Python code execution timed out (limit 15 seconds)."
    except Exception as e:
        return f"Error executing Python code: {e}"

def execute_tool(name, args, player_name):
    if name == "run_rcon_commands":
        cmds = args.get("commands", [])
        return run_rcon_commands(cmds, player_name)
    elif name == "run_rcon_command":
        cmd = args.get("command", "")
        return run_rcon_command(cmd, player_name)
    elif name == "web_search":
        q = args.get("query", "")
        return web_search(q)
    elif name == "search_item_by_name":
        q = args.get("query", "")
        return search_item_by_name(q)
    elif name == "list_installed_mods":
        return list_installed_mods()
    elif name == "execute_python_code":
        code = args.get("code", "")
        return execute_python_code(code)
    else:
        return f"Unknown tool: {name}"

def start_server():
    global server_process, server_status, server_uptime_start, empty_start_time
    if server_status != "SLEEPING":
        console.print("[bold red][-] Server is not in SLEEPING state.[/bold red]")
        return
    
    console.print("[bold yellow][*] Waking up server... Launching Java process.[/bold yellow]")
    server_status = "STARTING"
    server_uptime_start = time.time()
    empty_start_time = None
    
    try:
        # Redirect stdout/stderr to DEVNULL as we monitor latest.log
        server_process = subprocess.Popen(
            ["java", "-Xmx8G", "-Xms8G", "-jar", JAR_NAME, "nogui"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=server_dir,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        )
    except Exception as e:
        console.print(f"[bold red][-] Error starting process: {e}[/bold red]")
        console.print(f"[bold red][-] server_dir: {repr(server_dir)} (exists: {os.path.exists(server_dir)})[/bold red]")
        console.print(f"[bold red][-] JAR_NAME: {repr(JAR_NAME)}[/bold red]")
        server_status = "SLEEPING"

def stop_server():
    global server_status, server_process
    if server_status not in ["STARTING", "RUNNING"]:
        console.print("[bold red][-] Server is not running.[/bold red]")
        return
    
    console.print("[bold yellow][*] Stopping server cleanly...[/bold yellow]")
    server_status = "STOPPING"
    try:
        with MCRcon("127.0.0.1", RCON_PASS, port=RCON_PORT) as mcr:
            mcr.command("stop")
    except Exception:
        if server_process:
            server_process.kill()
            
    if server_process:
        server_process.wait()
        
    server_process = None
    server_status = "SLEEPING"
    console.print("[bold green][+] Server stopped. Return to wake-up port listening.[/bold green]")

def handle_agentic_loop(question, player_name="Player", use_web=False):
    reference_data = ""
    if os.path.exists(REF_PATH):
        try:
            with open(REF_PATH, "r", encoding="utf-8", errors="ignore") as f:
                reference_data = f.read().strip()
        except Exception:
            pass

    system_prompt = f"""You are AgentRCON, an autonomous, highly accurate Minecraft 1.20.1 Server Manager AI.
The player interacting with you is '{player_name}'. You have direct command console control via RCON tools.
You are also happy to assist with general, non-Minecraft questions by using your general knowledge or the `web_search` tool to look up real-time information.

To accomplish tasks or answer queries, you use a ReAct (Reasoning -> Action -> Observation) loop.
You must output your thoughts and tool calls in the following exact format:
<THOUGHT>Your planning/reasoning step. Break down complex requests into sequential steps.</THOUGHT>
<CALL name="tool_name">{{\"arg_name\": \"value\"}}</CALL>

Available Tools:
1. `run_rcon_commands`: Runs a list of Minecraft console commands sequentially. Use this to execute one or multiple commands!
   Arguments: {{"commands": ["command_without_leading_slash_1", "command_without_leading_slash_2", ...]}}
   Example: <CALL name="run_rcon_commands">{{"commands": ["give {player_name} minecraft:cooked_cod 1", "effect give {player_name} speed 30 1"]}}</CALL>

2. `run_rcon_command`: Runs a single Minecraft console command.
   Arguments: {{"command": "command_without_leading_slash"}}
   Example: <CALL name="run_rcon_command">{{"command": "give {player_name} minecraft:cooked_cod 1"}}</CALL>
   
3. `search_item_by_name`: Searches mod lang files for display name to ID mappings (use when user asks for modded items!).
   Arguments: {{"query": "item display name"}}
   Example: <CALL name="search_item_by_name">{{"query": "woodcutter"}}</CALL>

4. `web_search`: Queries the web for real-world information, recipes, item IDs, or general news.
   Arguments: {{"query": "search term"}}
   Example: <CALL name="web_search">{{"query": "who won the 2026 nba finals"}}</CALL>

5. `list_installed_mods`: Returns a list of all mods (and their versions) currently installed on the server.
   Arguments: None
   Example: <CALL name="list_installed_mods">{{}}</CALL>

6. `execute_python_code`: Executes arbitrary Python 3 code in a safe subprocess sandbox and returns stdout/stderr. Use this to write complex scripts, parse web pages, run math calculations, query external APIs, or create temporary custom tools.
   Arguments: {{"code": "python_code_string"}}
   Example: <CALL name="execute_python_code">{{"code": "import urllib.request\nhtml = urllib.request.urlopen('https://some-api.com').read().decode()\nprint(html)"}}</CALL>

IMPORTANT RULES:
- When targeting the player, you MUST use their exact username '{player_name}' in console commands instead of selectors like '@p' or '@s'.
- Minecraft 1.20.1 uses curly brace NBT syntax (e.g. `minecraft:diamond_sword{{display:{{Name:'{{"text":"Legendary Sword"}}'}}}}`). Square brackets `[]` are 1.21+ components and will CRASH the server.
- NEVER put spaces between relative coordinate tildes ('~') and their values (e.g. write '~-4' or '~2', NOT '~ -4' or '~ 2').
- Console executes from server center (no position). You MUST prefix all coordinate-dependent commands (like setblock, fill, summon) with `execute at {player_name} run ...` so they execute at the player's location.
- NEVER run administrative/destructive commands: stop, op, deop, ban, ban-ip, kick, whitelist.
- If you need to perform actions not covered by existing tools (e.g. doing complex calculations, scraping structured web data, calling JSON APIs, or creating custom tools), you can write and execute a custom Python script using the `execute_python_code` tool.
- Be extremely brief and concise in your responses. Do NOT append open-ended follow-up questions (such as "How can I assist you further?", "Is there anything else I can do?") when you successfully complete a task. Just state that the task was completed or provide the requested information, and stop.

Here is your local Minecraft 1.20.1 database containing exact Item IDs, Entity IDs, Status Effects, and Command Syntax:
---
{reference_data}
---

Your loop structure:
- You output <THOUGHT> followed by a <CALL> (if a tool is needed).
- The system will run the tool and return the output as <OBSERVATION>content</OBSERVATION>.
- You analyze the observation, plan your next action, and call another tool if needed.
- If you make a mistake (e.g. observation says "Unknown item"), you must use your thought step to correct it and call the corrected command.
- Once your goals are complete, or you just want to talk/reply to the player, output:
<SAY>Your friendly, concise reply to the player. Wrap it inside <SAY> and </SAY> tags.</SAY>
"""

    if player_name not in player_sessions:
        player_sessions[player_name] = []
        
    history = player_sessions[player_name]
    if len(history) > 10:
        history = history[-10:]
        
    history.append({"role": "user", "content": f"Query: {question} (Web search requested: {use_web})"})
    
    active_model = get_loaded_model() or OLLAMA_MODEL
    console.print(f"\n[bold green][+] AgentRCON starting loop for '{player_name}'...[/bold green]")
    
    max_iterations = 5
    loop_count = 0
    final_reply = ""
    
    while loop_count < max_iterations:
        loop_count += 1
        console.print(f"[bold grey53][*] Iteration {loop_count}/{max_iterations}[/bold grey53]")
        
        messages = [{"role": "system", "content": system_prompt}] + history
        
        try:
            response = client.chat.completions.create(
                model=active_model,
                messages=messages,
                temperature=0.2,
                extra_body={"options": {"num_ctx": 65536}}
            )
            ai_output = response.choices[0].message.content.strip()
        except Exception as e:
            console.print(f"[bold red][-] Ollama API error: {e}[/bold red]")
            final_reply = "I encountered an API error while processing your request."
            break
            
        # Parse tags
        thought_match = re.search(r"<THOUGHT>\s*(.*?)\s*</THOUGHT>", ai_output, re.DOTALL | re.IGNORECASE)
        call_match = re.search(r"<CALL\s+name=\"([^\"]+)\"\s*>(.*?)</CALL>", ai_output, re.DOTALL | re.IGNORECASE)
        say_match = re.search(r"<SAY>\s*(.*?)\s*</SAY>", ai_output, re.DOTALL | re.IGNORECASE)
        
        if thought_match:
            console.print(Panel(thought_match.group(1).strip(), title="Thought", border_style="yellow"))
            
        history.append({"role": "assistant", "content": ai_output})
        
        if call_match:
            tool_name = call_match.group(1).strip()
            tool_args_str = call_match.group(2).strip()
            try:
                tool_args = json.loads(tool_args_str)
            except Exception:
                tool_args = {}
                
            console.print(f"[bold green]Tool Call:[/bold green] {tool_name}({tool_args_str})")
            observation = execute_tool(tool_name, tool_args, player_name)
            console.print(Panel(observation, title="Observation", border_style="blue"))
            
            history.append({"role": "user", "content": f"<OBSERVATION>{observation}</OBSERVATION>"})
        else:
            if say_match:
                final_reply = say_match.group(1).strip()
            else:
                final_reply = ai_output
            break
            
    if not final_reply:
        final_reply = "I completed my actions."
        
    console.print(Panel(final_reply, title="Final Response to Game", border_style="green"))
    
    try:
        with MCRcon("127.0.0.1", RCON_PASS, port=RCON_PORT) as mcr:
            clean_reply = re.sub(r"<[^>]+>", "", final_reply).strip()
            for para in clean_reply.split("\n"):
                para = para.strip()
                if para:
                    mcr.command(f"say AgentRCON: {para}")
                    time.sleep(0.3)
    except Exception as e:
        console.print(f"[bold red][-] RCON Broadcast Error: {e}[/bold red]")
        
    player_sessions[player_name] = history
    save_memories()

def socket_listener_loop():
    global server_status
    while True:
        if server_status == "SLEEPING":
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("0.0.0.0", MC_PORT))
                    s.listen(1)
                    s.settimeout(2)
                    while server_status == "SLEEPING":
                        try:
                            conn, addr = s.accept()
                            conn.close()
                            start_server()
                            break
                        except socket.timeout:
                            continue
            except Exception:
                time.sleep(2)
        else:
            time.sleep(1)

def check_rcon_and_update_status():
    global server_status, server_process
    try:
        # Check if RCON port is open first (fast check)
        with socket.create_connection(("127.0.0.1", RCON_PORT), timeout=1.0) as s:
            pass
        # Try RCON authentication
        with MCRcon("127.0.0.1", RCON_PASS, port=RCON_PORT) as mcr:
            # RCON is active! Set status to RUNNING if not already
            if server_status != "RUNNING":
                console.print("[bold green][+] Detected Minecraft server is running via RCON connection![/bold green]")
                server_status = "RUNNING"
            
            # If server_process is None or dead, try to attach telemetry
            if server_process is None or (hasattr(server_process, 'poll') and server_process.poll() is not None):
                # Search for Java process pid to wrap it
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        cmdline = proc.info.get('cmdline') or []
                        if 'java' in proc.info['name'].lower():
                            if any(JAR_NAME in arg for arg in cmdline):
                                class DummyProcess:
                                    def __init__(self, pid):
                                        self.pid = pid
                                    def poll(self):
                                        return None if psutil.pid_exists(self.pid) else 0
                                    def kill(self):
                                        try:
                                            psutil.Process(self.pid).kill()
                                        except Exception:
                                            pass
                                    def wait(self):
                                        try:
                                            p = psutil.Process(self.pid)
                                            p.wait()
                                        except Exception:
                                            pass
                                server_process = DummyProcess(proc.info['pid'])
                                console.print(f"[bold green][+] Successfully attached telemetry to Java process PID {proc.info['pid']}[/bold green]")
                                break
                    except Exception:
                        pass
            return True
    except Exception:
        # RCON is not active or connection failed
        return False

def check_if_server_already_running():
    check_rcon_and_update_status()

def stats_monitoring_loop():
    global server_status, empty_start_time, player_count, online_players_list, server_process
    while True:
        time.sleep(3)  # Check every 3 seconds for fast status updates
        
        # If server is in STARTING or SLEEPING state, check if RCON is responsive
        if server_status in ["STARTING", "SLEEPING"]:
            check_rcon_and_update_status()
            
        if server_status == "RUNNING":
            try:
                with MCRcon("127.0.0.1", RCON_PASS, port=RCON_PORT) as mcr:
                    resp = mcr.command("list")
                    match = re.search(r"There are (\d+) of \d+ players online(?::\s*(.*))?", resp)
                    if match:
                        player_count = int(match.group(1))
                        players_str = match.group(2)
                        if players_str:
                            online_players_list = [p.strip() for p in players_str.split(",")]
                        else:
                            online_players_list = []
                            
                if player_count == 0:
                    if empty_start_time is None:
                        empty_start_time = time.time()
                    elapsed = time.time() - empty_start_time
                    if elapsed >= idle_time_limit:
                        console.print(f"\n[bold yellow][*] Idle timeout reached. Putting server to sleep...[/bold yellow]")
                        stop_server()
                else:
                    empty_start_time = None
            except Exception:
                # If RCON fails when RUNNING, check if server crashed or was manually stopped
                if not check_rcon_and_update_status():
                    # RCON is not responsive. Check if the subprocess is dead or gone.
                    if server_process is None or (hasattr(server_process, 'poll') and server_process.poll() is not None):
                        console.print("[bold red][-] Minecraft server is no longer active.[/bold red]")
                        server_status = "SLEEPING"
                        server_process = None
        elif server_status == "STARTING":
            # Check if subprocess died unexpectedly
            if server_process and server_process.poll() is not None:
                console.print("[bold red][-] Server process died during startup![/bold red]")
                server_process = None
                server_status = "SLEEPING"

def watch_logs():
    global server_status
    
    f = None
    last_position = 0
    
    while True:
        if f is None:
            if not os.path.exists(LOG_PATH):
                time.sleep(1)
                continue
            try:
                f = open(LOG_PATH, "r", encoding="utf-8", errors="ignore")
                # On initial daemon startup, seek to end so we don't parse historical logs
                f.seek(0, os.SEEK_END)
                last_position = f.tell()
            except Exception:
                time.sleep(1)
                continue

        try:
            # Check if file was truncated or recreated
            if os.path.exists(LOG_PATH):
                current_size = os.path.getsize(LOG_PATH)
                if current_size < last_position:
                    console.print("[bold cyan][*] Log file truncated or recreated. Reopening...[/bold cyan]")
                    f.close()
                    try:
                        f = open(LOG_PATH, "r", encoding="utf-8", errors="ignore")
                        last_position = 0
                    except Exception:
                        f = None
                        last_position = 0
                        time.sleep(1)
                        continue
            else:
                f.close()
                f = None
                last_position = 0
                continue

            line = f.readline()
            if not line:
                # We reached EOF. Check if file has grown but Python is caching EOF on Windows
                if os.path.exists(LOG_PATH):
                    current_size = os.path.getsize(LOG_PATH)
                    if current_size > last_position:
                        f.close()
                        f = open(LOG_PATH, "r", encoding="utf-8", errors="ignore")
                        f.seek(last_position)
                time.sleep(0.2)
                continue
                
            last_position = f.tell()
            
            # Print parsed chat lines nicely to console
            player_name = extract_player_name(line)
            if player_name:
                message = line.split(f"<{player_name}>")[-1].strip()
                console.print(f"[cyan]<{player_name}>[/cyan] {message}")
                
            # If server indicates startup completion
            if "Done (" in line and "s)! For help" in line:
                server_status = "RUNNING"
                console.print("[bold green][+] Server is fully loaded and ready![/bold green]")
                
            # Trigger check
            triggers = ["agentrcon", "agent", "computahh", "computah", "computer", "computa", "compuda"]
            trigger_found = None
            trigger_idx = -1
            line_lower = line.lower()
            
            for trig in triggers:
                idx = line_lower.find(trig)
                if idx != -1:
                    trigger_found = trig
                    trigger_idx = idx
                    break
            
            if trigger_found and player_name:
                question = line[trigger_idx + len(trigger_found):].strip()
                question = re.sub(r"^[^a-zA-Z0-9]+", "", question).strip()
                # Run ReAct loop in a background thread to keep log reading fluid
                threading.Thread(target=handle_agentic_loop, args=(question, player_name, False), daemon=True).start()
            elif "!web" in line and player_name:
                question = line.split("!web")[-1].strip()
                threading.Thread(target=handle_agentic_loop, args=(question, player_name, True), daemon=True).start()
        except Exception as e:
            console.print(f"[bold red][-] Error in log watcher: {e}[/bold red]")
            time.sleep(1)

def show_dashboard():
    cpu = 0.0
    ram = 0.0
    uptime = "N/A"
    
    if server_process and server_status in ["STARTING", "RUNNING", "STOPPING"]:
        try:
            p = psutil.Process(server_process.pid)
            cpu = p.cpu_percent(interval=0.1)
            ram = p.memory_info().rss / (1024 ** 3)
        except Exception:
            pass
            
    if server_uptime_start and server_status in ["STARTING", "RUNNING"]:
        elapsed = int(time.time() - server_uptime_start)
        h = elapsed // 3600
        m = (elapsed % 3600) // 60
        s = elapsed % 60
        uptime = f"{h:02d}:{m:02d}:{s:02d}"
        
    table = Table(box=box.DOUBLE, title="System Telemetry & Status", border_style="bright_blue")
    table.add_column("Property", style="bold cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Server Status", f"[bold]{server_status}[/bold]")
    table.add_row("Active Port Listener", "25565 (Idler Active)" if server_status == "SLEEPING" else "None (Java Active)")
    table.add_row("Java CPU Usage", f"{cpu:.1f}%")
    table.add_row("Java RAM Allocation", f"{ram:.2f} GB / 8.00 GB")
    table.add_row("Server Uptime", uptime)
    table.add_row("Players Online", f"{player_count} ({', '.join(online_players_list) if online_players_list else 'None'})")
    
    console.print(table)

def show_help():
    table = Table(box=box.ROUNDED, title="Available Shell Commands", border_style="yellow")
    table.add_column("Command", style="bold cyan")
    table.add_column("Description", style="white")
    
    table.add_row("/start", "Manually wake up and start the Minecraft server.")
    table.add_row("/stop", "Safely stop the server via RCON.")
    table.add_row("/sleep", "Stop the server and enable auto-wake listening mode.")
    table.add_row("/status", "Display the system telemetry and performance dashboard.")
    table.add_row("/history <player>", "View the AI's conversation history with a specific player.")
    table.add_row("/help", "Show this help table.")
    table.add_row("/exit", "Shutdown all background threads and exit AgentRCON.")
    table.add_row("Any raw text", "Passes the command directly to the Minecraft RCON console.")
    
    console.print(table)

def cli_input_loop():
    time.sleep(1)
    show_dashboard()
    show_help()
    
    while True:
        try:
            cmd = input("\nAgentRCON> ").strip()
            if not cmd:
                continue
                
            if cmd == "/start":
                if server_status == "SLEEPING":
                    start_server()
                else:
                    console.print("[bold red][-] Server is not sleeping.[/bold red]")
            elif cmd == "/stop":
                stop_server()
            elif cmd == "/sleep":
                stop_server()
            elif cmd == "/status":
                show_dashboard()
            elif cmd == "/help":
                show_help()
            elif cmd.startswith("/history"):
                parts = cmd.split(" ")
                if len(parts) > 1:
                    pname = parts[1]
                    if pname in player_sessions:
                        console.print(Panel(json.dumps(player_sessions[pname], indent=2), title=f"Memory for {pname}", border_style="cyan"))
                    else:
                        console.print(f"[bold red][-] No history found for player '{pname}'.[/bold red]")
                else:
                    console.print("[bold red][-] Usage: /history <player_name>[/bold red]")
            elif cmd == "/exit":
                console.print("[bold red][*] Shutting down background tasks...[/bold red]")
                if server_status in ["STARTING", "RUNNING"]:
                    stop_server()
                os._exit(0)
            else:
                # Pass directly to RCON
                if server_status == "RUNNING":
                    resp = run_rcon_command(cmd)
                    console.print(Panel(resp, title="RCON Response", border_style="magenta"))
                else:
                    console.print("[bold red][-] Command ignored: Server is not running.[/bold red]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[bold red][*] Exiting...[/bold red]")
            os._exit(0)

class AgentRCONAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress request logging to avoid cluttering stdout
        pass
        
    def _send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/status":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            
            cpu = 0.0
            ram = 0.0
            uptime = "N/A"
            if server_process and server_status in ["STARTING", "RUNNING", "STOPPING"]:
                try:
                    p = psutil.Process(server_process.pid)
                    cpu = p.cpu_percent(interval=None)
                    ram = p.memory_info().rss / (1024 ** 3)
                except Exception:
                    pass
            if server_uptime_start and server_status in ["STARTING", "RUNNING"]:
                elapsed = int(time.time() - server_uptime_start)
                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                s = elapsed % 60
                uptime = f"{h:02d}:{m:02d}:{s:02d}"
                
            model_status = "idle"
            if model_loaded_and_ready:
                model_status = "ready"
            elif model_loading_in_progress:
                model_status = "loading"
                
            response_data = {
                "status": server_status,
                "cpu": round(cpu, 1),
                "ram": round(ram, 2),
                "uptime": uptime,
                "player_count": player_count,
                "online_players": online_players_list,
                "model_status": model_status
            }
            self.wfile.write(json.dumps(response_data).encode("utf-8"))
            
        elif self.path == "/api/history":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(player_sessions).encode("utf-8"))
            
        elif self.path == "/api/logs":
            self.send_response(200)
            self._send_cors_headers()
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            log_lines = []
            if os.path.exists(LOG_PATH):
                try:
                    with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        log_lines = [l.strip() for l in lines[-150:]]
                except Exception:
                    pass
            self.wfile.write(json.dumps({"logs": log_lines}).encode("utf-8"))
        else:
            self.send_response(404)
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = {}
        try:
            data = json.loads(post_data.decode("utf-8"))
        except Exception:
            pass
            
        self.send_response(200)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        
        if self.path == "/api/control":
            action = data.get("action", "")
            if action == "start":
                if server_status == "SLEEPING":
                    start_server()
                    resp = {"success": True, "message": "Server waking up..."}
                else:
                    resp = {"success": False, "message": f"Server status is {server_status}"}
            elif action in ["stop", "sleep"]:
                if server_status in ["STARTING", "RUNNING"]:
                    threading.Thread(target=stop_server, daemon=True).start()
                    resp = {"success": True, "message": "Stopping server..."}
                else:
                    resp = {"success": False, "message": "Server is already sleeping."}
            else:
                resp = {"success": False, "message": "Invalid action."}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            
        elif self.path == "/api/command":
            cmd = data.get("command", "")
            if server_status == "RUNNING":
                rcon_resp = run_rcon_command(cmd, "Console")
                resp = {"success": True, "output": rcon_resp}
            else:
                resp = {"success": False, "message": "Server is not running."}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            
        elif self.path == "/api/search":
            query = data.get("query", "")
            search_resp = search_item_by_name(query)
            self.wfile.write(json.dumps({"results": search_resp}).encode("utf-8"))
            
        elif self.path == "/api/warmup":
            if model_loaded_and_ready:
                resp = {"success": True, "status": "ready", "message": "AI model is already loaded."}
            elif model_loading_in_progress:
                resp = {"success": True, "status": "loading", "message": "AI model is currently loading..."}
            else:
                threading.Thread(target=warmup_model, daemon=True).start()
                resp = {"success": True, "status": "starting", "message": "AI model loading initiated."}
            self.wfile.write(json.dumps(resp).encode("utf-8"))
        else:
            self.send_response(404)
            self.wfile.write(b"Not Found")

def run_api_server(httpd):
    console.print("[bold green][+] AgentRCON HTTP API listening on http://127.0.0.1:8000[/bold green]")
    httpd.serve_forever()

if __name__ == "__main__":
    console.print(Panel.fit(
        "AgentRCON v3.0 - Autonomous Manager & API Daemon\nMinecraft 1.20.1 Server Control System", 
        border_style="bold green", 
        box=box.DOUBLE
    ))
    
    # Load memory history
    load_memories()
    
    # Check if server is already running
    check_if_server_already_running()
    
    active_model = get_loaded_model()
    if active_model:
        console.print(f"[bold green][+] Connected to Ollama! Active Model: '{active_model}'[/bold green]")
    else:
        console.print(f"[bold red][-] WARNING: Ollama connection failed. Run 'ollama pull {OLLAMA_MODEL}'[/bold red]")

    # Instantiate REST API server (fails early if port is blocked)
    try:
        server_address = ('127.0.0.1', 8000)
        httpd = HTTPServer(server_address, AgentRCONAPIHandler)
    except OSError as e:
        console.print(f"[bold red][-] Failed to bind HTTP server to port 8000: {e}[/bold red]")
        console.print("[bold red][-] AgentRCON HTTP API port is already occupied. Exiting process.[/bold red]")
        import os
        os._exit(1)

    # Start background threads
    threading.Thread(target=socket_listener_loop, daemon=True).start()
    threading.Thread(target=stats_monitoring_loop, daemon=True).start()
    threading.Thread(target=watch_logs, daemon=True).start()
    
    # Start REST API server
    threading.Thread(target=run_api_server, args=(httpd,), daemon=True).start()
    
    # Auto warm up model in background on startup
    threading.Thread(target=warmup_model, daemon=True).start()
    
    # Check if we should skip the interactive terminal CLI loop
    if "--no-cli" in sys.argv:
        console.print("[bold cyan][*] Running in headless daemon mode...[/bold cyan]")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            console.print("[bold red][*] Exiting...[/bold red]")
            os._exit(0)
    else:
        # Run CLI prompt in the main thread
        cli_input_loop()
