"""Génère les clés VAPID pour les notifications push.

Usage:
    python scripts/generate_vapid_keys.py

Copier les deux valeurs dans les variables d'environnement Railway :
    VAPID_PRIVATE_KEY et VAPID_PUBLIC_KEY (+ VAPID_CLAIMS_EMAIL).
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def main():
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_value = private_key.private_numbers().private_value
    private_b64 = _b64url(private_value.to_bytes(32, "big"))
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = _b64url(public_bytes)
    print("VAPID_PRIVATE_KEY=" + private_b64)
    print("VAPID_PUBLIC_KEY=" + public_b64)


if __name__ == "__main__":
    main()
