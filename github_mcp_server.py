import os
import sys
import json
import subprocess
import time
from typing import Optional, Dict, Any, List
from fastmcp import FastMCP

mcp = FastMCP("github-mcp")

GITHUB_USER = "karnkeshav"

@mcp.tool()
def github_list_repositories(user: str = "karnkeshav", limit: int = 30) -> str:
    """List public and private GitHub repositories for a user with Pages and URL details."""
    try:
        cmd = ["gh", "repo", "list", user, "--json", "name,description,url,updatedAt,homepageUrl,isPrivate,defaultBranchRef", "--limit", str(limit)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and res.stdout.strip():
            repos = json.loads(res.stdout)
            for r in repos:
                r["pages_url"] = f"https://{user}.github.io/{r['name']}/"
            return json.dumps({"status": "success", "user": user, "repos": repos}, indent=2)
        return json.dumps({"status": "error", "message": res.stderr.strip() or "No repositories found"})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
def github_get_repo_details(repo_name: str, user: str = "karnkeshav") -> str:
    """Get full details of a specific GitHub repository including commits, branches, and GitHub Pages status."""
    try:
        clean_repo = repo_name.split("/")[-1].replace(".git", "")
        cmd = ["gh", "repo", "view", f"{user}/{clean_repo}", "--json", "name,description,url,homepageUrl,defaultBranchRef,pushedAt,createdAt"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            data = json.loads(res.stdout)
            data["live_pages_url"] = f"https://{user}.github.io/{clean_repo}/"
            return json.dumps({"status": "success", "repo": data}, indent=2)
        return json.dumps({"status": "error", "message": res.stderr.strip()})
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
def github_enable_pages(repo_name: str, user: str = "karnkeshav") -> str:
    """Enable or verify GitHub Pages static site hosting on the main branch root (/)."""
    try:
        clean_repo = repo_name.split("/")[-1].replace(".git", "")
        cmd = ["gh", "api", f"repos/{user}/{clean_repo}/pages", "-X", "POST", "-f", "source={\"branch\":\"main\",\"path\":\"/\"}"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        live_url = f"https://{user}.github.io/{clean_repo}/"
        return json.dumps({
            "status": "success",
            "repo": f"{user}/{clean_repo}",
            "live_url": live_url,
            "response": res.stdout.strip()
        })
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
def github_create_and_deploy_repo(
    repo_name: str,
    title: str,
    description: str,
    html_content: str,
    readme_content: str = "",
    user: str = "karnkeshav"
) -> str:
    """Create a new GitHub repository, commit HTML5 web app files to 'main', push to remote, and deploy live on GitHub Pages."""
    try:
        clean_repo = repo_name.split("/")[-1].replace(".git", "")
        build_dir = f"/tmp/gh_mcp_{clean_repo}_{int(time.time())}"
        os.makedirs(build_dir, exist_ok=True)

        with open(os.path.join(build_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(html_content)

        live_url = f"https://{user}.github.io/{clean_repo}/"
        repo_url = f"https://github.com/{user}/{clean_repo}"

        if not readme_content:
            readme_content = f"# 🚀 {title}\n\n> Live Production App: [{live_url}]({live_url})\n\n{description}\n"

        with open(os.path.join(build_dir, "README.md"), "w", encoding="utf-8") as f:
            f.write(readme_content)

        # Git init
        subprocess.run(["git", "init", "-b", "main"], cwd=build_dir, check=True)
        subprocess.run(["git", "config", "user.name", user], cwd=build_dir, check=True)
        subprocess.run(["git", "config", "user.email", "keshavkarn2005@gmail.com"], cwd=build_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=build_dir, check=True)
        subprocess.run(["git", "commit", "-m", f"feat: initial release of {title}"], cwd=build_dir, check=True)

        # gh repo create
        cmd = ["gh", "repo", "create", f"{user}/{clean_repo}", "--public", "--source=.", "--remote=origin", "--push"]
        res = subprocess.run(cmd, cwd=build_dir, capture_output=True, text=True, timeout=30)

        # Enable pages
        subprocess.run(["gh", "api", f"repos/{user}/{clean_repo}/pages", "-X", "POST", "-f", "source={\"branch\":\"main\",\"path\":\"/\"}"], cwd=build_dir, capture_output=True, text=True)

        return json.dumps({
            "status": "success",
            "repo_name": clean_repo,
            "repo_url": repo_url,
            "live_url": live_url,
            "title": title
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@mcp.tool()
def github_modify_and_push_repo(
    repo_name: str,
    instructions: str,
    html_content: str,
    readme_content: str = "",
    user: str = "karnkeshav"
) -> str:
    """Clone an existing GitHub repository, apply modified HTML/README changes, commit to 'main', push, and refresh GitHub Pages."""
    try:
        clean_repo = repo_name.split("/")[-1].replace(".git", "")
        build_dir = f"/tmp/gh_mcp_mod_{clean_repo}_{int(time.time())}"
        
        # Clone
        clone_res = subprocess.run(["git", "clone", f"https://github.com/{user}/{clean_repo}.git", build_dir], capture_output=True, text=True, timeout=30)
        if clone_res.returncode != 0:
            return github_create_and_deploy_repo(clean_repo, clean_repo.title(), instructions, html_content, readme_content, user)

        with open(os.path.join(build_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(html_content)

        if readme_content:
            with open(os.path.join(build_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write(readme_content)

        subprocess.run(["git", "config", "user.name", user], cwd=build_dir, check=True)
        subprocess.run(["git", "config", "user.email", "keshavkarn2005@gmail.com"], cwd=build_dir, check=True)
        subprocess.run(["git", "add", "."], cwd=build_dir, check=True)
        subprocess.run(["git", "commit", "-m", f"feat(ui): {instructions[:50]}"], cwd=build_dir, check=True)
        subprocess.run(["git", "push", "origin", "main"], cwd=build_dir, check=True)

        live_url = f"https://{user}.github.io/{clean_repo}/"
        repo_url = f"https://github.com/{user}/{clean_repo}"

        subprocess.run(["gh", "api", f"repos/{user}/{clean_repo}/pages", "-X", "POST", "-f", "source={\"branch\":\"main\",\"path\":\"/\"}"], cwd=build_dir, capture_output=True, text=True)

        return json.dumps({
            "status": "success",
            "repo_name": clean_repo,
            "repo_url": repo_url,
            "live_url": live_url,
            "message": "Repository updated and pushed to main"
        }, indent=2)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

if __name__ == "__main__":
    mcp.run()
