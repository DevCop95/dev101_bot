"""Generate the offline Enterprise ATT&CK catalog from a fixed official release."""

import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen

ATTACK_VERSION = "17.1"
SOURCE_URL = (
    "https://raw.githubusercontent.com/mitre-attack/attack-stix-data/"
    f"v{ATTACK_VERSION}/enterprise-attack/enterprise-attack.json"
)
OUTPUT = Path(__file__).resolve().parents[1] / "intelligence" / "attack_catalog.json"


def build_catalog(raw):
    bundle = json.loads(raw)
    if bundle.get("type") != "bundle" or not isinstance(bundle.get("objects"), list):
        raise ValueError("Expected official STIX bundle")
    techniques, tactics = {}, {}
    for obj in bundle["objects"]:
        if obj.get("type") not in ("attack-pattern", "x-mitre-tactic"):
            continue
        if "enterprise-attack" not in obj.get("x_mitre_domains", []):
            continue
        target = techniques if obj["type"] == "attack-pattern" else tactics
        pattern = r'T\d{4}(?:\.\d{3})?' if obj["type"] == "attack-pattern" else r'TA\d{4}'
        for ref in obj.get("external_references", []):
            identifier = ref.get("external_id", "")
            if ref.get("source_name") == "mitre-attack" and re.fullmatch(pattern, identifier):
                name = obj["name"]
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("Missing official ATT&CK name")
                if identifier in target and target[identifier] != name:
                    raise ValueError(f"Conflicting ATT&CK ID: {identifier}")
                target[identifier] = name
    # Guard against accidentally publishing a partial collection or wrong domain.
    if len(techniques) < 600 or len(tactics) < 14:
        raise ValueError("Incomplete Enterprise ATT&CK catalog")
    return {
        "attack_version": ATTACK_VERSION,
        "domain": "enterprise-attack",
        "source_url": SOURCE_URL,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "includes_revoked_deprecated": True,
        "technique_count": len(techniques),
        "techniques": techniques,
        "tactics": tactics,
    }


def main():
    with urlopen(SOURCE_URL, timeout=60) as response:
        raw = response.read(60 * 1024 * 1024 + 1)
    if len(raw) > 60 * 1024 * 1024:
        raise ValueError("ATT&CK bundle exceeds size limit")
    catalog = build_catalog(raw)
    OUTPUT.write_bytes((json.dumps(catalog, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(f"Enterprise ATT&CK v{ATTACK_VERSION}: {catalog['technique_count']} techniques -> {OUTPUT}")


if __name__ == "__main__":
    main()
