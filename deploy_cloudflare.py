"""Deploy static race files to the existing Pages project's production branch."""
from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.request

def project_config():
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    project = os.environ.get("CLOUDFLARE_PROJECT_NAME", "beyondhorsepower-race-data")
    if not token or not re.fullmatch(r"[a-fA-F0-9]{32}", account):
        raise ValueError("Set CLOUDFLARE_API_TOKEN and the 32-character CLOUDFLARE_ACCOUNT_ID in GitHub Secrets")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", project):
        raise ValueError("Invalid Cloudflare Pages project name")
    request = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{project}",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Cloudflare project check returned HTTP {error.code}; verify the token's Pages Edit permission, account and project") from None
    if not data.get("success") or not isinstance(data.get("result"), dict):
        raise RuntimeError("Cloudflare project lookup failed")
    result = data["result"]
    branch = result.get("production_branch")
    if not isinstance(branch, str) or not branch or branch.startswith("-") or "\n" in branch:
        raise ValueError("Cloudflare did not return a usable production branch")
    print(f"Existing Pages project found; production branch: {branch}")
    return project, branch

def deploy(output, check_only=False):
    project, branch = project_config()
    if check_only:
        return
    # Use the actual Pages production branch, which may differ from GitHub's main.
    # Only public/ is deployed; Python, workflow files and credentials stay outside it.
    subprocess.run(["npx", "--yes", "wrangler@4", "pages", "deploy", output,
                    "--project-name=" + project, "--branch=" + branch], check=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="public")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    deploy(args.output, args.check_only)
