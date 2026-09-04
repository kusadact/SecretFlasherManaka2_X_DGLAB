import asyncio
import configparser
import json
import logging
import math
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import ttk

if getattr(sys, "frozen", False):
    # PyInstaller one-file builds should keep config, logs, QR codes and
    # waveforms next to the EXE instead of inside the temporary extraction
    # directory.
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent
    VENDOR = ROOT / "vendor"
    if VENDOR.is_dir() and str(VENDOR) not in sys.path:
        sys.path.insert(0, str(VENDOR))

import qrcode  # noqa: E402  # vendor path must be configured first
from PIL import Image, ImageTk  # noqa: E402
from pydglab_ws import (  # noqa: E402
    Channel,
    DGLabWSServer,
    FeedbackButton,
    RetCode,
    StrengthData,
    StrengthOperationType,
)


DEFAULT_CONFIG = """[network]
ws_port = 5678
udp_port = 39090
public_host = auto

[safety]
dry_run = true
output_enabled = false
max_coyote_strength = 20
stale_timeout_seconds = 2.0
send_hz = 5
channel = A
channel_b_multiplier = 1.0

[mapping]
suspicion_deadzone = 0
low_suspicion_threshold = 0
low_suspicion_min_units = 0
suspicion_at_max = 1
output_scale_percent = 100
ramp_units_per_second = 10
vibrator_weak_percent = 30
vibrator_strong_percent = 70
piston_weak_percent = 30
piston_medium_percent = 50
piston_strong_percent = 70
climax_range_percent = 70
climax_active_percent = 90

[waveform]
climax_enabled = true
selected = 官方示例 - 呼吸
vibrator = 官方示例 - 呼吸
piston = 默认 - 活塞机
climax = 默认 - 高潮
"""


DEFAULT_WAVEFORMS = {
    "version": 1,
    "waveforms": [
        {
            "name": "官方示例 - 呼吸",
            "source": "DG-LAB-OPENSOURCE coyote/v3/example.md",
            "frames": [[10, 0], [10, 20], [10, 40], [10, 60], [10, 80], [10, 100],
                       [10, 100], [10, 100], [10, 0], [10, 0], [10, 0], [10, 0]],
        },
        {
            "name": "官方示例 - 潮汐",
            "source": "DG-LAB-OPENSOURCE coyote/v3/example.md",
            "frames": [[10, 0], [11, 16], [13, 33], [14, 50], [16, 66], [18, 83],
                       [19, 100], [21, 92], [22, 84], [24, 76], [26, 68], [26, 0],
                       [27, 16], [29, 33], [30, 50], [32, 66], [34, 83], [35, 100],
                       [37, 92], [38, 84], [40, 76], [42, 68], [10, 0]],
        },
        {
            "name": "平稳低频",
            "source": "Codex built-in",
            "frames": [[20, 20], [20, 30], [20, 40], [20, 30]],
        },
        {
            "name": "默认 - 活塞机",
            "source": "Codex built-in",
            "frames": [
                [42, 42, 42, 42, 100, 70, 20, 80],
                [48, 48, 48, 48, 20, 80, 100, 40],
                [55, 55, 55, 55, 0, 30, 90, 100],
                [48, 48, 48, 48, 70, 20, 0, 60],
            ],
        },
        {
            "name": "默认 - 高潮",
            "source": "Codex built-in",
            "frames": [
                [180, 180, 180, 180, 0, 30, 80, 100],
                [200, 200, 200, 200, 20, 70, 100, 40],
                [220, 220, 220, 220, 70, 100, 30, 0],
                [200, 200, 200, 200, 100, 60, 10, 40],
            ],
        },
    ],
}


# Dungeonlab's .pulse format stores frequency as an index into this period
# table.  The table and section-duration table match the format used by the
# DG-Lab/Dungeonlab waveform editor.  Imported points are converted to the
# client's existing (frequency, strength) representation below.
COYOTE_FREQUENCY_PERIODS_MS = (
    10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25,
    26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41,
    42, 43, 44, 45, 46, 47, 48, 49, 50, 52, 54, 56, 58, 60, 62, 64,
    66, 68, 70, 72, 74, 76, 78, 80, 85, 90, 95, 100, 110, 120, 130,
    140, 150, 160, 170, 180, 190, 200, 233, 266, 300, 333, 366, 400,
    450, 500, 550, 600, 700, 800, 900, 1000,
)
COYOTE_SECTION_SECONDS = (
    0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3,
    1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6,
    2.7, 2.8, 2.9, 3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9,
    4, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 5, 5.2, 5.4,
    5.6, 5.8, 6, 6.2, 6.4, 6.6, 6.8, 7, 7.2, 7.4, 7.6, 7.8, 8,
    8.5, 9, 9.5, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 23.4,
    26.6, 30, 33.4, 36.6, 40, 45, 50, 55, 60, 70, 80, 90, 100, 120,
    140, 160, 180, 200, 250, 300,
)
MAX_IMPORTED_WAVEFORM_POINTS = 3000


def ensure_config() -> Path:
    path = ROOT / "client_config.ini"
    if not path.exists():
        path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return path


def ensure_waveforms() -> Path:
    path = ROOT / "waveforms.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_WAVEFORMS, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ensure_waveform_directory() -> Path:
    path = ROOT / "pulse"
    path.mkdir(parents=True, exist_ok=True)
    return path


def frame_to_pulse(frame):
    values = list(frame)
    if len(values) == 2:
        freq = int(values[0])
        strength = int(values[1])
        freqs = [freq, freq, freq, freq]
        strengths = [strength, strength, strength, strength]
    elif len(values) == 8:
        freqs = [int(v) for v in values[:4]]
        strengths = [int(v) for v in values[4:]]
    else:
        raise ValueError("每行需要 2 个数字或 8 个数字")
    for freq in freqs:
        if freq < 10 or freq > 240:
            raise ValueError("波形频率必须在 10 到 240 之间")
    for strength in strengths:
        if strength < 0 or strength > 100:
            raise ValueError("波形强度必须在 0 到 100 之间")
    return (tuple(freqs), tuple(strengths))


def parse_waveform_text(text: str):
    pulses = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if len(line) == 16 and all(ch in "0123456789abcdefABCDEF" for ch in line):
            values = list(bytes.fromhex(line))
        else:
            normalized = line.replace(";", ",").replace("，", ",").replace(" ", ",")
            values = [part for part in normalized.split(",") if part]
            try:
                values = [int(part) for part in values]
            except ValueError as exc:
                raise ValueError(f"第 {line_no} 行不是数字") from exc
        try:
            pulses.append(frame_to_pulse(values))
        except ValueError as exc:
            raise ValueError(f"第 {line_no} 行：{exc}") from exc
    if not pulses:
        raise ValueError("波形不能为空")
    return pulses


def _coyote_frequency_from_index(index, speed_rate):
    safe_index = max(
        0,
        min(len(COYOTE_FREQUENCY_PERIODS_MS) - 1, math.floor(float(index))),
    )
    period_ms = COYOTE_FREQUENCY_PERIODS_MS[safe_index]
    frequency = 1000.0 / (period_ms / max(0.01, float(speed_rate)))
    # The client/API uses 10 as its minimum accepted frequency.  Very slow
    # Dungeonlab points therefore become a 10Hz silent/low-frequency point.
    return int(clamp(round(frequency), 10, 240))


def parse_dungeonlab_pulse(text: str):
    """Convert Dungeonlab's ``Dungeonlab+pulse`` text into client pulses."""
    marker = "Dungeonlab+pulse:"
    normalized = text.lstrip("\ufeff").strip()
    if not normalized.startswith(marker):
        raise ValueError("不是 Dungeonlab+pulse 波形")

    sections = normalized[len(marker):].split("+section+")
    if not sections:
        raise ValueError("Dungeonlab PULSE 没有波形小节")

    metadata, first_section = (sections[0].split("=", 1) if "=" in sections[0] else ("0,1", sections[0]))
    metadata_values = metadata.split(",")
    try:
        rest_duration_ms = max(0.0, float(metadata_values[0]))
        speed_rate = max(0.01, float(metadata_values[1]))
    except (IndexError, ValueError) as exc:
        raise ValueError("Dungeonlab PULSE 元数据无效") from exc
    sections[0] = first_section

    pulses = [((10, 10, 10, 10), (0, 0, 0, 0)) for _ in range(math.ceil(rest_duration_ms / 100.0))]
    for section in sections:
        if "/" not in section:
            continue
        header, pulse_data = section.split("/", 1)
        try:
            header_values = [int(value.strip()) for value in header.split(",")[:5]]
            min_index, max_index, duration_index, mode, enabled = header_values
        except (ValueError, TypeError) as exc:
            raise ValueError("Dungeonlab PULSE 小节参数无效") from exc
        if enabled != 1:
            continue
        if not 0 <= duration_index < len(COYOTE_SECTION_SECONDS):
            raise ValueError("Dungeonlab PULSE 持续时间参数无效")

        pulse_values = []
        for raw_point in pulse_data.split(","):
            if not raw_point.strip():
                continue
            try:
                # Dungeonlab stores the pulse width before '-' and a point
                # marker after it.  The marker does not change output strength.
                pulse_values.append(float(raw_point.split("-", 1)[0]))
            except ValueError as exc:
                raise ValueError("Dungeonlab PULSE 波形点无效") from exc
        if not pulse_values:
            continue

        section_seconds = COYOTE_SECTION_SECONDS[duration_index]
        repeat_count = max(1, math.ceil(section_seconds / (len(pulse_values) * 0.1)))
        total_points = repeat_count * len(pulse_values)
        for repeat_index in range(repeat_count):
            for pulse_index, pulse_value in enumerate(pulse_values):
                current = repeat_index * len(pulse_values) + pulse_index
                if mode == 2:
                    frequency_index = min_index + ((max_index - min_index) * current / total_points)
                elif mode == 3:
                    frequency_index = min_index + ((max_index - min_index) * pulse_index / len(pulse_values))
                elif mode == 4:
                    frequency_index = min_index + ((max_index - min_index) * repeat_index / repeat_count)
                else:
                    frequency_index = min_index
                frequency = _coyote_frequency_from_index(frequency_index, speed_rate)
                strength = int(clamp(round(pulse_value), 0, 100))
                pulses.append(((frequency,) * 4, (strength,) * 4))
                if len(pulses) >= MAX_IMPORTED_WAVEFORM_POINTS:
                    return pulses

    if not pulses:
        raise ValueError("Dungeonlab PULSE 波形为空")
    return pulses


def load_pulse_waveforms(directory):
    """Load all .pulse files from a user-managed waveform directory."""
    directory = Path(directory)
    if not directory.exists():
        return []
    records = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if not path.is_file() or path.suffix.lower() != ".pulse":
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
            if text.lstrip().startswith("Dungeonlab+pulse:"):
                pulses = parse_dungeonlab_pulse(text)
                source = f"pulse-file:{path.name}"
            else:
                pulses = parse_waveform_text(text)
                source = f"waveform-file:{path.name}"
        except Exception as exc:
            logging.warning("无法加载波形文件 %s：%s", path, exc)
            continue
        records.append({
            "name": path.stem.strip() or path.name,
            "source": source,
            "pulses": pulses,
        })
    return records


def load_waveforms():
    path = ensure_waveforms()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        payload = DEFAULT_WAVEFORMS
    records = []
    for item in payload.get("waveforms", []):
        name = str(item.get("name", "")).strip()
        frames = item.get("frames", [])
        if not name or not frames:
            continue
        try:
            pulses = [frame_to_pulse(frame) for frame in frames]
        except Exception:
            continue
        source = str(item.get("source", ""))
        # File-backed waveforms are always re-read from client/pulse so
        # editing or replacing the original .pulse takes effect on restart.
        if source.startswith(("pulse-file:", "waveform-file:")):
            continue
        records.append({
            "name": name,
            "source": source,
            "pulses": pulses,
        })
    default_records = []
    for item in DEFAULT_WAVEFORMS.get("waveforms", []):
        try:
            default_records.append({
                "name": str(item["name"]),
                "source": str(item.get("source", "Codex built-in")),
                "pulses": [frame_to_pulse(frame) for frame in item["frames"]],
            })
        except Exception:
            pass
    existing_names = {record["name"] for record in records}
    for record in default_records:
        if record["name"] not in existing_names:
            records.append(record)
            existing_names.add(record["name"])
    for record in load_pulse_waveforms(ensure_waveform_directory()):
        if record["name"] in existing_names:
            record["name"] = f"{record['name']}（文件）"
        if record["name"] not in existing_names:
            records.append(record)
            existing_names.add(record["name"])
    if not records:
        ensure_waveforms().write_text(json.dumps(DEFAULT_WAVEFORMS, ensure_ascii=False, indent=2), encoding="utf-8")
        return load_waveforms()
    return records


