#!/usr/bin/env bash
# KYA (Know Your Agent) — generate TLS certificates for inter-service mTLS
set -euo pipefail

CERT_DIR="${CERT_DIR:-/app/security/certs}"
mkdir -p "$CERT_DIR"

echo "[KYA] Generating CA key and certificate..."
openssl genrsa -out "$CERT_DIR/ca.key" 4096
openssl req -new -x509 -days 3650 -key "$CERT_DIR/ca.key" \
    -out "$CERT_DIR/ca.crt" \
    -subj "/CN=SovereignOS-CA/O=SovereignOS/C=US"

for SERVICE in gateway-node agent-executor memory-core; do
    echo "[KYA] Generating cert for $SERVICE..."
    openssl genrsa -out "$CERT_DIR/$SERVICE.key" 2048
    openssl req -new -key "$CERT_DIR/$SERVICE.key" \
        -out "$CERT_DIR/$SERVICE.csr" \
        -subj "/CN=$SERVICE/O=SovereignOS/C=US"
    openssl x509 -req -days 365 \
        -in "$CERT_DIR/$SERVICE.csr" \
        -CA "$CERT_DIR/ca.crt" \
        -CAkey "$CERT_DIR/ca.key" \
        -CAcreateserial \
        -out "$CERT_DIR/$SERVICE.crt"
    rm "$CERT_DIR/$SERVICE.csr"
done

echo "[KYA] Certificate generation complete. Certs in $CERT_DIR"
