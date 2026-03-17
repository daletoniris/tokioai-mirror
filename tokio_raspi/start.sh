#!/bin/bash
# TokioAI Raspi — launcher script
# Ensures correct Wayland environment for pygame

export XDG_RUNTIME_DIR=/run/user/1000
export WAYLAND_DISPLAY=wayland-0
export SDL_VIDEODRIVER=wayland

cd /home/mrmoz
exec python3 -m tokio_raspi --api "$@"
