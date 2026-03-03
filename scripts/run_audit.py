import json
import logging

from agents.core.hat_engine import run_all_hats

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Running Eleven Hats Audit...")
    reports = run_all_hats()
    summary = []
    for r in reports:
        findings = []
        for f in r.findings:
            findings.append(
                {
                    "severity": f.severity,
                    "title": f.title,
                    "description": f.description,
                    "recommendation": f.recommendation,
                }
            )
        summary.append({"hat": r.hat, "emoji": r.emoji, "findings": findings})

    print(json.dumps(summary, indent=2))
