"""
run_all.py
----------
Supervisor script that launches both watchdog processes (GIE and IPEX)
as child processes and monitors their health.

If either child exits unexpectedly, the supervisor terminates the other
and exits with a non-zero status code so the process manager (e.g. Heroku,
systemd, Docker) can restart the whole suite automatically.

Usage:
    python run_all.py
"""

import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WATCHDOGS = [
    "gie_rss_watchdog.py",
    "ipex_watchdog.py",
]

HEALTH_CHECK_INTERVAL = 5  # seconds between liveness polls

# ---------------------------------------------------------------------------
# Supervisor loop
# ---------------------------------------------------------------------------


def main() -> None:
    print("Starting watchdog suite…")

    processes: list[subprocess.Popen] = []
    for script in WATCHDOGS:
        proc = subprocess.Popen([sys.executable, script])
        processes.append(proc)
        print(f"  ✔  {script} started (PID {proc.pid})")

    print("All watchdogs are running. Press Ctrl+C to stop.\n")

    try:
        while True:
            for proc, script in zip(processes, WATCHDOGS):
                if proc.poll() is not None:
                    # One child has exited — terminate all siblings cleanly.
                    print(
                        f"\n[SUPERVISOR] {script} exited with code "
                        f"{proc.returncode}. Shutting down all watchdogs…"
                    )
                    for sibling in processes:
                        if sibling.poll() is None:
                            sibling.terminate()
                    sys.exit(1)

            time.sleep(HEALTH_CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n[SUPERVISOR] Interrupt received. Stopping all watchdogs…")
        for proc in processes:
            proc.terminate()
        print("[SUPERVISOR] Shutdown complete.")


if __name__ == "__main__":
    main()
