import os
import subprocess
import sys
import time
from pathlib import Path

from agents.core.dream_state import run_dream_cycle

# Note: run_agent_main is now invoked as a subprocess to capture logs

# Configuration
CHECK_IN_INTERVAL_SEC = 3600  # Sync with GitHub every hour
DREAM_CYCLE_HOUR_UTC = 3  # Run maintenance at 3 AM UTC (adjust as needed)
LOG_PATH = Path("logs/local_node.log")


def log_local(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{timestamp}] [LOCAL_NODE] {msg}"
    print(formatted_msg)

    # Ensure logs directory exists
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(formatted_msg + "\n")


def check_in():
    """Sync local state and logs with GitHub."""
    log_local("Starting Remote Sync (Git Check-in)...")
    try:
        # 1. Add tracked state and log files
        subprocess.run(["git", "add", "logs/", "data/state/"], capture_output=True)

        # 2. Check if there are changes
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            log_local("No changes to sync.")
            return

        # 3. Commit and Push
        commit_msg = f"Autonomous Node Check-in: {time.strftime('%Y-%m-%d %H:%M:%S')}"
        subprocess.run(["git", "commit", "-m", commit_msg], capture_output=True)
        result = subprocess.run(["git", "push", "origin", "main"], capture_output=True, text=True)

        if result.returncode == 0:
            log_local("Successfully synced with GitHub.")
        else:
            log_local(f"Sync failed: {result.stderr}")
    except Exception as e:
        log_local(f"Error during check-in: {e}")


def run_agent_process():
    """Start the agent executor in a subprocess and redirect output to logs."""
    log_local("Starting Agent Executor Subprocess...")
    log_file = open("logs/agent_executor.log", "a", buffering=1)  # noqa: SIM115
    # Run the agent module in a subprocess
    # We use 'uv run python -m agents.core.main' to ensure correct environment
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "agents.core.main"], stdout=log_file, stderr=log_file, env=os.environ, text=True
        )
        log_local(f"Agent Executor Subprocess PID: {process.pid}")
        return process
    except Exception as e:
        log_local(f"FAILED to start Agent Executor: {e}")
        return None


def autonomous_loop():
    """Main loop for local autonomy."""
    log_local("Autonomous Loop Started.")
    last_sync = time.time()
    last_dream = 0

    while True:
        now = time.gmtime()

        # 1. Check if it's time for the Dream Cycle (once per day)
        if now.tm_hour == DREAM_CYCLE_HOUR_UTC and (time.time() - last_dream) > 80000:
            log_local("Triggering Daily Dream Cycle...")
            run_dream_cycle(manual=False)
            last_dream = time.time()
            check_in()  # Sync immediately after dream

        # 2. Periodic Check-in
        if (time.time() - last_sync) > CHECK_IN_INTERVAL_SEC:
            check_in()
            last_sync = time.time()

        time.sleep(60)


if __name__ == "__main__":
    log_local("Sovereign OS Local Autonomous Node Initializing...")

    # Ensure local directories exist
    os.makedirs("logs", exist_ok=True)
    os.makedirs("data/state", exist_ok=True)

    # Set local environment defaults
    os.environ.setdefault("DEPLOYMENT_TIER", "forge")

    # Robust Redis URL construction
    redis_pw = os.environ.get("REDIS_PASSWORD")
    if redis_pw and "REDIS_URL" not in os.environ:
        os.environ["REDIS_URL"] = f"redis://:{redis_pw}@localhost:6379/0"
    else:
        os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

    os.environ.setdefault("AUTO_RUN", "true")

    # Start Agent Executor in a separate process
    agent_proc = run_agent_process()

    # Start Autonomous Orchestrator (Main Thread)
    try:
        log_local("Sovereign OS Local Autonomous Node is LIVE.")
        autonomous_loop()
    except KeyboardInterrupt:
        log_local("Shutting down Local Node...")
        if agent_proc:
            agent_proc.terminate()