def load_config() -> configparser.ConfigParser:
    path = ensure_config()
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8-sig")
    return cfg


def save_config(settings):
    cfg = configparser.ConfigParser()
    cfg["network"] = {
        "ws_port": str(settings.ws_port),
        "udp_port": str(settings.udp_port),
        "public_host": settings.public_host,
    }
    cfg["safety"] = {
        "dry_run": str(settings.dry_run).lower(),
        "output_enabled": str(settings.output_enabled).lower(),
        "max_coyote_strength": str(settings.max_coyote_strength),
        "stale_timeout_seconds": str(settings.stale_timeout_seconds),
        "send_hz": str(settings.send_hz),
        "channel": settings.channel,
        "channel_b_multiplier": f"{settings.channel_b_multiplier:.1f}",
    }
    cfg["mapping"] = {
        "suspicion_deadzone": str(settings.suspicion_deadzone),
        "low_suspicion_threshold": str(settings.low_suspicion_threshold),
        "low_suspicion_min_units": str(settings.low_suspicion_min_units),
        "suspicion_at_max": str(settings.suspicion_at_max),
        "output_scale_percent": str(settings.output_scale_percent),
        "ramp_units_per_second": str(settings.ramp_units_per_second),
        "vibrator_weak_percent": str(settings.vibrator_weak_percent),
        "vibrator_strong_percent": str(settings.vibrator_strong_percent),
        "piston_weak_percent": str(settings.piston_weak_percent),
        "piston_medium_percent": str(settings.piston_medium_percent),
        "piston_strong_percent": str(settings.piston_strong_percent),
        "climax_range_percent": str(settings.climax_range_percent),
        "climax_active_percent": str(settings.climax_active_percent),
    }
    cfg["waveform"] = {
        "climax_enabled": str(settings.climax_wave_enabled).lower(),
        # Keep selected for older clients; new clients use one name per layer.
        "selected": settings.vibrator_waveform_name,
        "vibrator": settings.vibrator_waveform_name,
        "piston": settings.piston_waveform_name,
        "climax": settings.climax_waveform_name,
    }
    with (ROOT / "client_config.ini").open("w", encoding="utf-8") as f:
        cfg.write(f)


def cfg_bool(cfg, section, key, default):
    try:
        return cfg.getboolean(section, key)
    except Exception:
        return default


def cfg_int(cfg, section, key, default):
    try:
        return cfg.getint(section, key)
    except Exception:
        return default


def cfg_float(cfg, section, key, default):
    try:
        return cfg.getfloat(section, key)
    except Exception:
        return default


def cfg_str(cfg, section, key, default):
    try:
        return cfg.get(section, key)
    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def vibrator_display_mode(configured_mode, effective_mode, is_on):
    if not is_on:
        return "关"
    configured = str(configured_mode or "").strip().lower()
    effective = str(effective_mode or "").strip().lower()
    if "random" in configured or "随机" in configured:
        if effective == "high":
            return "强(随机)"
        # Random's fallback is between weak and strong; use the weak label
        # until the game's currentMode reports a concrete result.
        return "弱(随机)"
    if effective == "high" or configured == "high":
        return "强"
    if effective == "low" or configured == "low":
        return "弱"
    return "关"


def piston_display_mode(configured_mode, effective_mode, is_on, is_random):
    if not is_on:
        return "关"
    try:
        configured = int(configured_mode or 0)
        effective = int(effective_mode or 0)
    except (TypeError, ValueError):
        configured, effective = 0, 0
    labels = {1: "弱", 2: "中", 3: "强"}
    if is_random or configured == 4:
        return labels.get(effective, "中")
    return labels.get(effective or configured, "关")


def resolve_vibrator_intensity(settings, configured_mode, effective_mode, is_on, fallback):
    if not is_on:
        return 0.0
    configured = str(configured_mode or "").strip().lower()
    effective = str(effective_mode or "").strip().lower()
    mode = effective if effective not in ("", "unknown", "random") else configured
    if mode in ("high", "strong"):
        return clamp(float(settings.vibrator_strong_percent), 0.0, 100.0)
    if mode in ("low", "weak"):
        return clamp(float(settings.vibrator_weak_percent), 0.0, 100.0)
    if mode == "off":
        return 0.0
    return clamp(float(fallback or 0.0), 0.0, 100.0)


def resolve_piston_intensity(settings, effective_mode, configured_mode, is_on, fallback):
    if not is_on:
        return 0.0
    try:
        effective = int(effective_mode or 0)
        configured = int(configured_mode or 0)
    except (TypeError, ValueError):
        effective, configured = 0, 0
    mode = effective or (configured if configured != 4 else 0)
    if mode <= 1 and mode > 0:
        return clamp(float(settings.piston_weak_percent), 0.0, 100.0)
    if mode == 2:
        return clamp(float(settings.piston_medium_percent), 0.0, 100.0)
    if mode >= 3:
        return clamp(float(settings.piston_strong_percent), 0.0, 100.0)
    return clamp(float(fallback or 0.0), 0.0, 100.0)


