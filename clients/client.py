import os
import requests
import logging

class SecurePactClient:
    def __init__(self, base_url=None, timeout=5):
        self.base_url = base_url or os.getenv("SECUREPACT_URL", "http://localhost:9000")
        self.timeout = timeout

    def verify_request(self, headers, ip, body: bytes):
        try:
            response = requests.post(
                f"{self.base_url}/validate",
                json={
                    "headers": dict(headers),
                    "ip": ip,
                    "body": body.decode("utf-8")  # optional: base64.encode if binary
                },
                timeout=self.timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"[SecurePactClient] Firewall check failed: {e}")
            return {"error": "SecurePact verification failed"}

# --------------------------------------------------

class IntellicoreAGIClient:
    def __init__(self, base_url=None):
        self.base_url = base_url or os.getenv("INTELLICORE_URL", "http://localhost:9010")

    def run_inference(self, query: str, context: dict = None):
        try:
            payload = {
                "query": query,
                "context": context or {}
            }
            response = requests.post(f"{self.base_url}/infer", json=payload)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"[IntellicoreAGIClient] Inference failed: {e}")
            return {"error": "Intellicore AGI inference failed"}

# --------------------------------------------------

class OptisysClient:
    def __init__(self, base_url=None):
        self.base_url = base_url or os.getenv("OPTISYS_URL", "http://localhost:9020")

    def optimize_payload(self, data: dict):
        try:
            response = requests.post(f"{self.base_url}/optimize", json=data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"[OptisysClient] Optimization failed: {e}")
            return {"error": "Optisys optimization failed"}
