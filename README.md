# AgentRCON v4.0

AgentRCON is a standalone desktop application that provides a modern, state-of-the-art **Graphical Dashboard** and **Autonomous AI Manager** for modded Minecraft 1.20.1 Fabric servers. 

It is designed to run in any directory. When launched, you can point it to any Minecraft server folder using a native directory picker dialog. The application will dynamically read your server configurations, start/stop the server, monitor stdout logs, scan mod jars for translations, and run a player-specific conversational ReAct AI loop.

---

## 🚀 Key Features

*   **Sleek Glassmorphism Dashboard:** Displays Java CPU/RAM performance telemetry, server statuses (`SLEEPING`, `STARTING`, `RUNNING`, `STOPPING`), active online players, and controls.
*   **Minecraft Server Idler Integration:** Automatically shuts down the Minecraft server when empty for more than 5 minutes and runs a lightweight TCP socket listener on port `25565`. When a player connects, it launches the Java subprocess, seamlessly transferring control.
*   **Interactive Live Console:** Real-time monospaced logging with coloring depending on message level (INFO, WARNING, ERROR, chat logs, and agent logs) and an input command injection prompt.
*   **Conversational ReAct Loop:** Parses player chat in-game (using a local Ollama model) to run server commands, self-correct syntax errors, place coordinate-dependent blocks (using execute context auto-wrapping), and chat with players.
*   **Player Memories Browser:** Track player conversations and trace AI reasoning steps (thought blocks, tool execution records) via a two-column session logs viewer.
*   **Mod Jar Localization Scanner:** Programmatic zip translation parser to map display names to mod namespaces IDs (e.g. `farmersdelight:bacon_and_eggs`) with clipboard copying and direct command injection.

---

## 🛠️ Prerequisites

To run AgentRCON, ensure you have:
1.  **Node.js** (v16+) installed.
2.  **Python 3.x** installed with dependencies: `pip install mcrcon openai psutil rich`.
3.  **Ollama** running locally on `http://localhost:11434` with model `gemma4:e2b-it-qat` pulled (`ollama pull gemma4:e2b-it-qat`).

---

## ⚡ Quick Start

1.  Clone this repository or download the source code:
    ```bash
    git clone https://github.com/ColbyJacks/AgentRCON.git
    ```
2.  Run **`start_dashboard.bat`** (or execute `npm start` inside the `dashboard/` directory).
3.  Select your Minecraft server directory inside the setup welcome screen folder picker.
4.  Launch Minecraft and connect to `localhost`.