def local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("223.5.5.5", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def parse_channels(value: str):
    value = (value or "A").strip().lower()
    if value in ("both", "ab", "a+b", "all", "双通道"):
        return [Channel.A, Channel.B]
    if value == "b":
        return [Channel.B]
    return [Channel.A]


PHASE_SLOTS = [0.1, 0.3, 0.7, 0.5, 0.9, 0.2, 0.4, 0.6, 0.8]


def waveform_period(pulses):
    return max(0.1, len(pulses or []) * 0.1)


def phase_offset(index, count, period):
    if index < len(PHASE_SLOTS):
        return PHASE_SLOTS[index] * period
    return ((index + 0.5) / max(1, count)) * period


def sample_pulse(pulses, time_seconds):
    if not pulses:
        return 10, 0
    period = waveform_period(pulses)
    local = time_seconds % period
    sub_index = int(local / 0.025) % (len(pulses) * 4)
    frame_index = min(len(pulses) - 1, sub_index // 4)
    slot_index = sub_index % 4
    freqs, strengths = pulses[frame_index]
    return int(freqs[slot_index]), int(strengths[slot_index])


PISTON_OVERLAY_PULSES = [
    ((42, 42, 42, 42), (100, 70, 20, 80)),
    ((48, 48, 48, 48), (20, 80, 100, 40)),
    ((55, 55, 55, 55), (0, 30, 90, 100)),
    ((48, 48, 48, 48), (70, 20, 0, 60)),
]

CLIMAX_OVERLAY_PULSES = [
    ((180, 180, 180, 180), (0, 30, 80, 100)),
    ((200, 200, 200, 200), (20, 70, 100, 40)),
    ((220, 220, 220, 220), (70, 100, 30, 0)),
    ((200, 200, 200, 200), (100, 60, 10, 40)),
]


def composite_strength_percent(
    vibrator_percent,
    piston_percent,
    climax_bar_percent,
    climax_active,
    vibrator_strong,
    piston_strong,
    climax_range_percent=70.0,
    climax_active_percent=90.0,
):
    vibrator_percent = clamp(float(vibrator_percent or 0), 0.0, 100.0)
    piston_percent = clamp(float(piston_percent or 0), 0.0, 100.0)
    climax_bar_percent = clamp(float(climax_bar_percent or 0), 0.0, 100.0)
    if vibrator_percent <= 0 and piston_percent <= 0 and climax_bar_percent <= 0 and not climax_active:
        return 0.0

    if climax_active:
        return clamp(float(climax_active_percent or 0), 0.0, 100.0)
    climax_percent = climax_bar_percent * clamp(float(climax_range_percent or 0), 0.0, 100.0) / 100.0
    strongest = max(vibrator_percent, piston_percent)
    second = min(vibrator_percent, piston_percent)
    multiplier = 0.1 if vibrator_strong or piston_strong else 0.2
    return clamp(strongest + second * multiplier + climax_percent * multiplier, 0.0, 100.0)


def compose_composite_frame(
    layers,
    max_units,
    start_time,
    climax_active=False,
    climax_range_percent=70.0,
    climax_active_percent=90.0,
):
    max_units = max(1, int(max_units or 1))
    active_layers = []
    for layer in (layers or []):
        if len(layer) >= 6:
            pulses, percent, phase, role, strong, epoch = layer[:6]
        else:
            pulses, percent, phase, role, strong = layer
            epoch = 0.0
        if pulses and float(percent) > 0:
            active_layers.append((pulses, float(percent), float(phase), role, bool(strong), float(epoch)))
    if not active_layers:
        return ((10, 10, 10, 10), (0, 0, 0, 0)), 0.0

    freqs = []
    strengths = []
    peak_units = 0.0
    for slot in range(4):
        t = start_time + slot * 0.025
        sampled = {"vibrator": 0.0, "piston": 0.0, "climax": 0.0}
        chosen_freq = 10
        strongest = 0.0
        for pulses, percent, phase, role, _strong, epoch in active_layers:
            freq, wave_strength = sample_pulse(pulses, t - epoch - phase)
            contribution = percent * (wave_strength / 100.0)
            sampled[role] = max(sampled.get(role, 0.0), contribution)
            if contribution > strongest:
                strongest = contribution
                chosen_freq = freq

        strong_mode = any(strong for _pulses, _percent, _phase, role, strong, _epoch in active_layers
                          if role in ("vibrator", "piston"))
        total_percent = composite_strength_percent(
            sampled["vibrator"],
            sampled["piston"],
            sampled["climax"] * 100.0 / max(0.01, float(climax_range_percent)) if sampled["climax"] > 0 else 0.0,
            climax_active,
            strong_mode,
            False,
            climax_range_percent,
            climax_active_percent,
        )
        total_units = (total_percent / 100.0) * max_units
        peak_units = max(peak_units, total_units)
        freqs.append(int(clamp(chosen_freq, 10, 240)))
        strengths.append(int(round(total_percent)))

    return (tuple(freqs), tuple(strengths)), peak_units


def compose_wave_frame(pulses, npc_units, max_units, start_time):
    period = waveform_period(pulses)
    npc_units = [float(unit) for unit in (npc_units or []) if float(unit) > 0]
    if not pulses or not npc_units:
        return ((10, 10, 10, 10), (0, 0, 0, 0)), 0.0
    count = len(npc_units)
    layers = [
        (pulses, unit, phase_offset(index, count, period), "vibrator", False)
        for index, unit in enumerate(npc_units)
    ]
    return compose_composite_frame(layers, max_units, start_time)


@dataclass
class Settings:
    ws_port: int = 5678
    udp_port: int = 39090
    public_host: str = "auto"
    dry_run: bool = True
    output_enabled: bool = False
    max_coyote_strength: int = 20
    stale_timeout_seconds: float = 2.0
    send_hz: float = 5.0
    channel: str = "A"
    channel_b_multiplier: float = 1.0
    suspicion_deadzone: float = 0.0
    low_suspicion_threshold: float = 0.0
    low_suspicion_min_units: float = 0.0
    suspicion_at_max: float = 1.0
    output_scale_percent: float = 100.0
    ramp_units_per_second: float = 10.0
    vibrator_weak_percent: int = 30
    vibrator_strong_percent: int = 70
    piston_weak_percent: int = 30
    piston_medium_percent: int = 50
    piston_strong_percent: int = 70
    climax_range_percent: int = 70
    climax_active_percent: int = 90
    climax_wave_enabled: bool = True
    vibrator_waveform_builtin: bool = True
    vibrator_waveform_name: str = "官方示例 - 呼吸"
    vibrator_waveform_pulses: list = field(default_factory=list)
    piston_waveform_builtin: bool = True
    piston_waveform_name: str = "默认 - 活塞机"
    piston_waveform_pulses: list = field(default_factory=list)
    climax_waveform_builtin: bool = True
    climax_waveform_name: str = "默认 - 高潮"
    climax_waveform_pulses: list = field(default_factory=list)


@dataclass
class RuntimeState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    running: bool = False
    app_bound: bool = False
    game_ok: bool = False
    game_armed: bool = True
    dry_run: bool = True
    output_enabled: bool = False
    suspicion: float = 0.0
    intensity_percent: float = 0.0
    direct_vibrator: bool = False
    vibrator_on: bool = False
    vibrator_strong: bool = False
    configured_mode: str = "Unknown"
    effective_mode: str = "Unknown"
    raw_strength: int = 0
    climax_percent: float = 0.0
    climax_value: int = 0
    climax_max: float = 100.0
    has_climax_data: bool = False
    climax_active: bool = False
    piston_on: bool = False
    piston_random: bool = False
    piston_strong: bool = False
    piston_configured_mode: int = 0
    piston_effective_mode: int = 0
    piston_intensity_percent: float = 0.0
    has_piston_data: bool = False
    npc_suspicions: list = field(default_factory=list)
    npc_units: list = field(default_factory=list)
    found_count: int = 0
    npc_count: int = 0
    active_npc_count: int = 0
    planned_output_percent: float = 0.0
    preview_units: int = 0
    actual_units: int = 0
    coyote_target_units: int = 0
    display_units: int = 0
    max_units: int = 20
    last_udp_time: float = 0.0
    packet_count: int = 0
    status_text: str = "未启动"
    error_text: str = ""
    qr_path: str = ""
    qr_url: str = ""


class GameUdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, worker):
        self.worker = worker

    def datagram_received(self, data, addr):
        now = asyncio.get_running_loop().time()
        try:
            payload = json.loads(data.decode("utf-8", errors="replace"))
            with self.worker.state.lock:
                # The protocol is also exercised by lightweight test workers
                # that do not expose a settings object.  The real bridge
                # always has one; when it is absent, retain the game's raw
                # values instead of rejecting the packet.
                settings = getattr(self.worker, "settings", None)
                packet_type = str(payload.get("type", "") or "").lower()
                direct_vibrator = packet_type == "secretflashermanaka.vibrator" or "effectiveMode" in payload
                game_armed = bool(payload.get("armed", True))
                panic = bool(payload.get("panic", False)) or str(payload.get("reason", "") or "").lower() == "panic"
                raw_intensity = float(payload.get("intensity", 0) or 0)
                intensity_percent = clamp(raw_intensity, 0.0, 100.0)
                vibrator_on = bool(payload.get("vibratorOn", intensity_percent > 0.0))
                suspicion = float(payload.get("suspicion", 0) or 0)
                npc_suspicions = payload.get("npcSuspicions", [])
                if isinstance(npc_suspicions, list):
                    parsed_npcs = []
                    for value in npc_suspicions:
                        try:
                            parsed_npcs.append(float(value or 0))
                        except Exception:
                            pass
                else:
                    parsed_npcs = []
                if not parsed_npcs and suspicion > 0:
                    parsed_npcs = [suspicion]

                self.worker.state.suspicion = suspicion
                self.worker.state.intensity_percent = intensity_percent if direct_vibrator else clamp(suspicion * 100.0, 0.0, 100.0)
                self.worker.state.direct_vibrator = direct_vibrator
                self.worker.state.vibrator_on = vibrator_on if direct_vibrator else intensity_percent > 0.0
                self.worker.state.vibrator_strong = bool(payload.get("vibratorStrong", str(payload.get("effectiveMode", "")).lower() == "high"))
                self.worker.state.configured_mode = str(payload.get("configuredMode", "Unknown") or "Unknown")
                self.worker.state.effective_mode = str(payload.get("effectiveMode", "Unknown") or "Unknown")
                try:
                    self.worker.state.raw_strength = int(payload.get("rawStrength", 0) or 0)
                except Exception:
                    self.worker.state.raw_strength = 0
                try:
                    self.worker.state.climax_percent = clamp(float(payload.get("climaxPercent", 0) or 0), 0.0, 100.0)
                    self.worker.state.climax_value = int(payload.get("climaxValue", 0) or 0)
                except Exception:
                    self.worker.state.climax_percent = 0.0
                    self.worker.state.climax_value = 0
                self.worker.state.climax_active = bool(payload.get("climaxActive", False))
                self.worker.state.has_climax_data = bool(payload.get("hasClimaxData", "climaxPercent" in payload))
                self.worker.state.piston_on = bool(payload.get("pistonOn", False))
                self.worker.state.piston_random = bool(payload.get("pistonRandom", False))
                self.worker.state.has_piston_data = bool(payload.get("hasPistonData", "pistonIntensity" in payload))
                try:
                    self.worker.state.piston_configured_mode = int(payload.get("pistonConfiguredMode", 0) or 0)
                    self.worker.state.piston_effective_mode = int(payload.get("pistonEffectiveMode", 0) or 0)
                    self.worker.state.piston_intensity_percent = clamp(float(payload.get("pistonIntensity", 0) or 0), 0.0, 100.0)
                except Exception:
                    self.worker.state.piston_configured_mode = 0
                    self.worker.state.piston_effective_mode = 0
                    self.worker.state.piston_intensity_percent = 0.0
                self.worker.state.piston_strong = bool(
                    payload.get(
                        "pistonStrong",
                        self.worker.state.piston_effective_mode == 3
                        or (
                            self.worker.state.piston_effective_mode == 0
                            and self.worker.state.piston_configured_mode == 3
                        ),
                    )
                )
                if direct_vibrator and settings is not None:
                    self.worker.state.intensity_percent = resolve_vibrator_intensity(
                        settings,
                        self.worker.state.configured_mode,
                        self.worker.state.effective_mode,
                        self.worker.state.vibrator_on,
                        intensity_percent,
                    )
                if settings is not None:
                    self.worker.state.piston_intensity_percent = resolve_piston_intensity(
                        settings,
                        self.worker.state.piston_effective_mode,
                        self.worker.state.piston_configured_mode,
                        self.worker.state.piston_on,
                        self.worker.state.piston_intensity_percent,
                    )
                self.worker.state.npc_suspicions = parsed_npcs
                self.worker.state.found_count = int(payload.get("foundCount", 0) or 0)
                self.worker.state.npc_count = int(payload.get("npcCount", 0) or 0)
                self.worker.state.active_npc_count = int(payload.get("activeNpcCount", 0) or 0)
                self.worker.state.last_udp_time = now
                self.worker.state.packet_count += 1
                self.worker.state.game_ok = True
                self.worker.state.game_armed = game_armed
                self.worker.state.error_text = ""
            if panic:
                self.worker.send_event("panic", "F12")
        except Exception as exc:
            self.worker.send_event("error", f"UDP 数据解析失败：{exc}")


class BridgeWorker(threading.Thread):
    def __init__(self, settings: Settings, state: RuntimeState, events: queue.Queue):
        super().__init__(daemon=True)
        self.settings = settings
        self.state = state
        self.events = events
        self.stop_flag = threading.Event()
        self.current_units = 0.0
        self.preview_units = 0.0
        self.last_sent_units = -1
        self.last_send_time = 0.0
        self.pulse_cursor = 0
        self.pulse_time = 0.0
        self.layer_epochs = {"vibrator": 0.0, "piston": 0.0, "climax": 0.0}
        self.layer_was_active = {"vibrator": False, "piston": False, "climax": False}
        self.layer_reset_pending = False
        self.last_pulse_send_time = 0.0
        self.pulses_cleared = True
        self.current_npc_units = []
        self.current_composite_layers = []
        self.current_composite_climax_active = False
        self.last_channel = None
        self.last_multiplier = None

    def send_event(self, kind, payload=None):
        self.events.put((kind, payload))

    def stop(self):
        self.stop_flag.set()

    def run(self):
        try:
            asyncio.run(self.main())
        except Exception as exc:
            self.send_event("error", f"客户端启动失败：{exc}")
            with self.state.lock:
                self.state.running = False
                self.state.status_text = "启动失败"

    async def main(self):
        os.makedirs(ROOT / "logs", exist_ok=True)
        logging.basicConfig(
            filename=ROOT / "logs" / "client.log",
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
            encoding="utf-8",
        )

        public_host = self.settings.public_host.strip()
        if public_host.lower() == "auto":
            public_host = local_ip()

        loop = asyncio.get_running_loop()
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: GameUdpProtocol(self),
            local_addr=("127.0.0.1", self.settings.udp_port),
        )

        with self.state.lock:
            self.state.running = True
            self.state.status_text = "等待扫码"
            self.state.dry_run = self.settings.dry_run
            self.state.output_enabled = self.settings.output_enabled
            self.state.max_units = self.settings.max_coyote_strength

        try:
            async with DGLabWSServer("0.0.0.0", self.settings.ws_port, heartbeat_interval=20) as server:
                client = server.new_local_client()
                qr_url = client.get_qrcode(f"ws://{public_host}:{self.settings.ws_port}")
                qr_path = ROOT / "qrcode.png"
                qrcode.make(qr_url).save(qr_path)
                (ROOT / "qrcode-url.txt").write_text(qr_url, encoding="utf-8")
                logging.info("QR generated for ws://%s:%s", public_host, self.settings.ws_port)

                with self.state.lock:
                    self.state.qr_path = str(qr_path)
                    self.state.qr_url = qr_url
                self.send_event("qr", str(qr_path))

                tasks = [
                    asyncio.create_task(self.bind_loop(client)),
                    asyncio.create_task(self.output_loop(client)),
                ]

                while not self.stop_flag.is_set():
                    await asyncio.sleep(0.1)

                for task in tasks:
                    task.cancel()
                await self.safe_zero(client)
        finally:
            transport.close()
            with self.state.lock:
                self.state.running = False
                self.state.app_bound = False
                self.state.status_text = "已停止"

    async def bind_loop(self, client):
        while not self.stop_flag.is_set():
            try:
                with self.state.lock:
                    self.state.status_text = "等待 App 扫码"
                result = await client.rebind()
                if result == RetCode.SUCCESS:
                    self.last_sent_units = -1
                    self.last_send_time = 0.0
                    self.pulses_cleared = True
                    with self.state.lock:
                        self.state.app_bound = True
                        self.state.status_text = "App 已连接"
                    logging.info("App bound")
                else:
                    self.send_event("error", f"绑定返回：{result}")
                    await asyncio.sleep(1)
                    continue

                while not self.stop_flag.is_set():
                    data = await client.recv_data()
                    if data == RetCode.CLIENT_DISCONNECTED:
                        self.last_sent_units = -1
                        self.last_send_time = 0.0
                        with self.state.lock:
                            self.state.app_bound = False
                            self.state.status_text = "App 已断开"
                        await self.safe_zero(client)
                        break
                    if isinstance(data, StrengthData):
                        logging.info("App strength: %s", data)
                    elif isinstance(data, FeedbackButton):
                        self.send_event("feedback", str(data))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                with self.state.lock:
                    self.state.app_bound = False
                    self.state.status_text = "App 连接异常"
                self.send_event("error", f"App 连接异常：{exc}")
                await asyncio.sleep(1)

    def snapshot_settings(self):
        return Settings(**self.settings.__dict__)

    def map_suspicion_to_units(self, suspicion, settings):
        suspicion = float(suspicion or 0.0)
        max_units = max(0, int(settings.max_coyote_strength or 0))
        if suspicion <= 0.0 or max_units <= 0:
            return 0, 0.0

        low = settings.suspicion_deadzone
        high = settings.suspicion_at_max
        if high <= low + 0.001:
            high = low + 1
        planned_percent = ((suspicion - low) / (high - low)) * settings.output_scale_percent
        planned_percent = clamp(planned_percent, 0.0, 100.0)
        target_units = round((planned_percent / 100.0) * max_units)

        low_threshold = max(0.0, float(settings.low_suspicion_threshold or 0.0)) / 100.0
        low_min_units = int(round(clamp(float(settings.low_suspicion_min_units or 0.0), 0.0, max_units)))
        if low_threshold > 0.0 and low_min_units > 0 and suspicion <= low_threshold:
            target_units = max(target_units, low_min_units)
            planned_percent = max(planned_percent, (target_units / max(1, max_units)) * 100.0)

        return int(clamp(target_units, 0, max_units)), clamp(planned_percent, 0.0, 100.0)

    def smooth_unit_list(self, targets, max_step):
        current = list(self.current_npc_units)
        if len(current) < len(targets):
            current.extend([0.0] * (len(targets) - len(current)))
        next_units = []
        for index, target in enumerate(targets):
            old = current[index] if index < len(current) else 0.0
            if target > old + max_step:
                value = old + max_step
            elif target < old - max_step:
                value = old - max_step
            else:
                value = float(target)
            next_units.append(value)
        self.current_npc_units = next_units
        return [int(round(clamp(value, 0, 200))) for value in next_units]

    def update_layer_epochs(self, active_roles):
        active_roles = set(active_roles or [])
        restarted = False
        for role in ("vibrator", "piston", "climax"):
            active = role in active_roles
            if active and not self.layer_was_active[role]:
                self.layer_epochs[role] = self.pulse_time
                restarted = True
            self.layer_was_active[role] = active
        if restarted:
            self.layer_reset_pending = True
        return restarted

    def compute_output(self, now):
        settings = self.snapshot_settings()
        with self.state.lock:
            suspicion = self.state.suspicion
            intensity_percent = self.state.intensity_percent
            direct_vibrator = self.state.direct_vibrator
            game_armed = self.state.game_armed
            vibrator_on = self.state.vibrator_on
            climax_percent = self.state.climax_percent
            climax_active = self.state.climax_active
            piston_intensity_percent = self.state.piston_intensity_percent
            has_piston_data = self.state.has_piston_data
            piston_on = self.state.piston_on
            vibrator_strong_state = self.state.vibrator_strong
            piston_strong_state = self.state.piston_strong
            npc_suspicions = list(self.state.npc_suspicions)
            last_udp_time = self.state.last_udp_time

        if not game_armed:
            climax_percent = 0.0
            climax_active = False
        elif not settings.climax_wave_enabled:
            climax_percent = 0.0
            climax_active = False

        stale = last_udp_time <= 0 or (now - last_udp_time) > settings.stale_timeout_seconds
        vibrator_layer_units = 0
        piston_layer_units = 0
        vibrator_percent = 0.0
        piston_percent = 0.0
        vibrator_strong = False
        piston_strong = False
        if stale or not game_armed:
            planned_percent = 0.0
            npc_target_units = []
        else:
            if direct_vibrator:
                vibrator_percent = intensity_percent if vibrator_on else 0.0
                piston_percent = piston_intensity_percent if has_piston_data and piston_on else 0.0
                vibrator_strong = vibrator_strong_state
                piston_strong = piston_strong_state
                planned_percent = composite_strength_percent(
                    vibrator_percent,
                    piston_percent,
                    climax_percent,
                    climax_active,
                    vibrator_strong,
                    piston_strong,
                    settings.climax_range_percent,
                    settings.climax_active_percent,
                )
                max_units = max(0, int(settings.max_coyote_strength or 0))
                vibrator_layer_units = round((vibrator_percent / 100.0) * max_units)
                piston_layer_units = round((piston_percent / 100.0) * max_units)
                target_units = round((planned_percent / 100.0) * max_units)
                npc_target_units = [target_units] if target_units > 0 else []
            else:
                if not npc_suspicions:
                    npc_suspicions = [suspicion] if suspicion > 0 else []
                npc_target_units = []
                planned_values = []
                for npc_suspicion in npc_suspicions:
                    units, percent = self.map_suspicion_to_units(npc_suspicion, settings)
                    if units > 0:
                        npc_target_units.append(units)
                        planned_values.append(percent)
                planned_percent = max(planned_values) if planned_values else 0.0

        target_units = max(npc_target_units) if npc_target_units else 0
        if not direct_vibrator:
            vibrator_layer_units = target_units
        if settings.dry_run or not settings.output_enabled:
            actual_target = 0
        else:
            actual_target = target_units

        return (stale, planned_percent, target_units, actual_target, settings, npc_target_units,
                climax_percent, climax_active, piston_intensity_percent,
                vibrator_layer_units, piston_layer_units, vibrator_percent,
                piston_percent, vibrator_strong, piston_strong)

    async def output_loop(self, client):
        loop = asyncio.get_running_loop()
        previous = loop.time()
        last_status_log = 0.0

        while not self.stop_flag.is_set():
            now = loop.time()
            delta = max(0.001, min(1.0, now - previous))
            previous = now
            (
                stale,
                planned_percent,
                preview_target_units,
                actual_target_units,
                settings,
                npc_target_units,
                climax_percent,
                climax_active,
                piston_intensity_percent,
                vibrator_layer_units,
                piston_layer_units,
                vibrator_percent,
                piston_percent,
                vibrator_strong,
                piston_strong,
            ) = self.compute_output(now)

            # In test mode no pulse chunk is sent, so advance the preview
            # clock here instead of waiting for next_pulse_chunk().
            if settings.dry_run or not settings.output_enabled:
                self.pulse_time += delta

            max_step = max(1.0, settings.ramp_units_per_second) * delta
            npc_units = self.smooth_unit_list(npc_target_units, max_step)
            composite_layers = []
            active_wave_roles = []
            if vibrator_percent > 0 and settings.vibrator_waveform_pulses:
                active_wave_roles.append("vibrator")
            if piston_percent > 0 and settings.piston_waveform_pulses:
                active_wave_roles.append("piston")
            if (climax_percent > 0 or climax_active) and settings.climax_waveform_pulses:
                active_wave_roles.append("climax")
            self.update_layer_epochs(active_wave_roles)
            if "vibrator" in active_wave_roles:
                composite_layers.append((settings.vibrator_waveform_pulses, vibrator_percent, 0.0, "vibrator", vibrator_strong, self.layer_epochs["vibrator"]))
            if "piston" in active_wave_roles:
                composite_layers.append((settings.piston_waveform_pulses, piston_percent, 0.0, "piston", piston_strong, self.layer_epochs["piston"]))
            if "climax" in active_wave_roles:
                climax_layer_percent = 100.0 if climax_active else climax_percent
                composite_layers.append((settings.climax_waveform_pulses, climax_layer_percent, 0.0, "climax", False, self.layer_epochs["climax"]))
            self.current_composite_layers = composite_layers
            self.current_composite_climax_active = climax_active

            if preview_target_units > self.preview_units + max_step:
                self.preview_units += max_step
            elif preview_target_units < self.preview_units - max_step:
                self.preview_units -= max_step
            else:
                self.preview_units = float(preview_target_units)

            channel_target_units = settings.max_coyote_strength if npc_units else 0
            actual_target_units = 0 if (settings.dry_run or not settings.output_enabled) else channel_target_units

            if actual_target_units > self.current_units + max_step:
                self.current_units += max_step
            elif actual_target_units < self.current_units - max_step:
                self.current_units -= max_step
            else:
                self.current_units = float(actual_target_units)

            preview_units = int(round(clamp(self.preview_units, 0, settings.max_coyote_strength)))
            send_units = int(round(clamp(self.current_units, 0, settings.max_coyote_strength)))
            composed_frame, composed_units = compose_composite_frame(
                composite_layers,
                settings.max_coyote_strength,
                self.pulse_time,
                climax_active,
                settings.climax_range_percent,
                settings.climax_active_percent,
            )
            preview_units = int(round(clamp(composed_units, 0, settings.max_coyote_strength)))
            actual_effective_units = int(round(clamp(composed_units * (send_units / max(1, settings.max_coyote_strength)), 0, settings.max_coyote_strength)))
            display_units = preview_units if settings.dry_run else actual_effective_units
            send_due = (now - self.last_send_time) >= (1.0 / max(0.5, settings.send_hz))

            app_bound = False
            with self.state.lock:
                app_bound = self.state.app_bound
                self.state.game_ok = not stale
                self.state.planned_output_percent = planned_percent
                self.state.preview_units = preview_units
                self.state.actual_units = actual_effective_units
                self.state.coyote_target_units = send_units
                self.state.display_units = display_units
                self.state.npc_units = list(npc_units)
                self.state.max_units = settings.max_coyote_strength
                self.state.dry_run = settings.dry_run
                self.state.output_enabled = settings.output_enabled

            force_resend = send_units > 0 and (now - self.last_send_time) >= 1.0
            channel_or_ratio_changed = False
            if app_bound and client and self.last_channel is not None and self.last_channel != settings.channel:
                channel_or_ratio_changed = True
                old_channels = set(parse_channels(self.last_channel))
                new_channels = set(parse_channels(settings.channel))
                dropped_channels = old_channels - new_channels
                for ch in dropped_channels:
                    try:
                        await client.set_strength(ch, StrengthOperationType.SET_TO, 0)
                    except Exception:
                        pass
                    try:
                        await client.clear_pulses(ch)
                    except Exception:
                        pass
            if self.last_multiplier is not None and self.last_multiplier != settings.channel_b_multiplier:
                channel_or_ratio_changed = True

            if channel_or_ratio_changed:
                self.last_sent_units = -1
                self.last_send_time = 0.0

            self.last_channel = settings.channel
            self.last_multiplier = settings.channel_b_multiplier

            if (send_due or channel_or_ratio_changed) and app_bound and (send_units != self.last_sent_units or force_resend or channel_or_ratio_changed):
                try:
                    await self.send_strength(client, send_units, settings)
                    active_channels = parse_channels(settings.channel)
                    if Channel.A in active_channels and Channel.B in active_channels:
                        b_units = int(round(clamp(send_units * settings.channel_b_multiplier, 0, 200)))
                        logging.info("Sent strength: channel=Both A=%s B=%s forced=%s", send_units, b_units, force_resend or channel_or_ratio_changed)
                    else:
                        logging.info("Sent strength: units=%s channel=%s forced=%s", send_units, settings.channel, force_resend or channel_or_ratio_changed)
                    self.last_sent_units = send_units
                    self.last_send_time = now
                except Exception as exc:
                    self.send_event("error", f"发送强度失败：{exc}")

            pulse_interval = 0.2 if settings.send_hz >= 15 else 0.8
            pulse_active = (
                app_bound
                and settings.output_enabled
                and not settings.dry_run
                and bool(composite_layers)
                and send_units > 0
            )
            if pulse_active and (now - self.last_pulse_send_time) >= pulse_interval:
                try:
                    if self.layer_reset_pending:
                        await self.clear_pulses(client, settings)
                        self.pulses_cleared = True
                        self.layer_reset_pending = False
                    await self.send_pulses(client, settings)
                    self.last_pulse_send_time = now
                    self.pulses_cleared = False
                except Exception as exc:
                    self.send_event("error", f"发送波形失败：{exc}")
            elif not pulse_active and app_bound and not self.pulses_cleared:
                try:
                    await self.clear_pulses(client, settings)
                except Exception:
                    pass
                self.pulses_cleared = True
            if now - last_status_log > 2.0:
                last_status_log = now
                with self.state.lock:
                    logging.info(
                        "status app=%s game=%s vibrator=%s/%s intensity=%.1f planned=%.1f actual=%s/%s dry_run=%s enabled=%s packets=%s",
                        self.state.app_bound,
                        self.state.game_ok,
                        self.state.configured_mode,
                        self.state.effective_mode,
                        self.state.intensity_percent,
                        self.state.planned_output_percent,
                        self.state.actual_units,
                        self.state.max_units,
                        self.state.dry_run,
                        self.state.output_enabled,
                        self.state.packet_count,
                    )

            await asyncio.sleep(0.02)

    async def send_strength(self, client, units: int, settings=None):
        if settings is None:
            settings = self.settings
        if settings.dry_run:
            return
        channels = parse_channels(settings.channel)
        if Channel.A in channels and Channel.B in channels:
            a_units = int(round(clamp(units, 0, 200)))
            b_units = int(round(clamp(units * settings.channel_b_multiplier, 0, 200)))
            await client.set_strength(Channel.A, StrengthOperationType.SET_TO, a_units)
            await client.set_strength(Channel.B, StrengthOperationType.SET_TO, b_units)
        else:
            target_units = int(round(clamp(units, 0, 200)))
            for channel in channels:
                await client.set_strength(channel, StrengthOperationType.SET_TO, target_units)

    def next_pulse_chunk(self, settings, count=None):
        if count is None:
            count = 3 if settings.send_hz >= 15 else 10
        layers = list(self.current_composite_layers or [])
        if not layers:
            return []
        chunk = []
        for index in range(count):
            frame, _units = compose_composite_frame(
                layers,
                settings.max_coyote_strength,
                self.pulse_time + index * 0.1,
                self.current_composite_climax_active,
                settings.climax_range_percent,
                settings.climax_active_percent,
            )
            chunk.append(frame)
        # The App queues each pulse as 100 ms. Keep the generation cursor
        # continuous so newly activated layers can start at their own epoch.
        self.pulse_time += count * 0.1
        return chunk

    async def send_pulses(self, client, settings):
        chunk = self.next_pulse_chunk(settings)
        if not chunk:
            return
        for channel in parse_channels(settings.channel):
            await client.add_pulses(channel, *chunk)

    async def clear_pulses(self, client, settings):
        for channel in parse_channels(settings.channel):
            await client.clear_pulses(channel)

    async def safe_zero(self, client):
        with self.state.lock:
            app_bound = self.state.app_bound
        if not app_bound:
            return
        try:
            old_dry = self.settings.dry_run
            self.settings.dry_run = False
            for channel in (Channel.A, Channel.B):
                try:
                    await client.set_strength(channel, StrengthOperationType.SET_TO, 0)
                except Exception:
                    pass
                try:
                    await client.clear_pulses(channel)
                except Exception:
                    pass
            self.settings.dry_run = old_dry
        except Exception:
            pass


