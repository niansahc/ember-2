"""
src/memory/vault_storage.py

Vault storage analysis — size breakdown, growth rate, and projection.

Walks the vault directory tree, sums file sizes by memory type,
computes daily growth rate from file timestamps, and projects
30-day storage needs.
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path


def format_bytes(size_bytes: int) -> str:
    """Convert bytes to human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def analyze_vault(vault_path: Path) -> dict:
    """Analyze vault storage usage.

    Returns:
        Dict with current_bytes, current_human, by_type breakdown,
        growth_rate_bytes_per_day, projected_30d_bytes,
        projected_30d_human, and sampled_days.
    """
    vault = Path(vault_path)
    if not vault.exists():
        return {
            "current_bytes": 0,
            "current_human": "0 B",
            "by_type": {},
            "growth_rate_bytes_per_day": 0,
            "projected_30d_bytes": 0,
            "projected_30d_human": "0 B",
            "sampled_days": 0,
        }

    # Walk vault and sum sizes by memory type subdirectory
    total_bytes = 0
    by_type: dict[str, int] = defaultdict(int)
    daily_bytes: dict[str, int] = defaultdict(int)  # date_str -> bytes written that day

    memory_dir = vault / "memory"
    if memory_dir.exists():
        for root, dirs, files in os.walk(memory_dir):
            root_path = Path(root)
            # Determine memory type from first subdirectory under memory/
            relative = root_path.relative_to(memory_dir)
            parts = relative.parts
            memory_type = parts[0] if parts else "other"

            for f in files:
                file_path = root_path / f
                try:
                    stat = file_path.stat()
                    size = stat.st_size
                    total_bytes += size
                    by_type[memory_type] += size

                    # Track daily write volume by mtime
                    mtime = datetime.fromtimestamp(stat.st_mtime)
                    day_key = mtime.strftime("%Y-%m-%d")
                    daily_bytes[day_key] += size
                except OSError:
                    continue

    # Also count embeddings directory
    embeddings_dir = vault / "embeddings"
    if embeddings_dir.exists():
        for root, dirs, files in os.walk(embeddings_dir):
            for f in files:
                file_path = Path(root) / f
                try:
                    size = file_path.stat().st_size
                    total_bytes += size
                    by_type["embeddings"] += size
                except OSError:
                    continue

    # Compute growth rate
    sampled_days = len(daily_bytes)
    if sampled_days >= 2:
        sorted_days = sorted(daily_bytes.keys())
        first_day = datetime.strptime(sorted_days[0], "%Y-%m-%d")
        last_day = datetime.strptime(sorted_days[-1], "%Y-%m-%d")
        span_days = max((last_day - first_day).days, 1)
        total_written = sum(daily_bytes.values())
        growth_rate = total_written / span_days
    elif sampled_days == 1:
        growth_rate = sum(daily_bytes.values())
    else:
        growth_rate = 0

    growth_rate_int = int(growth_rate)
    projected_30d = total_bytes + (growth_rate_int * 30)

    # Build by_type breakdown
    by_type_result = {}
    for mem_type, size in sorted(by_type.items()):
        by_type_result[mem_type] = {
            "bytes": size,
            "human": format_bytes(size),
        }

    return {
        "current_bytes": total_bytes,
        "current_human": format_bytes(total_bytes),
        "by_type": by_type_result,
        "growth_rate_bytes_per_day": growth_rate_int,
        "projected_30d_bytes": projected_30d,
        "projected_30d_human": format_bytes(projected_30d),
        "sampled_days": sampled_days,
    }
