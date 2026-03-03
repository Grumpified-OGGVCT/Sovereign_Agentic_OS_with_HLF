import os
import subprocess
import threading
import webbrowser

import pystray
from PIL import Image, ImageDraw
from pystray import MenuItem as item

# OS Level states
backend_process = None
gui_process = None
mcp_process = None
backend_running = False
gui_running = False
mcp_running = False


# Setup Icon Graphic
def create_image(width, height, color1, color2):
    image = Image.new("RGB", (width, height), color1)
    dc = ImageDraw.Draw(image)
    dc.rectangle((width // 2, 0, width, height // 2), fill=color2)
    dc.rectangle((0, height // 2, width // 2, height), fill=color2)
    return image


icon_img = create_image(64, 64, "black", "blue")


# --- Actions ---


def start_backend(icon, item):
    global backend_process, backend_running
    if not backend_running:
        print("[System Tray] Starting Sovereign OS Backends via Docker Compose...")
        # Profile hearth by default
        env = os.environ.copy()
        env["DEPLOYMENT_TIER"] = "hearth"
        backend_process = subprocess.Popen(["docker", "compose", "--profile", "hearth", "up", "-d"], env=env)
        backend_running = True
        icon.notify("Sovereign OS backends started (Hearth Profiling).")


def stop_backend(icon, item):
    global backend_process, backend_running
    if backend_running:
        print("[System Tray] Stopping Sovereign OS Backends...")
        subprocess.Popen(["docker", "compose", "down"])
        backend_running = False
        icon.notify("Sovereign OS backends shutting down.")


def start_gui(icon, item):
    global gui_process, gui_running
    if not gui_running:
        icon.notify("Starting Sovereign Command Center UI...")
        print("[System Tray] Starting Streamlit GUI...")
        gui_process = subprocess.Popen(["uv", "run", "streamlit", "run", "gui/app.py", "--server.headless", "true"])
        gui_running = True

        def open_browser():
            import time

            time.sleep(3)  # Wait for Streamlit server to boot
            webbrowser.open("http://localhost:8501")

        threading.Thread(target=open_browser, daemon=True).start()


def stop_gui(icon, item):
    global gui_process, gui_running
    if gui_running and gui_process:
        print("[System Tray] Stopping Streamlit GUI...")
        gui_process.terminate()
        gui_running = False
        icon.notify("Command Center UI stopped.")


def exit_action(icon, item):
    stop_gui(icon, item)
    stop_mcp(icon, item)
    stop_backend(icon, item)
    icon.stop()


def start_mcp(icon, item):
    global mcp_process, mcp_running
    if not mcp_running:
        print("[System Tray] Starting Sovereign OS MCP Server...")
        mcp_process = subprocess.Popen(["uv", "run", "python", "mcp/sovereign_mcp_server.py"])
        mcp_running = True
        if icon:
            icon.notify("Sovereign OS MCP Server started.")


def stop_mcp(icon, item):
    global mcp_process, mcp_running
    if mcp_running and mcp_process:
        print("[System Tray] Stopping MCP Server...")
        mcp_process.terminate()
        mcp_running = False
        if icon:
            icon.notify("MCP Server stopped.")


# --- Menu Definition ---

menu = pystray.Menu(
    item("Start Command Center GUI \u25b6", start_gui),
    item("Stop Command Center GUI \u23f9", stop_gui),
    pystray.Menu.SEPARATOR,
    item("Start OS Backend \u2699", start_backend),
    item("Stop OS Backend \u26d4", stop_backend),
    pystray.Menu.SEPARATOR,
    item("Start MCP Server \U0001f517", start_mcp),
    item("Stop MCP Server \u23f9", stop_mcp),
    pystray.Menu.SEPARATOR,
    item("Exit Manager \u274c", exit_action),
)

icon = pystray.Icon("SovereignOS", icon_img, "Sovereign Agentic OS", menu)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-launch", action="store_true", help="Automatically start backend and GUI on boot")
    args = parser.parse_args()

    # Pre-start if requested by run.bat
    if args.auto_launch:
        print("[System Tray] Auto-launching OS Services...")
        start_backend(icon, None)
        start_gui(icon, None)
        start_mcp(icon, None)

    print("Sovereign OS Tray Manager loading. Check your system tray (bottom right).")
    icon.run()
