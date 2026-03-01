#!/usr/bin/env python3
"""Tail nginx access.log and push JSON lines to Kafka."""
import json, os, time, sys, subprocess
from kafka import KafkaProducer
KAFKA = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
TOPIC = os.getenv("KAFKA_TOPIC_WAF_LOGS", "waf-logs")
LOG = os.getenv("MODSEC_LOG_PATH", "/logs/waf-access.log")
def main():
    print(f"[log-processor] Waiting for {LOG}...")
    while not os.path.exists(LOG):
        time.sleep(2)
    print(f"[log-processor] Found {LOG}, connecting to Kafka at {KAFKA}...")
    producer = None
    for i in range(30):
        try:
            producer = KafkaProducer(bootstrap_servers=KAFKA.split(","),
                                     value_serializer=lambda v: json.dumps(v).encode())
            break
        except Exception as e:
            print(f"[log-processor] Kafka not ready ({e}), retrying...")
            time.sleep(5)
    if not producer:
        print("[log-processor] Failed to connect to Kafka"); sys.exit(1)
    print(f"[log-processor] Connected. Tailing {LOG}...")
    # Use subprocess tail -F for robust following (handles log rotation)
    proc = subprocess.Popen(["tail", "-n", "0", "-F", LOG],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True)
    count = 0
    for line in proc.stdout:
        line = line.strip()
        if not line: continue
        try:
            data = json.loads(line)
            producer.send(TOPIC, value=data)
            count += 1
            if count % 100 == 0:
                print(f"[log-processor] Sent {count} events to Kafka")
        except json.JSONDecodeError:
            pass  # skip non-JSON lines
        except Exception as e:
            print(f"[log-processor] Error: {e}")
if __name__ == "__main__": main()