class CoyoteClientApp:
    BG = "#101215"
    PANEL = "#181c22"
    PANEL2 = "#202630"
    SETTINGS_PANEL_WIDTH = 286
    SETTINGS_CONTENT_WIDTH = SETTINGS_PANEL_WIDTH - 38
    URL_LABEL_HEIGHT = 42
    TIP_LABEL_HEIGHT = 21
    MESSAGE_CARD_WIDTH = SETTINGS_PANEL_WIDTH - 16
    MESSAGE_CARD_HEIGHT = 65
    TEXT = "#e8eef6"
    MUTED = "#91a0b5"
    CYAN = "#39c5d7"
    ORANGE = "#ffb454"
    GREEN = "#57d68d"
    RED = "#ff6b6b"
    YELLOW = "#ffd166"
    PINK = "#f783ac"
    PURPLE = "#b197fc"

    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.settings = Settings(
            ws_port=cfg_int(self.cfg, "network", "ws_port", 5678),
            udp_port=cfg_int(self.cfg, "network", "udp_port", 39090),
            public_host=cfg_str(self.cfg, "network", "public_host", "auto"),
            dry_run=cfg_bool(self.cfg, "safety", "dry_run", True),
            output_enabled=cfg_bool(self.cfg, "safety", "output_enabled", False),
            max_coyote_strength=cfg_int(self.cfg, "safety", "max_coyote_strength", 20),
            stale_timeout_seconds=cfg_float(self.cfg, "safety", "stale_timeout_seconds", 2.0),
            send_hz=cfg_float(self.cfg, "safety", "send_hz", 5.0),
            channel=cfg_str(self.cfg, "safety", "channel", "A"),
            channel_b_multiplier=clamp(round(cfg_float(self.cfg, "safety", "channel_b_multiplier", 1.0), 1), 0.1, 10.0),
            suspicion_deadzone=cfg_float(self.cfg, "mapping", "suspicion_deadzone", 0.0),
            low_suspicion_threshold=cfg_float(self.cfg, "mapping", "low_suspicion_threshold", 0.0),
            low_suspicion_min_units=cfg_float(self.cfg, "mapping", "low_suspicion_min_units", 0.0),
            suspicion_at_max=cfg_float(self.cfg, "mapping", "suspicion_at_max", 1.0),
            output_scale_percent=cfg_float(self.cfg, "mapping", "output_scale_percent", 100.0),
            ramp_units_per_second=cfg_float(self.cfg, "mapping", "ramp_units_per_second", 10.0),
            vibrator_weak_percent=cfg_int(self.cfg, "mapping", "vibrator_weak_percent", 30),
            vibrator_strong_percent=cfg_int(self.cfg, "mapping", "vibrator_strong_percent", 70),
            piston_weak_percent=cfg_int(self.cfg, "mapping", "piston_weak_percent", 30),
            piston_medium_percent=cfg_int(self.cfg, "mapping", "piston_medium_percent", 50),
            piston_strong_percent=cfg_int(self.cfg, "mapping", "piston_strong_percent", 70),
            climax_range_percent=cfg_int(self.cfg, "mapping", "climax_range_percent", 70),
            climax_active_percent=cfg_int(self.cfg, "mapping", "climax_active_percent", 90),
            climax_wave_enabled=cfg_bool(self.cfg, "waveform", "climax_enabled", True),
            vibrator_waveform_name=cfg_str(
                self.cfg,
                "waveform",
                "vibrator",
                cfg_str(self.cfg, "waveform", "selected", "官方示例 - 呼吸"),
            ),
            piston_waveform_name=cfg_str(self.cfg, "waveform", "piston", "默认 - 活塞机"),
            climax_waveform_name=cfg_str(self.cfg, "waveform", "climax", "默认 - 高潮"),
        )
        self.normalize_intensity_settings()
        self.waveform_records = load_waveforms()
        self.ensure_waveform_role("vibrator", self.settings.vibrator_waveform_name)
        self.ensure_waveform_role("piston", self.settings.piston_waveform_name)
        self.ensure_waveform_role("climax", self.settings.climax_waveform_name)
        self.settings.vibrator_waveform_pulses = self.get_waveform_pulses(self.settings.vibrator_waveform_name)
        self.settings.vibrator_waveform_builtin = self.is_builtin_waveform(self.settings.vibrator_waveform_name)
        self.settings.piston_waveform_pulses = self.get_waveform_pulses(self.settings.piston_waveform_name)
        self.settings.piston_waveform_builtin = self.is_builtin_waveform(self.settings.piston_waveform_name)
        self.settings.climax_waveform_pulses = self.get_waveform_pulses(self.settings.climax_waveform_name)
        self.settings.climax_waveform_builtin = self.is_builtin_waveform(self.settings.climax_waveform_name)
        self.state = RuntimeState()
        self.events = queue.Queue()
        self.worker = None
        self.history = []
        self.qr_image = None
        self.qr_large_image = None
        self.qr_path = ""
        self.qr_url = ""
        self.last_sample = 0.0
        self.compact_mode = False
        self.setup_window()
        self.setup_styles()
        self.build_ui()
        self.apply_settings_to_vars()
        self.root.after(100, self.tick)
        self.root.after(300, self.auto_start)

    def setup_window(self):
        self.root.title("Secret Flasher Manaka Vibrator Coyote Client")
        self.root.geometry("900x610")
        self.root.minsize(800, 340)
        self.root.configure(bg=self.BG)
        self.root.attributes("-topmost", False)

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("TLabel", background=self.BG, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Panel.TLabel", background=self.PANEL, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background=self.PANEL, foreground=self.MUTED, font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", background=self.BG, foreground=self.TEXT, font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCheckbutton", background=self.PANEL, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure("TRadiobutton", background=self.PANEL, foreground=self.TEXT, font=("Microsoft YaHei UI", 10))
        style.configure(
            "Channel.TRadiobutton",
            background=self.PANEL,
            foreground=self.TEXT,
            font=("Microsoft YaHei UI", 10),
            focuscolor=self.PANEL,
            focusthickness=0,
        )
        style.map(
            "Channel.TRadiobutton",
            background=[("active", self.PANEL)],
            foreground=[("active", self.TEXT)],
        )
        style.configure("TEntry", fieldbackground=self.PANEL2, foreground=self.TEXT, insertcolor=self.TEXT)

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        self.outer = outer

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text="Manaka Coyote Client", style="Title.TLabel").pack(side="left")
        self.status_row = ttk.Frame(header)
        self.status_row.pack(side="left", fill="x", expand=True, padx=(16, 0))
        self.app_badge = self.badge(self.status_row, "App 未连接", self.RED)
        self.game_badge = self.badge(self.status_row, "游戏无数据", self.RED)
        self.output_badge = self.badge(self.status_row, "测试模式", self.ORANGE)

        self.topmost_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            header,
            text="置顶悬浮",
            variable=self.topmost_var,
            command=self.on_topmost,
            bg=self.BG,
            fg=self.TEXT,
            activebackground=self.BG,
            activeforeground=self.TEXT,
            selectcolor=self.PANEL,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="right")
        self.compact_button = ttk.Button(header, text="只看图表", command=self.toggle_compact)
        self.compact_button.pack(side="right", padx=(0, 8))

        self.main = ttk.Frame(outer)
        self.main.pack(fill="both", expand=True)
        self.main.columnconfigure(0, weight=1)
        self.main.columnconfigure(1, weight=0, minsize=12)
        self.main.columnconfigure(2, weight=0, minsize=self.SETTINGS_PANEL_WIDTH)
        self.main.rowconfigure(0, weight=1)

        self.left_panel = ttk.Frame(self.main, style="Panel.TFrame", padding=(12, 12, 12, 0))
        self.left_panel.grid(row=0, column=0, sticky="nsew")
        self.left_panel.rowconfigure(1, weight=1)

        chart_head = ttk.Frame(self.left_panel, style="Panel.TFrame")
        chart_head.pack(fill="x")
        ttk.Label(chart_head, text="郊狼输出波形", style="Panel.TLabel", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        self.value_label = ttk.Label(chart_head, text="振动器 关 | 活塞 关 | 模拟 0/20", style="Muted.TLabel")
        self.value_label.pack(side="right")

        self.chart = tk.Canvas(self.left_panel, bg="#0b0e12", highlightthickness=0)
        self.chart.pack(fill="both", expand=True, pady=(10, 0))
        self.chart.bind("<Double-Button-1>", lambda _event: self.toggle_compact())
        legend = ttk.Label(self.left_panel, text="黄线=郊狼输出  青线=高潮  粉线=振动器  紫线=活塞机", style="Muted.TLabel")
        legend.pack(anchor="w")

        self.right_shell = ttk.Frame(self.main, style="Panel.TFrame", width=self.SETTINGS_PANEL_WIDTH)
        self.right_shell.grid(row=0, column=2, sticky="nsew")
        self.right_shell.grid_propagate(False)
        self.right_shell.columnconfigure(0, weight=1)
        self.right_shell.rowconfigure(0, weight=1)
        self.right_shell.rowconfigure(1, weight=0)

        self.right_canvas = tk.Canvas(self.right_shell, bg=self.PANEL, highlightthickness=0, bd=0)
        self.right_scroll = ttk.Scrollbar(self.right_shell, orient="vertical", command=self.right_canvas.yview)
        self.right_canvas.configure(yscrollcommand=self.right_scroll.set)
        self.right_canvas.grid(row=0, column=0, sticky="nsew")
        self.right_scroll.grid(row=0, column=1, sticky="ns")

        self.message_card = tk.Frame(
            self.right_shell,
            width=self.MESSAGE_CARD_WIDTH,
            height=self.MESSAGE_CARD_HEIGHT,
            bg=self.PANEL2,
            highlightthickness=1,
            highlightbackground="#384150",
            bd=0,
            padx=10,
            pady=8,
        )
        self.message_card.pack_propagate(False)
        self.message_card.grid(row=1, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 8))
        self.message_label = tk.Label(
            self.message_card,
            text="",
            bg=self.PANEL2,
            fg=self.MUTED,
            justify="left",
            anchor="w",
            wraplength=250,
            font=("Microsoft YaHei UI", 9),
        )
        self.message_label.pack(fill="x")

        right = ttk.Frame(self.right_canvas, style="Panel.TFrame", padding=12)
        self.right_panel = right
        self.right_window = self.right_canvas.create_window((0, 0), window=right, anchor="nw")
        self.right_panel.bind("<Configure>", self.on_right_panel_configure)
        self.right_canvas.bind("<Configure>", self.on_right_canvas_configure)
        self.bind_right_scroll(self.right_canvas)
        self.bind_right_scroll(self.right_panel)

        buttons = ttk.Frame(right, style="Panel.TFrame")
        buttons.pack(fill="x")
        self.start_button = ttk.Button(buttons, text="开始连接", style="Accent.TButton", command=self.toggle_start)
        self.start_button.pack(side="left", fill="x", expand=True)
        ttk.Button(buttons, text="急停归零", command=self.panic_stop).pack(side="left", padx=(8, 0))

        self.qr_section = ttk.Frame(right, style="Panel.TFrame")
        self.qr_section.pack(fill="x", pady=(12, 10))
        ttk.Label(self.qr_section, text="扫码连接", style="Panel.TLabel", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.qr_holder = tk.Frame(self.qr_section, bg=self.PANEL2, width=260, height=260)
        self.qr_holder.pack(pady=(8, 8))
        self.qr_holder.pack_propagate(False)
        self.qr_box = tk.Label(
            self.qr_holder,
            bg=self.PANEL2,
            fg=self.MUTED,
            text="正在生成二维码...",
            wraplength=180,
            justify="center",
        )
        self.qr_box.place(relx=0.5, rely=0.5, anchor="center")
        qr_actions = ttk.Frame(self.qr_section, style="Panel.TFrame")
        qr_actions.pack(fill="x")
        ttk.Button(qr_actions, text="复制链接", command=self.copy_qr_url).pack(side="left", fill="x", expand=True)
        ttk.Button(qr_actions, text="刷新二维码", command=self.refresh_qr).pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.url_label_holder = ttk.Frame(
            self.qr_section,
            style="Panel.TFrame",
            height=self.URL_LABEL_HEIGHT,
        )
        self.url_label_holder.pack(fill="x", pady=(8, 0))
        self.url_label_holder.pack_propagate(False)
        self.url_label = ttk.Label(
            self.url_label_holder,
            text="客户端会自动生成二维码",
            style="Muted.TLabel",
            wraplength=self.SETTINGS_CONTENT_WIDTH,
        )
        self.url_label.pack(fill="x")

        mode_frame = ttk.Frame(right, style="Panel.TFrame")
        self.mode_frame = mode_frame
        mode_frame.pack(fill="x", pady=(12, 4))
        ttk.Label(mode_frame, text="运行模式", style="Panel.TLabel").pack(side="left")
        self.output_mode_var = tk.StringVar(value=self.output_mode_from_settings())
        self.output_mode_combo = ttk.Combobox(
            mode_frame,
            textvariable=self.output_mode_var,
            values=("测试模式", "输出关闭", "真实输出"),
            state="readonly",
            width=12,
        )
        self.output_mode_combo.pack(side="right", fill="x", expand=True, padx=(8, 0))
        self.output_mode_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_control_change())

        self.tip_slot = ttk.Frame(
            right,
            style="Panel.TFrame",
            height=self.TIP_LABEL_HEIGHT,
        )
        self.tip_slot.pack(fill="x")
        self.tip_slot.pack_propagate(False)
        self.tip_label = ttk.Label(
            self.tip_slot,
            text="先用测试模式确认数据正常，再切到真实输出",
            style="Muted.TLabel",
            wraplength=self.SETTINGS_CONTENT_WIDTH,
        )
        self.tip_label.pack(fill="x")

        channel_container = ttk.Frame(right, style="Panel.TFrame")
        channel_container.pack(fill="x", pady=(0, 0))

        channel_frame = ttk.Frame(channel_container, style="Panel.TFrame")
        channel_frame.pack(fill="x", pady=(0, 0))
        ttk.Label(channel_frame, text="通道", style="Panel.TLabel").pack(side="left")
        self.channel_var = tk.StringVar(value="A")
        for value in ("A", "B", "Both"):
            ttk.Radiobutton(
                channel_frame,
                text=value,
                value=value,
                variable=self.channel_var,
                command=self.on_control_change,
                style="Channel.TRadiobutton",
            ).pack(side="left", padx=(10, 0))

        self.channel_b_ratio_var = tk.StringVar(value=f"{self.settings.channel_b_multiplier:.1f}")
        self.channel_b_ratio_entry = ttk.Entry(
            channel_frame,
            textvariable=self.channel_b_ratio_var,
            width=5,
            justify="center",
        )
        self.channel_b_ratio_entry.bind("<Return>", self.on_channel_b_ratio_entry)
        self.channel_b_ratio_entry.bind("<FocusOut>", self.on_channel_b_ratio_entry)

        self.channel_tip_frame = ttk.Frame(channel_container, style="Panel.TFrame")
        self.channel_tip_label = ttk.Label(
            self.channel_tip_frame,
            text="右侧为B通道倍率(0.1-10.0)：B = A × 倍率",
            style="Muted.TLabel",
            wraplength=self.SETTINGS_CONTENT_WIDTH,
        )
        self.channel_tip_label.pack(side="left", fill="x")

        waveform_frame = ttk.Frame(right, style="Panel.TFrame")
        waveform_frame.pack(fill="x", pady=(0, 0))
        self.climax_wave_enabled_var = tk.BooleanVar(value=self.settings.climax_wave_enabled)
        tk.Checkbutton(
            waveform_frame,
            text="启用高潮波",
            variable=self.climax_wave_enabled_var,
            command=self.on_climax_wave_change,
            bg=self.PANEL,
            fg=self.TEXT,
            activebackground=self.PANEL,
            activeforeground=self.TEXT,
            selectcolor=self.PANEL2,
            highlightthickness=0,
            bd=0,
            font=("Microsoft YaHei UI", 10),
        ).pack(anchor="w", pady=(4, 0))
        self.waveform_role_vars = {}
        self.waveform_role_combos = {}
        waveform_roles = (
            ("vibrator", "振动器波形", self.settings.vibrator_waveform_name),
            ("piston", "活塞机波形", self.settings.piston_waveform_name),
            ("climax", "高潮波形", self.settings.climax_waveform_name),
        )
        for role, label, selected in waveform_roles:
            select_row = ttk.Frame(waveform_frame, style="Panel.TFrame")
            select_row.pack(fill="x", pady=(6, 0))
            ttk.Label(select_row, text=label, style="Panel.TLabel").pack(side="left")
            name_var = tk.StringVar(value=selected)
            combo = ttk.Combobox(
                select_row,
                textvariable=name_var,
                values=self.waveform_names(),
                state="readonly",
                width=18,
            )
            combo.pack(side="right", fill="x", expand=True, padx=(8, 0))
            combo.bind("<<ComboboxSelected>>", lambda _event, selected_role=role: self.on_waveform_selected(selected_role))
            self.waveform_role_vars[role] = name_var
            self.waveform_role_combos[role] = combo

        self.slider_vars = {}
        self.add_slider(right, "郊狼上限", "max_coyote_strength", 0, 200, self.settings.max_coyote_strength)
        self.intensity_vars = {}
        self.intensity_entries = {}
        self.add_intensity_row(
            right,
            "振动器强度：",
            (("弱", "vibrator_weak_percent"), ("强", "vibrator_strong_percent")),
        )
        self.add_intensity_row(
            right,
            "活塞机强度：",
            (
                ("弱", "piston_weak_percent"),
                ("中", "piston_medium_percent"),
                ("强", "piston_strong_percent"),
            ),
        )
        self.add_intensity_row(
            right,
            "高潮强度：",
            (("范围", "climax_range_percent"), ("绝顶", "climax_active_percent")),
        )
        self.add_slider(right, "变化速度", "ramp_units_per_second", 1, 100, self.settings.ramp_units_per_second)
        self.add_slider(right, "发送频率", "send_hz", 1, 30, self.settings.send_hz)
        ttk.Button(right, text="重置为默认配置", command=self.reset_default_config).pack(fill="x", pady=(10, 0))

        self.bind_right_scroll_tree(right)

    def on_right_panel_configure(self, _event=None):
        bbox = self.right_canvas.bbox("all")
        self.right_canvas.configure(scrollregion=bbox or (0, 0, 0, 0))
        self.update_right_scroll_state()

    def on_right_canvas_configure(self, event):
        self.right_canvas.itemconfigure(self.right_window, width=event.width)
        self.update_right_scroll_state()

    def bind_right_scroll(self, widget):
        widget.bind("<Enter>", lambda _event: self.right_canvas.bind_all("<MouseWheel>", self.on_right_mousewheel))
        widget.bind("<Leave>", lambda _event: self.right_canvas.unbind_all("<MouseWheel>"))

    def bind_right_scroll_tree(self, widget):
        widget.bind("<MouseWheel>", self.on_right_mousewheel)
        for child in widget.winfo_children():
            self.bind_right_scroll_tree(child)

    def on_right_mousewheel(self, event):
        if not self.right_panel_can_scroll():
            self.right_canvas.yview_moveto(0)
            return "break"
        self.right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def right_panel_can_scroll(self):
        bbox = self.right_canvas.bbox("all")
        if not bbox:
            return False
        return (bbox[3] - bbox[1]) > self.right_canvas.winfo_height() + 1

    def update_right_scroll_state(self):
        if not hasattr(self, "right_scroll"):
            return
        if self.right_panel_can_scroll():
            self.right_scroll.state(["!disabled"])
        else:
            self.right_canvas.yview_moveto(0)
            self.right_scroll.state(["disabled"])

    def set_tip_visible(self, visible):
        if visible:
            if not self.tip_label.winfo_manager():
                self.tip_label.pack(fill="x")
        elif self.tip_label.winfo_manager():
            self.tip_label.pack_forget()

    def toggle_compact(self):
        self.compact_mode = not self.compact_mode
        if self.compact_mode:
            self.right_shell.grid_remove()
            self.main.columnconfigure(0, weight=1)
            self.main.columnconfigure(1, weight=0, minsize=0)
            self.main.columnconfigure(2, weight=0, minsize=0)
            self.compact_button.config(text="显示设置")
            self.root.geometry("560x380")
        else:
            self.right_shell.grid(row=0, column=2, sticky="nsew")
            self.main.columnconfigure(0, weight=1)
            self.main.columnconfigure(1, weight=0, minsize=12)
            self.main.columnconfigure(2, weight=0, minsize=self.SETTINGS_PANEL_WIDTH)
            self.compact_button.config(text="只看图表")
            self.root.geometry("900x610")

    def badge(self, parent, text, color):
        label = tk.Label(parent, text=text, bg=color, fg="#0b0e12", padx=12, pady=5, font=("Microsoft YaHei UI", 10, "bold"))
        label.pack(side="left", padx=(0, 8))
        return label

    def format_setting_value(self, key, value):
        if key in ("max_coyote_strength", "low_suspicion_min_units"):
            return str(int(round(value)))
        text = f"{float(value):.1f}"
        return text.rstrip("0").rstrip(".")

    def add_slider(self, parent, label, key, low, high, value):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        head = ttk.Frame(row, style="Panel.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text=label, style="Panel.TLabel").pack(side="left")
        entry_var = tk.StringVar(value=self.format_setting_value(key, value))
        entry = ttk.Entry(head, textvariable=entry_var, width=8)
        entry.pack(side="right")
        value_label = ttk.Label(head, text=self.format_setting_value(key, value), style="Muted.TLabel")
        value_label.pack(side="right", padx=(0, 8))
        var = tk.DoubleVar(value=value)
        scale = ttk.Scale(row, from_=low, to=high, variable=var, command=lambda _v, k=key: self.on_slider(k))
        scale.pack(fill="x")
        entry.bind("<Return>", lambda _event, k=key: self.on_setting_entry(k))
        entry.bind("<FocusOut>", lambda _event, k=key: self.on_setting_entry(k))
        self.slider_vars[key] = (var, value_label, entry_var, low, high)

    def add_intensity_row(self, parent, label, fields):
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=label, style="Panel.TLabel").pack(side="left")
        for index, (field_label, key) in enumerate(fields):
            ttk.Label(row, text=field_label, style="Panel.TLabel").pack(
                side="left", padx=(6 if index else 4, 0)
            )
            value = int(getattr(self.settings, key))
            value_var = tk.StringVar(value=str(value))
            entry = ttk.Entry(row, textvariable=value_var, width=3, justify="center")
            entry.pack(side="left", padx=(2, 0))
            entry.bind("<Return>", lambda _event, k=key: self.on_intensity_entry(k))
            entry.bind("<FocusOut>", lambda _event, k=key: self.on_intensity_entry(k))
            self.intensity_vars[key] = value_var
            self.intensity_entries[key] = entry

    def normalize_intensity_settings(self):
        defaults = Settings()
        fields = (
            "vibrator_weak_percent",
            "vibrator_strong_percent",
            "piston_weak_percent",
            "piston_medium_percent",
            "piston_strong_percent",
            "climax_range_percent",
            "climax_active_percent",
        )
        for key in fields:
            value = getattr(self.settings, key)
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                setattr(self.settings, key, getattr(defaults, key))

        self.settings.vibrator_strong_percent = max(
            self.settings.vibrator_strong_percent,
            self.settings.vibrator_weak_percent,
        )
        self.settings.piston_medium_percent = max(
            self.settings.piston_medium_percent,
            self.settings.piston_weak_percent,
        )
        self.settings.piston_strong_percent = max(
            self.settings.piston_strong_percent,
            self.settings.piston_medium_percent,
        )
        self.settings.climax_active_percent = max(
            self.settings.climax_active_percent,
            self.settings.climax_range_percent,
        )

    def apply_settings_to_vars(self):
        self.output_mode_var.set(self.output_mode_from_settings())
        self.channel_var.set(self.settings.channel)
        if hasattr(self, "channel_b_ratio_var"):
            self.channel_b_ratio_var.set(f"{self.settings.channel_b_multiplier:.1f}")
        self.update_channel_ui_state()
        self.climax_wave_enabled_var.set(self.settings.climax_wave_enabled)
        for role in ("vibrator", "piston", "climax"):
            self.waveform_role_vars[role].set(self.waveform_role_name(role))
        for key, (var, value_label, entry_var, _low, _high) in self.slider_vars.items():
            value = getattr(self.settings, key)
            var.set(value)
            text = self.format_setting_value(key, value)
            value_label.config(text=text)
            entry_var.set(text)
        for key, value_var in self.intensity_vars.items():
            value_var.set(str(int(getattr(self.settings, key))))

    def output_mode_from_settings(self):
        if self.settings.dry_run:
            return "测试模式"
        if self.settings.output_enabled:
            return "真实输出"
        return "输出关闭"

    def apply_output_mode(self):
        mode = self.output_mode_var.get() if hasattr(self, "output_mode_var") else self.output_mode_from_settings()
        if mode == "真实输出":
            self.settings.dry_run = False
            self.settings.output_enabled = True
        elif mode == "输出关闭":
            self.settings.dry_run = False
            self.settings.output_enabled = False
        else:
            self.settings.dry_run = True
            self.settings.output_enabled = False

    def waveform_names(self):
        return [record["name"] for record in self.waveform_records]

    def get_waveform_record(self, name):
        for record in self.waveform_records:
            if record["name"] == name:
                return record
        return self.waveform_records[0]

    def ensure_waveform_role(self, role, name):
        if any(record["name"] == name for record in self.waveform_records):
            return
        fallback = self.waveform_records[0]["name"]
        setattr(self.settings, f"{role}_waveform_name", fallback)

    def waveform_role_name(self, role):
        return getattr(self.settings, f"{role}_waveform_name")

    def set_waveform_role(self, role, name):
        if not any(record["name"] == name for record in self.waveform_records):
            name = self.waveform_records[0]["name"]
        setattr(self.settings, f"{role}_waveform_name", name)
        setattr(self.settings, f"{role}_waveform_pulses", self.get_waveform_pulses(name))
        setattr(self.settings, f"{role}_waveform_builtin", self.is_builtin_waveform(name))

    def is_builtin_waveform(self, name):
        source = str(self.get_waveform_record(name).get("source", "") or "").strip().lower()
        return source != "user"

    def get_waveform_pulses(self, name):
        return list(self.get_waveform_record(name)["pulses"])

    def refresh_waveform_combo(self):
        names = self.waveform_names()
        for role, combo in self.waveform_role_combos.items():
            combo.configure(values=names)
            if self.waveform_role_name(role) not in names:
                self.set_waveform_role(role, names[0])
            self.waveform_role_vars[role].set(self.waveform_role_name(role))

    def on_waveform_selected(self, role):
        name = self.waveform_role_vars[role].get()
        self.set_waveform_role(role, name)
        self.on_control_change(save=True)

    def on_climax_wave_change(self):
        self.settings.climax_wave_enabled = bool(self.climax_wave_enabled_var.get())
        self.on_control_change(save=True)

    def on_topmost(self):
        self.root.attributes("-topmost", self.topmost_var.get())

    def set_setting_from_value(self, key, value, save=True):
        var, value_label, entry_var, low, high = self.slider_vars[key]
        value = clamp(float(value), low, high)
        if key in ("max_coyote_strength", "low_suspicion_min_units"):
            value = int(round(value))
        var.set(value)
        text = self.format_setting_value(key, value)
        value_label.config(text=text)
        entry_var.set(text)
        setattr(self.settings, key, value)
        self.on_control_change(save=save)

    def on_slider(self, key):
        var, _label, _entry_var, _low, _high = self.slider_vars[key]
        value = var.get()
        self.set_setting_from_value(key, value, save=True)

    def on_setting_entry(self, key):
        var, _value_label, entry_var, _low, _high = self.slider_vars[key]
        raw = entry_var.get().strip().replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            entry_var.set(self.format_setting_value(key, var.get()))
            self.message_label.config(text="请输入数字，例如 80 或 12.5。")
            return
        self.set_setting_from_value(key, value, save=True)

    def intensity_bounds(self, key):
        if key == "vibrator_weak_percent":
            return 0, self.settings.vibrator_strong_percent
        if key == "vibrator_strong_percent":
            return self.settings.vibrator_weak_percent, 100
        if key == "piston_weak_percent":
            return 0, self.settings.piston_medium_percent
        if key == "piston_medium_percent":
            return self.settings.piston_weak_percent, self.settings.piston_strong_percent
        if key == "piston_strong_percent":
            return self.settings.piston_medium_percent, 100
        if key == "climax_range_percent":
            return 0, self.settings.climax_active_percent
        if key == "climax_active_percent":
            return self.settings.climax_range_percent, 100
        return 0, 100

    def intensity_relation_error(self, key):
        if key in ("vibrator_weak_percent", "piston_weak_percent"):
            other = "强档" if key == "vibrator_weak_percent" else "中档"
            return f"输入错误，输入的整数应小于等于{other}。"
        if key in ("vibrator_strong_percent", "piston_medium_percent", "piston_strong_percent", "climax_active_percent"):
            if key == "vibrator_strong_percent":
                other = "弱档"
            elif key == "piston_medium_percent":
                other = "弱档或强档"
            elif key == "piston_strong_percent":
                other = "中档"
            else:
                other = "范围"
            if key == "piston_medium_percent":
                return "输入错误，输入的整数应大于等于弱档且小于等于强档。"
            return f"输入错误，输入的整数应大于等于{other}。"
        return "输入错误，输入的整数应小于等于绝顶。"

    def on_intensity_entry(self, key):
        value_var = self.intensity_vars[key]
        raw = value_var.get().strip()
        lower, upper = self.intensity_bounds(key)
        if not raw.isdecimal():
            value_var.set(str(int(getattr(self.settings, key))))
            self.message_label.config(text=f"输入错误，请输入{lower}-{upper}之间的整数。")
            return

        value = int(raw)
        if value < 0 or value > 100:
            value_var.set(str(int(getattr(self.settings, key))))
            self.message_label.config(text="输入错误，请输入0-100之间的整数。")
            return

        if value < lower or value > upper:
            value_var.set(str(int(getattr(self.settings, key))))
            self.message_label.config(text=self.intensity_relation_error(key))
            return

        setattr(self.settings, key, value)
        save_config(self.settings)

    def reset_default_config(self):
        defaults = Settings()
        fields = (
            "ws_port",
            "udp_port",
            "public_host",
            "dry_run",
            "output_enabled",
            "max_coyote_strength",
            "stale_timeout_seconds",
            "send_hz",
            "channel",
            "channel_b_multiplier",
            "suspicion_deadzone",
            "low_suspicion_threshold",
            "low_suspicion_min_units",
            "suspicion_at_max",
            "output_scale_percent",
            "ramp_units_per_second",
            "vibrator_weak_percent",
            "vibrator_strong_percent",
            "piston_weak_percent",
            "piston_medium_percent",
            "piston_strong_percent",
            "climax_range_percent",
            "climax_active_percent",
            "climax_wave_enabled",
        )
        for key in fields:
            setattr(self.settings, key, getattr(defaults, key))
        for role in ("vibrator", "piston", "climax"):
            self.set_waveform_role(role, getattr(defaults, f"{role}_waveform_name"))
        self.topmost_var.set(False)
        self.on_topmost()
        self.apply_settings_to_vars()
        save_config(self.settings)
        self.message_label.config(text="已重置为默认配置。")

    def update_channel_ui_state(self):
        if not hasattr(self, "channel_b_ratio_entry"):
            return
        if self.channel_var.get() == "Both":
            if not self.channel_b_ratio_entry.winfo_ismapped():
                self.channel_b_ratio_entry.pack(side="left", padx=(8, 0))
            if not self.channel_tip_frame.winfo_ismapped():
                self.channel_tip_frame.pack(fill="x", pady=(2, 2))
        else:
            if self.channel_b_ratio_entry.winfo_ismapped():
                self.channel_b_ratio_entry.pack_forget()
            if self.channel_tip_frame.winfo_ismapped():
                self.channel_tip_frame.pack_forget()

    def on_channel_b_ratio_entry(self, _event=None):
        if not hasattr(self, "channel_b_ratio_var"):
            return
        raw = self.channel_b_ratio_var.get().strip()
        raw = raw.replace("，", ".").replace(",", ".").replace("。", ".")
        try:
            val = float(raw)
        except ValueError:
            self.channel_b_ratio_var.set(f"{self.settings.channel_b_multiplier:.1f}")
            self.message_label.config(text="倍率请输入数字，范围 0.1 ~ 10.0。")
            return
        clamped = clamp(round(val, 1), 0.1, 10.0)
        self.settings.channel_b_multiplier = clamped
        self.channel_b_ratio_var.set(f"{clamped:.1f}")
        self.on_control_change(save=True)

    def on_control_change(self, save=True):
        self.apply_output_mode()
        self.settings.channel = self.channel_var.get()
        self.update_channel_ui_state()
        if hasattr(self, "climax_wave_enabled_var"):
            self.settings.climax_wave_enabled = bool(self.climax_wave_enabled_var.get())
        if hasattr(self, "waveform_role_vars"):
            for role in ("vibrator", "piston", "climax"):
                self.set_waveform_role(role, self.waveform_role_vars[role].get())
        if save:
            save_config(self.settings)

    def auto_start(self):
        if not (self.worker and self.worker.is_alive()):
            self.start_connection()

    def set_qr_generating(self, text="正在生成二维码...", clear_files=True):
        self.qr_path = ""
        self.qr_url = ""
        self.qr_image = None
        if hasattr(self, "qr_box"):
            self.qr_box.config(image="", text=text, bg=self.PANEL2, fg=self.MUTED)
        if hasattr(self, "url_label"):
            self.url_label.config(text="客户端会自动生成二维码")
        with self.state.lock:
            self.state.qr_path = ""
            self.state.qr_url = ""
            self.state.app_bound = False
        if clear_files:
            for generated in (ROOT / "qrcode.png", ROOT / "qrcode-url.txt"):
                try:
                    generated.unlink()
                except FileNotFoundError:
                    pass
                except Exception:
                    pass

    def set_qr_section_visible(self, visible):
        if not hasattr(self, "qr_section") or not hasattr(self, "mode_frame"):
            return
        if visible:
            if not self.qr_section.winfo_manager():
                self.qr_section.pack(fill="x", pady=(12, 10), before=self.mode_frame)
        elif self.qr_section.winfo_manager():
            self.qr_section.pack_forget()

    def start_connection(self):
        self.on_control_change()
        self.set_qr_generating()
        self.worker = BridgeWorker(self.settings, self.state, self.events)
        self.worker.start()
        self.start_button.config(text="停止连接")

    def toggle_start(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.start_button.config(text="开始连接")
            self.message_label.config(text="正在停止客户端...")
            return

        self.start_connection()

    def refresh_qr(self):
        self.set_qr_generating("正在刷新二维码...")
        self.message_label.config(text="正在刷新二维码；如果 App 已连接，需要重新扫码。")
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.start_button.config(text="开始连接")
            self.root.after(500, self.start_connection_when_stopped)
        else:
            self.start_connection()

    def start_connection_when_stopped(self, attempts=0):
        if self.worker and self.worker.is_alive():
            if attempts < 20:
                self.root.after(300, lambda: self.start_connection_when_stopped(attempts + 1))
            else:
                self.message_label.config(text="旧连接还没有完全关闭，请稍等几秒后再点刷新二维码。")
            return
        self.start_connection()

    def panic_stop(self):
        self.output_mode_var.set("输出关闭")
        self.settings.dry_run = False
        self.settings.output_enabled = False
        self.on_control_change()
        if self.worker:
            self.worker.current_units = 0
            self.worker.preview_units = 0
            self.worker.last_sent_units = -1
        with self.state.lock:
            self.state.actual_units = 0
            self.state.preview_units = 0
            self.state.display_units = 0
        self.message_label.config(text="已急停：运行模式已切到输出关闭，目标强度归零。")

    def tick(self):
        self.handle_events()
        self.ensure_qr_loaded()
        self.sample_history()
        self.refresh_status()
        self.draw_chart()
        self.root.after(200, self.tick)

    def handle_events(self):
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "qr":
                self.load_qr(payload)
            elif kind == "error":
                self.message_label.config(text=str(payload))
            elif kind == "feedback":
                self.message_label.config(text=f"App 反馈：{payload}")
            elif kind == "panic":
                self.panic_stop()

    def load_qr(self, path):
        try:
            self.qr_path = path
            image = Image.open(path).convert("RGB")
            image = image.resize((220, 220), Image.Resampling.NEAREST)
            self.qr_image = ImageTk.PhotoImage(image)
            self.qr_box.config(image=self.qr_image, text="", bg="white")
            with self.state.lock:
                self.qr_url = self.state.qr_url
            self.url_label.config(text="二维码已生成。扫不上时可以点“复制链接”，或点“刷新二维码”重来")
        except Exception as exc:
            self.qr_box.config(text=f"二维码显示失败：{exc}")

    def ensure_qr_loaded(self):
        if self.qr_url:
            return
        with self.state.lock:
            path = self.state.qr_path
            url = self.state.qr_url
        if path and Path(path).exists():
            self.load_qr(path)
            return

        fallback_path = ROOT / "qrcode.png"
        fallback_url_path = ROOT / "qrcode-url.txt"
        if fallback_path.exists() and fallback_url_path.exists():
            try:
                url = fallback_url_path.read_text(encoding="utf-8").strip()
            except Exception:
                url = ""
            if url:
                with self.state.lock:
                    self.state.qr_path = str(fallback_path)
                    self.state.qr_url = url
                self.load_qr(str(fallback_path))

    def copy_qr_url(self):
        url = self.qr_url
        if not url:
            with self.state.lock:
                url = self.state.qr_url
        if not url:
            self.message_label.config(text="还没有连接链接。客户端正在自动生成二维码，请稍等。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.message_label.config(text="连接链接已复制，可以粘贴到需要的地方。")

    def sample_history(self):
        now = time.time()
        if now - self.last_sample < 0.5:
            return
        self.last_sample = now
        with self.state.lock:
            intensity_percent = self.state.intensity_percent
            climax_percent = self.state.climax_percent if self.state.has_climax_data else intensity_percent
            units = self.state.display_units
            max_units = self.state.max_units
        self.history.append((now, climax_percent / 100.0, units, max_units))
        if len(self.history) > 240:
            self.history = self.history[-240:]

    def refresh_status(self):
        with self.state.lock:
            app = self.state.app_bound
            game = self.state.game_ok
            game_armed = self.state.game_armed
            dry = self.state.dry_run
            enabled = self.state.output_enabled
            intensity_percent = self.state.intensity_percent
            configured_mode = self.state.configured_mode
            effective_mode = self.state.effective_mode
            vibrator_on = self.state.vibrator_on
            climax_percent = self.state.climax_percent
            climax_active = self.state.climax_active
            piston_on = self.state.piston_on
            piston_random = self.state.piston_random
            piston_configured_mode = self.state.piston_configured_mode
            piston_effective_mode = self.state.piston_effective_mode
            has_piston_data = self.state.has_piston_data
            actual_units = self.state.actual_units
            preview_units = self.state.preview_units
            max_units = self.state.max_units

        self.set_qr_section_visible(not app)
        self.set_tip_visible(not game)
        self.app_badge.config(text="App 已连接" if app else "App 未连接", bg=self.GREEN if app else self.RED)
        if not game:
            self.game_badge.config(text="游戏无数据", bg=self.RED)
        elif not game_armed:
            self.game_badge.config(text="游戏数据暂停(F8)", bg=self.YELLOW)
        else:
            self.game_badge.config(text="游戏数据正常", bg=self.GREEN)
        if dry:
            self.output_badge.config(text="测试模式", bg=self.ORANGE)
        elif enabled:
            self.output_badge.config(text="真实输出", bg=self.GREEN)
        else:
            self.output_badge.config(text="输出关闭", bg=self.RED)
        intensity_percent = clamp(intensity_percent, 0.0, 100.0)
        if dry:
            if self.settings.channel == "Both" and abs(self.settings.channel_b_multiplier - 1.0) > 1e-4:
                b_preview = int(round(clamp(preview_units * self.settings.channel_b_multiplier, 0, 200)))
                output_text = f"模拟 A:{preview_units} B:{b_preview}/{max_units}"
            else:
                output_text = f"模拟 {preview_units}/{max_units}"
        else:
            if self.settings.channel == "Both" and abs(self.settings.channel_b_multiplier - 1.0) > 1e-4:
                b_actual = int(round(clamp(actual_units * self.settings.channel_b_multiplier, 0, 200)))
                output_text = f"郊狼 A:{actual_units} B:{b_actual}/{max_units}"
            else:
                output_text = f"郊狼 {actual_units}/{max_units}"
        vibrator_text = vibrator_display_mode(configured_mode, effective_mode, vibrator_on)
        piston_text = piston_display_mode(
            piston_configured_mode,
            piston_effective_mode,
            piston_on and has_piston_data,
            piston_random,
        )
        if self.settings.climax_wave_enabled:
            climax_text = f" | 高潮 {climax_percent:.0f}%" + (" 开火" if climax_active else "")
        else:
            climax_text = " | 高潮波关"
        self.value_label.config(text=f"振动器 {vibrator_text} | 活塞 {piston_text}{climax_text} | {output_text}")

    def draw_chart(self):
        c = self.chart
        w = max(10, c.winfo_width())
        h = max(10, c.winfo_height())
        c.delete("all")

        pad_l, pad_r, pad_t, pad_b = 54, 62, 28, 30
        plot_w = w - pad_l - pad_r
        plot_h = h - pad_t - pad_b
        if plot_w <= 10 or plot_h <= 10:
            return

        pulses = list(self.settings.vibrator_waveform_pulses or [])
        piston_pulses = list(self.settings.piston_waveform_pulses or [])
        climax_pulses = list(self.settings.climax_waveform_pulses or [])
        climax_enabled = bool(self.settings.climax_wave_enabled)

        with self.state.lock:
            max_units = max(1, self.state.max_units, self.settings.max_coyote_strength)
            intensity_percent = self.state.intensity_percent
            vibrator_on = self.state.vibrator_on
            piston_percent = self.state.piston_intensity_percent
            piston_on = self.state.piston_on
            climax_percent = self.state.climax_percent
            climax_active = self.state.climax_active
            game_armed = self.state.game_armed
            vibrator_strong = self.state.vibrator_strong
            piston_strong = self.state.piston_strong
            dry_run = self.state.dry_run
            coyote_target_units = self.state.coyote_target_units

        if not game_armed:
            intensity_percent = 0.0
            vibrator_on = False
            piston_percent = 0.0
            piston_on = False
            climax_percent = 0.0
            climax_active = False

        worker = self.worker
        layer_epochs = getattr(worker, "layer_epochs", {}) if worker else {}
        preview_clock = getattr(worker, "pulse_time", 0.0) if worker else 0.0
        vibrator_epoch = float(layer_epochs.get("vibrator", preview_clock))
        piston_epoch = float(layer_epochs.get("piston", preview_clock))
        climax_epoch = float(layer_epochs.get("climax", preview_clock))

        preview_layers = []
        if vibrator_on and intensity_percent > 0:
            preview_layers.append((pulses, intensity_percent, 0.0, "vibrator", vibrator_strong, vibrator_epoch))
        if piston_on and piston_percent > 0 and piston_pulses:
            preview_layers.append((piston_pulses, piston_percent, 0.0, "piston", piston_strong, piston_epoch))
        if climax_enabled and (climax_percent > 0 or climax_active) and climax_pulses:
            climax_layer_percent = 100.0 if climax_active else climax_percent
            preview_layers.append((climax_pulses, climax_layer_percent, 0.0, "climax", False, climax_epoch))

        period = max(
            [waveform_period(layer[0]) for layer in preview_layers]
            or [waveform_period(pulses), waveform_period(piston_pulses), waveform_period(climax_pulses), 1.0]
        )
        c.create_rectangle(0, 0, w, h, fill="#0b0e12", outline="")
        for i in range(5):
            percent_value = 100 - i * 25
            units_value = max_units * (1.0 - i / 4)
            y = pad_t + plot_h * i / 4
            c.create_line(pad_l, y, w - pad_r, y, fill="#202834")
            c.create_text(pad_l - 8, y, anchor="e", text=f"{percent_value}%", fill=self.TEXT, font=("Consolas", 8))
            c.create_text(w - pad_r + 8, y, anchor="w", text=f"{units_value:.0f}", fill=self.YELLOW, font=("Consolas", 8))
        for i in range(5):
            x = pad_l + plot_w * i / 4
            t = period * i / 4
            c.create_line(x, pad_t, x, h - pad_b, fill="#151b24")
            c.create_text(x, h - 8, anchor="center", text=f"{t:.1f}s", fill="#667085", font=("Consolas", 8))

        def y_from_percent(percent):
            return pad_t + plot_h * (1.0 - clamp(percent, 0.0, 100.0) / 100.0)

        def y_from_units(units):
            return pad_t + plot_h * (1.0 - clamp(float(units), 0.0, max_units) / max_units)

        def draw_line(points, color, width=2):
            if len(points) >= 4:
                c.create_line(*points, fill=color, width=width, smooth=True)

        samples = max(40, min(180, int(plot_w)))
        vibrator_points = []
        piston_points = []
        climax_points = []
        coyote_points = []
        for i in range(samples):
            t = preview_clock + period * i / max(1, samples - 1)
            x = pad_l + plot_w * i / max(1, samples - 1)
            _vfreq, v_wave = sample_pulse(pulses, t - vibrator_epoch)
            _pfreq, p_wave = sample_pulse(piston_pulses, t - piston_epoch) if piston_pulses else (10, 0)
            _cfreq, c_wave = sample_pulse(climax_pulses, t - climax_epoch) if climax_pulses else (10, 0)
            vibrator_value = intensity_percent * (v_wave / 100.0) if vibrator_on else 0.0
            piston_value = piston_percent * (p_wave / 100.0) if piston_on else 0.0
            climax_value = (
                self.settings.climax_active_percent
                if climax_enabled and climax_active
                else climax_percent * self.settings.climax_range_percent / 100.0 * (c_wave / 100.0)
            )
            if vibrator_on and intensity_percent > 0:
                vibrator_points.extend([x, y_from_percent(vibrator_value)])
            if piston_on and piston_percent > 0:
                piston_points.extend([x, y_from_percent(piston_value)])
            if climax_enabled and (climax_percent > 0 or climax_active):
                climax_points.extend([x, y_from_percent(climax_value)])
            if preview_layers:
                _frame, units = compose_composite_frame(
                    preview_layers,
                    max_units,
                    t,
                    climax_active,
                    self.settings.climax_range_percent,
                    self.settings.climax_active_percent,
                )
                if dry_run:
                    coyote_units = units
                else:
                    coyote_units = units * coyote_target_units / max(1, max_units)
                coyote_points.extend([x, y_from_units(coyote_units)])

        draw_line(vibrator_points, self.PINK, 2)
        draw_line(piston_points, self.PURPLE, 2)
        draw_line(climax_points, self.CYAN, 2)
        draw_line(coyote_points, self.YELLOW, 3)
        c.create_line(pad_l, pad_t, pad_l, h - pad_b, fill="#384150")
        c.create_line(w - pad_r, pad_t, w - pad_r, h - pad_b, fill="#384150")
        c.create_line(pad_l, h - pad_b, w - pad_r, h - pad_b, fill="#384150")
        c.create_text(pad_l, 12, anchor="w", text="百分比", fill=self.TEXT, font=("Microsoft YaHei UI", 8))
        c.create_text(w - pad_r, 12, anchor="e", text=f"郊狼 0-{max_units:.0f}", fill=self.YELLOW, font=("Microsoft YaHei UI", 8))
        if not preview_layers:
            c.create_text(w / 2, h / 2, text="等待振动器 / 活塞机 / 高潮输入", fill="#667085", font=("Microsoft YaHei UI", 9))


def main():
    root = tk.Tk()
    app = CoyoteClientApp(root)
    root.mainloop()
    if app.worker and app.worker.is_alive():
        app.worker.stop()


if __name__ == "__main__":
    main()
