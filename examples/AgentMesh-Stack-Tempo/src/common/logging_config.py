"""Structured logging setup for AOIN agents."""

import logging
import sys


def setup_logging(agent_name: str, level: str = "INFO") -> logging.Logger:
    """Configure structured logging for an agent."""
    logger = logging.getLogger(f"aoin.{agent_name}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            f"%(asctime)s [{agent_name.upper()}] %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(handler)

    # Suppress noisy libp2p logs
    logging.getLogger("libp2p").setLevel(logging.WARNING)
    logging.getLogger("multiaddr").setLevel(logging.WARNING)

    return logger
