#!/usr/bin/env python3
"""
Tokio Coffee — Touchscreen UI for Raspberry Pi HDMI display.

Full-screen pygame interface with:
  - Recipe selection via touch
  - Tokio emotional face animations
  - Brewing progress visualization
  - Status and history display

Run: python3 coffee_ui.py
"""
from __future__ import annotations

import json
import math
import os
import sys
import threading
import time
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pygame
from tokio_agent.engine.tools.builtin.coffee_tools import (
    coffee_control,
    RECIPES,
    TOKIO_EMOTIONS,
    BREWING_QUOTES,
)
import random

# ── Display Config ───────────────────────────────────────────────────────────

SCREEN_W = int(os.environ.get("COFFEE_SCREEN_W", "1024"))
SCREEN_H = int(os.environ.get("COFFEE_SCREEN_H", "600"))
FPS = 30

# ── Colors ───────────────────────────────────────────────────────────────────

BG_DARK = (15, 15, 25)
BG_CARD = (30, 30, 50)
BG_CARD_HOVER = (45, 45, 70)
TEXT_WHITE = (240, 240, 240)
TEXT_DIM = (140, 140, 160)
ACCENT_CYAN = (78, 205, 196)
ACCENT_RED = (255, 107, 107)
ACCENT_ORANGE = (255, 152, 0)
ACCENT_GREEN = (139, 195, 74)
BORDER_COLOR = (60, 60, 90)


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert hex color to RGB tuple."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ── UI State ─────────────────────────────────────────────────────────────────

