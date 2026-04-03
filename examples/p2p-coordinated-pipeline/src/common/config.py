"""
AgentMesh Full-Stack — Configuration loader

Reads config.toml from the current working directory using Python 3.11+
stdlib tomllib.  Falls back to hardcoded defaults when config.toml is
absent, so the demo works out-of-the-box with no configuration file.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WorkerEntry:
    name: str
    port: int
    capability: str  # matches WorkerCapability.value
    cost: float
    quality: str


@dataclass
class Config:
    # [network]
    listen_ip: str = "127.0.0.1"

    # [ports]
    bootstrap_port: int = 9000
    coordinator_port: int = 9004

    # [timeouts] — all in seconds
    bootstrap_ready_delay: float = 2.0
    coordinator_ready_delay: float = 6.0
    discovery_window: float = 8.0
    negotiate_timeout: float = 10.0
    execute_timeout: float = 30.0
    reannounce_interval: float = 5.0
    mesh_broadcast_interval: float = 15.0

    # DHT re-provide interval (seconds; 12 h default)
    dht_reprovide_interval: float = 43200.0

    # [coordinator]
    default_budget: float = 0.10
    execute_retry_attempts: int = 2
    negotiate_retry_attempts: int = 3

    # [[workers]]
    workers: list[WorkerEntry] = field(default_factory=lambda: [
        WorkerEntry("DataValidator",   9001, "data_validation",   0.01,  "standard"),
        WorkerEntry("AnalyticsEngine", 9002, "analytics",         0.02,  "standard"),
        WorkerEntry("ReportWriter",    9003, "report_generation", 0.015, "premium"),
    ])


def load_config(search_dir: Optional[Path] = None) -> Config:
    """
    Load config.toml from search_dir (default: cwd).
    Returns Config with all defaults if config.toml is not found.
    """
    base = search_dir or Path.cwd()
    config_path = base / "config.toml"

    if not config_path.exists():
        return Config()

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    cfg = Config()

    net = data.get("network", {})
    cfg.listen_ip = net.get("listen_ip", cfg.listen_ip)

    ports = data.get("ports", {})
    cfg.bootstrap_port  = ports.get("bootstrap",   cfg.bootstrap_port)
    cfg.coordinator_port = ports.get("coordinator", cfg.coordinator_port)

    t = data.get("timeouts", {})
    cfg.bootstrap_ready_delay   = t.get("bootstrap_ready_delay",   cfg.bootstrap_ready_delay)
    cfg.coordinator_ready_delay = t.get("coordinator_ready_delay", cfg.coordinator_ready_delay)
    cfg.discovery_window        = t.get("discovery_window",        cfg.discovery_window)
    cfg.negotiate_timeout       = t.get("negotiate_timeout",       cfg.negotiate_timeout)
    cfg.execute_timeout         = t.get("execute_timeout",         cfg.execute_timeout)
    cfg.reannounce_interval     = t.get("reannounce_interval",     cfg.reannounce_interval)
    cfg.mesh_broadcast_interval = t.get("mesh_broadcast_interval", cfg.mesh_broadcast_interval)
    cfg.dht_reprovide_interval  = t.get("dht_reprovide_interval",  cfg.dht_reprovide_interval)

    coord = data.get("coordinator", {})
    cfg.default_budget           = coord.get("default_budget",           cfg.default_budget)
    cfg.execute_retry_attempts   = coord.get("execute_retry_attempts",   cfg.execute_retry_attempts)
    cfg.negotiate_retry_attempts = coord.get("negotiate_retry_attempts", cfg.negotiate_retry_attempts)

    raw_workers = data.get("workers", [])
    if raw_workers:
        cfg.workers = [
            WorkerEntry(
                name=w["name"],
                port=w["port"],
                capability=w["capability"],
                cost=float(w["cost"]),
                quality=w.get("quality", "standard"),
            )
            for w in raw_workers
        ]

    return cfg
