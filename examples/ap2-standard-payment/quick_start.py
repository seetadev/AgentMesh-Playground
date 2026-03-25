"""Cross-platform launcher for the AP2 Standard Payment Demo.

Starts all 5 agents and runs the complete payment flow.
Usage: python quick_start.py [--verbose]
"""
import argparse
import asyncio
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = str(Path(__file__).parent)
sys.path.insert(0, PROJECT_ROOT)


async def wait_for_server(port: int, timeout: float = 10.0):
    """Wait for a server to start accepting connections."""
    import httpx
    start = time.time()
    while time.time() - start < timeout:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                r = await client.get(f"http://localhost:{port}/.well-known/agent.json")
                if r.status_code == 200:
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


async def main(verbose: bool = False):
    procs = []
    log_level = "info" if verbose else "warning"

    try:
        print("=" * 50)
        print("  AP2 Standard Payment Demo - Quick Start")
        print("=" * 50)
        print()

        # Phase 1: Start backend agents
        print("[BOOT] Starting Payment Processor on port 8004...")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.payment_processor", "--port", "8004"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE if not verbose else None,
            stderr=subprocess.PIPE if not verbose else None,
        ))

        print("[BOOT] Starting Credentials Provider on port 8003...")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.credentials_provider", "--port", "8003"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE if not verbose else None,
            stderr=subprocess.PIPE if not verbose else None,
        ))

        # Wait for backend agents
        pp_ready = await wait_for_server(8004)
        cp_ready = await wait_for_server(8003)
        if not pp_ready or not cp_ready:
            print("[ERROR] Backend agents failed to start. Check ports 8003/8004.")
            return

        # Phase 2: Start merchants
        print("[BOOT] Starting Merchant A (QuickShoot Studios) on port 8001...")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.merchant_agent",
             "--port", "8001", "--name", "QuickShoot Studios",
             "--price", "350", "--processor", "http://localhost:8004"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE if not verbose else None,
            stderr=subprocess.PIPE if not verbose else None,
        ))

        print("[BOOT] Starting Merchant B (Premium Films) on port 8002...")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "agents.merchant_agent",
             "--port", "8002", "--name", "Premium Films",
             "--price", "450", "--processor", "http://localhost:8004"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE if not verbose else None,
            stderr=subprocess.PIPE if not verbose else None,
        ))

        ma_ready = await wait_for_server(8001)
        mb_ready = await wait_for_server(8002)
        if not ma_ready or not mb_ready:
            print("[ERROR] Merchant agents failed to start. Check ports 8001/8002.")
            return

        print("[BOOT] All agents running.")
        print()

        # Phase 3: Run Shopping Agent flow
        from agents.shopping_agent import run_shopping_flow

        await run_shopping_flow(
            merchant_urls=["http://localhost:8001", "http://localhost:8002"],
            credentials_provider_url="http://localhost:8003",
            budget=400.0,
            user_email="johndoe@example.com",
        )

    except KeyboardInterrupt:
        print("\n[SYSTEM] Interrupted by user.")
    finally:
        print("\n[SYSTEM] Shutting down all agents...")
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("[SYSTEM] All agents stopped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AP2 Standard Payment Demo Launcher")
    parser.add_argument("--verbose", action="store_true", help="Show full agent logs")
    args = parser.parse_args()
    asyncio.run(main(verbose=args.verbose))
