import os
import sys
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
        server_dir = sys.argv[i+1]
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

def web_search(query):
    console.print(f"[bold grey53][*] Searching the web for: '{query}'...[/bold grey53]")
    try:
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as response:
            html = response.read().decode("utf-8", errors="ignore")
            
        results = []
        snippets = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL)
        
        def clean_html(text):
            text = re.sub(r'<[^>]+>', '', text)
            text = text.replace("&amp;", "&").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">").replace("&#x27;", "'")
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

4. `web_search`: Queries the web for item IDs, recipes, or information.
   Arguments: {{"query": "search term"}}
   Example: <CALL name="web_search">{{"query": "minecraft 1.20.1 raw iron block ID"}}</CALL>

IMPORTANT RULES:
- When targeting the player, you MUST use their exact username '{player_name}' in console commands instead of selectors like '@p' or '@s'.
- Minecraft 1.20.1 uses curly brace NBT syntax (e.g. `minecraft:diamond_sword{{display:{{Name:'{{"text":"Legendary Sword"}}'}}}}`). Square brackets `[]` are 1.21+ components and will CRASH the server.
- NEVER put spaces between relative coordinate tildes ('~') and their values (e.g. write '~-4' or '~2', NOT '~ -4' or '~ 2').
- Console executes from server center (no position). You MUST prefix all coordinate-dependent commands (like setblock, fill, summon) with `execute at {player_name} run ...` so they execute at the player's location.
- NEVER run administrative/destructive commands: stop, op, deop, ban, ban-ip, kick, whitelist.
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
                extra_body={"options": {"num_ctx": 8192}}
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

def stats_monitoring_loop():
    global server_status, empty_start_time, player_count, online_players_list, server_process
    while True:
        time.sleep(8)
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
                pass
        elif server_status == "STARTING":
            # Check if subprocess died unexpectedly
            if server_process and server_process.poll() is not None:
                console.print("[bold red][-] Server process died during startup![/bold red]")
                server_process = None
                server_status = "SLEEPING"

def watch_logs():
    if not os.path.exists(LOG_PATH):
        while not os.path.exists(LOG_PATH):
            time.sleep(2)

    with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                f.seek(f.tell())
                continue
            
            # Print parsed chat lines nicely to console
            player_name = extract_player_name(line)
            if player_name:
                message = line.split(f"<{player_name}>")[-1].strip()
                console.print(f"[cyan]<{player_name}>[/cyan] {message}")
                
            # If server indicates startup completion
            if "Done (" in line and "s)! For help" in line:
                global server_status
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
                
            response_data = {
                "status": server_status,
                "cpu": round(cpu, 1),
                "ram": round(ram, 2),
                "uptime": uptime,
                "player_count": player_count,
                "online_players": online_players_list
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
        else:
            self.send_response(404)
            self.wfile.write(b"Not Found")

def run_api_server():
    server_address = ('127.0.0.1', 8000)
    httpd = HTTPServer(server_address, AgentRCONAPIHandler)
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
    
    active_model = get_loaded_model()
    if active_model:
        console.print(f"[bold green][+] Connected to Ollama! Active Model: '{active_model}'[/bold green]")
    else:
        console.print(f"[bold red][-] WARNING: Ollama connection failed. Run 'ollama pull {OLLAMA_MODEL}'[/bold red]")

    # Start background threads
    threading.Thread(target=socket_listener_loop, daemon=True).start()
    threading.Thread(target=stats_monitoring_loop, daemon=True).start()
    threading.Thread(target=watch_logs, daemon=True).start()
    
    # Start REST API server
    threading.Thread(target=run_api_server, daemon=True).start()
    
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