class CoffeeUI:
    """Main UI controller for the Tokio Coffee touchscreen."""

    def __init__(self):
        pygame.init()
        pygame.mouse.set_visible(True)

        # Try fullscreen, fallback to windowed
        try:
            info = pygame.display.Info()
            if info.current_w > 0 and info.current_h > 0:
                self.screen_w = info.current_w
                self.screen_h = info.current_h
            else:
                self.screen_w = SCREEN_W
                self.screen_h = SCREEN_H
            self.screen = pygame.display.set_mode(
                (self.screen_w, self.screen_h),
                pygame.FULLSCREEN | pygame.HWSURFACE | pygame.DOUBLEBUF,
            )
        except Exception:
            self.screen_w = SCREEN_W
            self.screen_h = SCREEN_H
            self.screen = pygame.display.set_mode((self.screen_w, self.screen_h))

        pygame.display.set_caption("Tokio Coffee ☕")
        self.clock = pygame.time.Clock()

        # Fonts
        self.font_huge = pygame.font.SysFont("monospace", 72, bold=True)
        self.font_big = pygame.font.SysFont("monospace", 42, bold=True)
        self.font_medium = pygame.font.SysFont("monospace", 28)
        self.font_small = pygame.font.SysFont("monospace", 20)
        self.font_tiny = pygame.font.SysFont("monospace", 16)

        # State
        self.state = "menu"  # menu | brewing | done
        self.current_emotion = TOKIO_EMOTIONS["idle"]
        self.current_mood = "idle"
        self.brew_result: Optional[dict] = None
        self.brew_thread: Optional[threading.Thread] = None
        self.brew_progress = 0.0
        self.brew_start_time = 0.0
        self.brew_total_time = 0.0
        self.brew_recipe_id = ""
        self.animation_tick = 0
        self.quote = random.choice(BREWING_QUOTES)
        self.scroll_offset = 0

        # Recipe buttons layout
        self.recipe_buttons: list = []
        self._build_recipe_buttons()

    def _build_recipe_buttons(self):
        """Calculate recipe button positions."""
        self.recipe_buttons = []
        recipes = list(RECIPES.items())
        cols = 4
        rows = 2
        margin = 15
        start_y = 200
        btn_w = (self.screen_w - margin * (cols + 1)) // cols
        btn_h = (self.screen_h - start_y - 80 - margin * (rows + 1)) // rows

        for i, (rid, recipe) in enumerate(recipes):
            col = i % cols
            row = i // cols
            x = margin + col * (btn_w + margin)
            y = start_y + row * (btn_h + margin)
            self.recipe_buttons.append({
                "rect": pygame.Rect(x, y, btn_w, btn_h),
                "id": rid,
                "recipe": recipe,
            })

    def _draw_text_centered(self, text: str, font, color, y: int,
                            x: Optional[int] = None):
        """Draw centered text."""
        surface = font.render(text, True, color)
        rect = surface.get_rect()
        rect.centerx = x if x else self.screen_w // 2
        rect.centery = y
        self.screen.blit(surface, rect)

    def _draw_rounded_rect(self, rect: pygame.Rect, color, radius=12,
                           border_color=None, border_width=2):
        """Draw a rounded rectangle."""
        pygame.draw.rect(self.screen, color, rect, border_radius=radius)
        if border_color:
            pygame.draw.rect(self.screen, border_color, rect, width=border_width,
                             border_radius=radius)

    def _animate_face(self, face: str, color_hex: str, animation: str,
                      center_y: int):
        """Draw Tokio's animated face."""
        t = self.animation_tick / FPS
        color = hex_to_rgb(color_hex)

        # Animation offsets
        offset_x = 0
        offset_y = 0
        scale = 1.0

        if animation == "bounce":
            offset_y = int(math.sin(t * 4) * 15)
        elif animation == "shake":
            offset_x = int(math.sin(t * 12) * 8)
        elif animation == "pulse":
            scale = 1.0 + math.sin(t * 3) * 0.05
        elif animation == "float":
            offset_y = int(math.sin(t * 2) * 8)
        elif animation == "vibrate":
            offset_x = int(math.sin(t * 20) * 5)
            offset_y = int(math.cos(t * 20) * 5)
        elif animation == "spin":
            offset_x = int(math.sin(t * 3) * 20)
        elif animation == "breathe":
            scale = 1.0 + math.sin(t * 1.5) * 0.03
        elif animation == "glow":
            intensity = int(abs(math.sin(t * 2)) * 60)
            color = tuple(min(255, c + intensity) for c in color)
        elif animation == "pour":
            offset_y = int(abs(math.sin(t * 2)) * 10)
        elif animation == "celebrate":
            offset_y = int(abs(math.sin(t * 6)) * 20)
            offset_x = int(math.sin(t * 4) * 10)
        elif animation == "glitch":
            if random.random() < 0.1:
                offset_x = random.randint(-15, 15)
                offset_y = random.randint(-5, 5)
        elif animation == "fade":
            alpha = int(180 + math.sin(t * 2) * 75)
            color = tuple(min(255, max(0, int(c * alpha / 255))) for c in color)

        # Draw glow circle behind face
        glow_radius = int(60 * scale)
        glow_surface = pygame.Surface((glow_radius * 4, glow_radius * 4), pygame.SRCALPHA)
        for r in range(glow_radius, 0, -2):
            alpha = int(30 * (r / glow_radius))
            pygame.draw.circle(glow_surface, (*color, alpha),
                               (glow_radius * 2, glow_radius * 2), r)
        glow_rect = glow_surface.get_rect(
            center=(self.screen_w // 2 + offset_x, center_y + offset_y)
        )
        self.screen.blit(glow_surface, glow_rect)

        # Draw face text
        font_size = int(72 * scale)
        face_font = pygame.font.SysFont("monospace", font_size, bold=True)
        self._draw_text_centered(
            face, face_font, color,
            center_y + offset_y,
            self.screen_w // 2 + offset_x,
        )

    def _draw_menu(self):
        """Draw the recipe selection menu."""
        self.screen.fill(BG_DARK)

        # Header
        self._draw_text_centered("TOKIO COFFEE", self.font_big, ACCENT_CYAN, 40)

        # Tokio idle face
        self._animate_face(
            self.current_emotion["face"],
            self.current_emotion["color"],
            self.current_emotion["animation"],
            center_y=110,
        )

        # Idle message
        self._draw_text_centered(
            self.current_emotion["message"],
            self.font_small, TEXT_DIM, 160,
        )

        # Recipe buttons
        mouse_pos = pygame.mouse.get_pos()
        for btn in self.recipe_buttons:
            rect = btn["rect"]
            recipe = btn["recipe"]
            hovered = rect.collidepoint(mouse_pos)

            bg = BG_CARD_HOVER if hovered else BG_CARD
            border = ACCENT_CYAN if hovered else BORDER_COLOR
            self._draw_rounded_rect(rect, bg, border_color=border)

            # Emoji
            self._draw_text_centered(
                recipe["emoji"], self.font_big,
                TEXT_WHITE, rect.y + 35, rect.centerx,
            )
            # Name
            self._draw_text_centered(
                recipe["name_es"], self.font_small,
                TEXT_WHITE if hovered else TEXT_DIM,
                rect.y + 75, rect.centerx,
            )
            # Details
            details = []
            if recipe["water_ml"] > 0:
                details.append(f"agua:{recipe['water_ml']}ml")
            if recipe["milk_ml"] > 0:
                details.append(f"leche:{recipe['milk_ml']}ml")
            self._draw_text_centered(
                " ".join(details), self.font_tiny,
                TEXT_DIM, rect.y + 100, rect.centerx,
            )

        # Footer
        self._draw_text_centered(
            "Tocá una receta para preparar  |  ESC para salir",
            self.font_tiny, TEXT_DIM, self.screen_h - 20,
        )

    def _draw_brewing(self):
        """Draw the brewing animation screen."""
        self.screen.fill(BG_DARK)

        recipe = RECIPES.get(self.brew_recipe_id, {})
        name = recipe.get("name_es", "Café")
        emoji = recipe.get("emoji", "☕")

        # Title
        self._draw_text_centered(
            f"Preparando {emoji} {name}...",
            self.font_big, ACCENT_ORANGE, 50,
        )

        # Animated face
        brewing_emotion = TOKIO_EMOTIONS["brewing"]
        self._animate_face(
            brewing_emotion["face"],
            brewing_emotion["color"],
            brewing_emotion["animation"],
            center_y=180,
        )

        # Progress bar
        bar_w = self.screen_w - 200
        bar_h = 30
        bar_x = 100
        bar_y = 280

        # Background
        pygame.draw.rect(self.screen, BG_CARD,
                         (bar_x, bar_y, bar_w, bar_h), border_radius=15)

        # Progress fill
        if self.brew_total_time > 0:
            elapsed = time.time() - self.brew_start_time
            self.brew_progress = min(1.0, elapsed / self.brew_total_time)
        fill_w = int(bar_w * self.brew_progress)
        if fill_w > 0:
            pygame.draw.rect(self.screen, hex_to_rgb(ACCENT_ORANGE),
                             (bar_x, bar_y, fill_w, bar_h), border_radius=15)

        # Percentage
        pct = int(self.brew_progress * 100)
        self._draw_text_centered(
            f"{pct}%", self.font_medium, TEXT_WHITE, bar_y + bar_h + 30,
        )

        # Quote
        self._draw_text_centered(
            self.quote, self.font_small, TEXT_DIM, 400,
        )

        # Water/milk info
        water_ml = recipe.get("water_ml", 0)
        milk_ml = recipe.get("milk_ml", 0)
        info_parts = []
        if water_ml > 0:
            info_parts.append(f"Agua: {water_ml}ml")
        if milk_ml > 0:
            info_parts.append(f"Leche: {milk_ml}ml")
        self._draw_text_centered(
            " | ".join(info_parts), self.font_small, ACCENT_CYAN, 460,
        )

    def _draw_done(self):
        """Draw the completion screen."""
        self.screen.fill(BG_DARK)

        if not self.brew_result:
            return

        recipe = RECIPES.get(self.brew_recipe_id, {})
        name = recipe.get("name_es", "Café")
        emoji = recipe.get("emoji", "☕")

        # Celebration face
        done_emotion = TOKIO_EMOTIONS["done"]
        self._animate_face(
            done_emotion["face"],
            done_emotion["color"],
            done_emotion["animation"],
            center_y=120,
        )

        # Done message
        self._draw_text_centered(
            f"{emoji} {name} listo!",
            self.font_big, ACCENT_GREEN, 230,
        )

        # Recipe emotion message
        mood = recipe.get("tokio_mood", "happy")
        emotion = TOKIO_EMOTIONS.get(mood, TOKIO_EMOTIONS["happy"])
        self._draw_text_centered(
            emotion["message"],
            self.font_medium, hex_to_rgb(emotion["color"]), 300,
        )

        # Quote
        self._draw_text_centered(
            self.brew_result.get("quote", ""),
            self.font_small, TEXT_DIM, 370,
        )

        # Stats
        brew_time = self.brew_result.get("brew_time_seconds", 0)
        self._draw_text_centered(
            f"Tiempo: {brew_time}s | Simulación: {'Sí' if self.brew_result.get('simulation') else 'No'}",
            self.font_tiny, TEXT_DIM, 430,
        )

        # Touch to continue
        alpha = int(128 + math.sin(self.animation_tick / FPS * 2) * 127)
        hint_color = (alpha, alpha, alpha)
        self._draw_text_centered(
            "Tocá la pantalla para volver al menú",
            self.font_small, hint_color, self.screen_h - 60,
        )

    def _start_brew(self, recipe_id: str):
        """Start brewing in a background thread."""
        self.state = "brewing"
        self.brew_recipe_id = recipe_id
        self.brew_progress = 0.0
        self.brew_result = None
        self.quote = random.choice(BREWING_QUOTES)

        recipe = RECIPES.get(recipe_id, {})
        from tokio_agent.engine.tools.builtin.coffee_tools import (
            WATER_FLOW_RATE, MILK_FLOW_RATE,
        )
        water_time = recipe.get("water_ml", 0) / WATER_FLOW_RATE
        milk_time = recipe.get("milk_ml", 0) / MILK_FLOW_RATE
        self.brew_total_time = water_time + milk_time
        self.brew_start_time = time.time()

        def _brew():
            result_json = coffee_control("brew", {"recipe": recipe_id})
            self.brew_result = json.loads(result_json)
            self.brew_progress = 1.0
            time.sleep(1.0)
            self.state = "done"

        self.brew_thread = threading.Thread(target=_brew, daemon=True)
        self.brew_thread.start()

    def run(self):
        """Main event loop."""
        running = True
        self.current_emotion = TOKIO_EMOTIONS["idle"]

        while running:
            self.animation_tick += 1

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_F11:
                        pygame.display.toggle_fullscreen()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if self.state == "menu":
                        for btn in self.recipe_buttons:
                            if btn["rect"].collidepoint(event.pos):
                                self._start_brew(btn["id"])
                                break
                    elif self.state == "done":
                        self.state = "menu"
                        self.current_emotion = TOKIO_EMOTIONS["idle"]

            # Draw current state
            if self.state == "menu":
                self._draw_menu()
            elif self.state == "brewing":
                self._draw_brewing()
            elif self.state == "done":
                self._draw_done()

            pygame.display.flip()
            self.clock.tick(FPS)

        pygame.quit()


if __name__ == "__main__":
    ui = CoffeeUI()
    ui.run()
