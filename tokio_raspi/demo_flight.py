"""
TokioAI Demo Flight — Pre-programmed drone flight patterns for Ekoparty.

Patterns use discrete Tello commands (forward, back, cw, ccw, up, down, flip)
which are reliable and impressive for live demos.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import requests

PROXY_URL = "http://127.0.0.1:5001"
CMD_TIMEOUT = 12


class DemoFlight:
    """Execute pre-programmed demo flight patterns via drone proxy."""

    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._current_pattern = ""
        self._step = 0
        self._total_steps = 0
        self._error = ""

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_pattern(self) -> str:
        return self._current_pattern

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "pattern": self._current_pattern,
            "step": self._step,
            "total_steps": self._total_steps,
            "error": self._error,
        }

    def cancel(self):
        self._running = False

    def execute(self, pattern: str = "demo", speed: int = 2):
        patterns = {
            "demo": self._pattern_demo,
            "circle": self._pattern_circle,
            "figure8": self._pattern_figure8,
            "speed_run": self._pattern_speed_run,
            "spiral_up": self._pattern_spiral_up,
            "wave": self._pattern_wave,
            "show_off": self._pattern_show_off,
        }
        func = patterns.get(pattern)
        if not func:
            raise ValueError(f"Unknown pattern: {pattern}. Valid: {list(patterns.keys())}")

        self._current_pattern = pattern
        self._error = ""
        self._running = True
        self._thread = threading.Thread(target=self._run, args=(func, speed), daemon=True)
        self._thread.start()

    def _run(self, func, speed: int):
        try:
            func(speed)
        except Exception as e:
            self._error = str(e)
            print(f"[DemoFlight] Error: {e}")
        finally:
            self._running = False
            self._current_pattern = ""

    def _cmd(self, command: str, params: dict = None) -> bool:
        """Send command to drone proxy. Returns True on success."""
        if not self._running:
            return False
        self._step += 1
        try:
            payload = {"command": command}
            if params:
                payload["params"] = params
            r = requests.post(f"{PROXY_URL}/drone/command", json=payload, timeout=CMD_TIMEOUT)
            data = r.json()
            if data.get("blocked"):
                print(f"[DemoFlight] Command blocked: {command}")
                return False
            return r.status_code == 200
        except Exception as e:
            print(f"[DemoFlight] Command failed: {command} — {e}")
            return False

    def _move(self, direction: str, distance: int) -> bool:
        return self._cmd("move", {"direction": direction, "distance": distance})

    def _rotate(self, direction: str, degrees: int) -> bool:
        return self._cmd("rotate", {"direction": direction, "degrees": degrees})

    def _flip(self, direction: str) -> bool:
        return self._cmd("flip", {"direction": direction})

    def _pause(self, seconds: float = 1.0):
        """Pause between moves. Checks for cancellation."""
        end = time.time() + seconds
        while time.time() < end and self._running:
            time.sleep(0.1)

    # --- Patterns ---

    def _pattern_demo(self, speed: int):
        """Classic demo: rise, look around, approach, flip, return."""
        steps = [
            ("up", 50), ("pause", 1.5),
            ("cw", 90), ("pause", 0.5), ("ccw", 180), ("pause", 0.5), ("cw", 90),
            ("forward", 80), ("pause", 1),
            ("flip_f",), ("pause", 1.5),
            ("back", 80), ("pause", 0.5),
            ("cw", 360), ("pause", 1),
            ("down", 30),
        ]
        self._total_steps = len(steps)
        self._step = 0
        for step in steps:
            if not self._running:
                break
            action = step[0]
            if action == "pause":
                self._pause(step[1])
            elif action == "flip_f":
                self._flip("f")
            elif action in ("cw", "ccw"):
                self._rotate(action, step[1])
            elif action in ("up", "down", "forward", "back", "left", "right"):
                self._move(action, step[1])
            self._pause(0.3)

    def _pattern_circle(self, speed: int):
        """Fly in a circle using forward + rotate segments."""
        seg_dist = 30 + speed * 5  # 35-45cm per segment
        seg_angle = 30  # 12 segments = 360 degrees
        segments = 12
        self._total_steps = segments
        self._step = 0

        self._move("up", 40)
        self._pause(0.5)

        for _ in range(segments):
            if not self._running:
                break
            self._move("forward", seg_dist)
            self._rotate("cw", seg_angle)
            self._pause(0.2)

    def _pattern_figure8(self, speed: int):
        """Figure-8 pattern: two circles in opposite directions."""
        seg_dist = 30
        seg_angle = 45  # 8 segments per half
        self._total_steps = 16
        self._step = 0

        self._move("up", 40)
        self._pause(0.5)

        # First circle (CW)
        for _ in range(8):
            if not self._running:
                break
            self._move("forward", seg_dist)
            self._rotate("cw", seg_angle)
            self._pause(0.2)

        # Second circle (CCW)
        for _ in range(8):
            if not self._running:
                break
            self._move("forward", seg_dist)
            self._rotate("ccw", seg_angle)
            self._pause(0.2)

    def _pattern_speed_run(self, speed: int):
        """Fast forward-back with flips."""
        dist = 60 + speed * 20
        self._total_steps = 8
        self._step = 0

        self._move("up", 60)
        self._pause(0.5)
        self._move("forward", dist)
        self._pause(0.3)
        self._flip("f")
        self._pause(1)
        self._move("back", dist)
        self._pause(0.3)
        self._flip("b")
        self._pause(1)
        self._move("left", dist)
        self._pause(0.3)
        self._move("right", dist)
        self._pause(0.3)
        self._move("down", 40)

    def _pattern_spiral_up(self, speed: int):
        """Spiral upward then descend."""
        self._total_steps = 10
        self._step = 0

        for i in range(8):
            if not self._running:
                break
            self._move("up", 20)
            self._move("forward", 25)
            self._rotate("cw", 45)
            self._pause(0.2)

        self._pause(1)
        # Come back down
        self._move("down", 120)
        self._pause(0.5)

    def _pattern_wave(self, speed: int):
        """Sine wave: alternating up-forward, down-forward."""
        self._total_steps = 8
        self._step = 0

        for i in range(4):
            if not self._running:
                break
            self._move("up", 40)
            self._move("forward", 40)
            self._pause(0.2)
            self._move("down", 40)
            self._move("forward", 40)
            self._pause(0.2)

    def _pattern_show_off(self, speed: int):
        """Full show: combines multiple moves and all 4 flip directions."""
        self._total_steps = 16
        self._step = 0

        # Rise
        self._move("up", 70)
        self._pause(1)

        # Look around dramatically
        self._rotate("cw", 180)
        self._pause(0.5)
        self._rotate("ccw", 360)
        self._pause(0.5)
        self._rotate("cw", 180)
        self._pause(1)

        # Approach
        self._move("forward", 60)
        self._pause(0.5)

        # All 4 flips
        for d in ["f", "b", "l", "r"]:
            if not self._running:
                break
            self._flip(d)
            self._pause(1.5)

        # Return
        self._move("back", 60)
        self._pause(0.5)

        # Final spin
        self._rotate("cw", 360)
        self._pause(0.5)
        self._move("down", 50)
