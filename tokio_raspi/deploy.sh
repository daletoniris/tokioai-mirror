#!/bin/bash
# Deploy TokioAI Raspi app to Raspberry Pi
# Usage: ./deploy.sh [raspi-ip]

RASPI_IP="${1:?Usage: ./deploy.sh <raspi-ip>}"
RASPI_USER="${RASPI_SSH_USER:-mrmoz}"
SSH_KEY="$HOME/.ssh/id_rsa_raspberry"
REMOTE_DIR="/home/mrmoz/tokio_raspi"

echo "=== TokioAI Raspi Deploy ==="
echo "Target: ${RASPI_USER}@${RASPI_IP}:${REMOTE_DIR}"

# Create remote directory
ssh -i "$SSH_KEY" "${RASPI_USER}@${RASPI_IP}" "mkdir -p ${REMOTE_DIR}"

# Sync files
scp -i "$SSH_KEY" -r \
    "$(dirname "$0")"/*.py \
    "${RASPI_USER}@${RASPI_IP}:${REMOTE_DIR}/"

echo ""
echo "Deployed! Run on Raspi with:"
echo "  cd /home/mrmoz && python3 -m tokio_raspi --api"
echo "  python3 -m tokio_raspi --demo --windowed  # test without camera"
echo ""
echo "API will be at http://${RASPI_IP}:5000/"
