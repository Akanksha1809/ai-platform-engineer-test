# Testing the GitHub PR automation (step by step)

This walks through everything needed to prove `pr_client.py` can really open a GitHub pull request: creating a throwaway repo, creating a scoped access token, installing the project's dependencies, setting a few environment variables, and running one test script. Written for macOS with no assumed familiarity -- every click and every command is spelled out.

Total time: about 10-15 minutes.

---

## Part 1 — Create a scratch GitHub repo

We'll use a brand-new, disposable repo so there's zero risk to any real project.

1. Open your web browser and go to **github.com**. Log in if you aren't already.
2. In the top-right corner of the page, click the **+** icon.
3. From the dropdown menu, click **New repository**.
4. In the "Repository name" box, type: `ai-platform-engineer-test`
5. Leave "Description" empty (or add anything you like — it doesn't matter).
6. Choose **Private** (the black-filled circle next to it) — no need for this test repo to be public.
7. Find the checkbox labeled **"Add a README file"** and click it so it's checked. This is important: it makes GitHub create the repo with one initial commit already on the `main` branch, which our test script needs.
8. Leave ".gitignore" and "License" both set to "None".
9. Scroll down and click the green **Create repository** button.
10. You'll land on the new repo's page. Look at the URL in your browser — it will look like `https://github.com/YOUR-USERNAME/ai-platform-engineer-test`. Write down (or remember) the part after `github.com/`, i.e. `YOUR-USERNAME/ai-platform-engineer-test`. You'll need this exact string later as `GITHUB_REPO`.

That's Part 1 done — you now have a repo with one commit on `main`.

---

## Part 2 — Create a fine-grained personal access token (PAT)

This is a password-like credential, scoped to only this one repo, that lets our code create branches/files/PRs on your behalf.

1. In GitHub, click your profile picture in the top-right corner.
2. Click **Settings** in the dropdown.
3. On the Settings page, scroll all the way down the left-hand sidebar menu until you see **Developer settings** (it's near the very bottom). Click it.
4. In the left sidebar that appears, click **Personal access tokens** to expand it, then click **Fine-grained tokens**.
5. Click the **Generate new token** button (top-right of that page).
6. You'll see a form:
   - **Token name**: type `ai-platform-engineer-test`
   - **Expiration**: change this to something short like `30 days` (this is a throwaway token for testing, so a short life is safer)
   - **Description**: leave blank, or note "testing PR automation"
   - **Resource owner**: leave as your own username
7. Scroll down to **Repository access**. Select the radio button **"Only select repositories"**.
8. A box labeled "Select repositories" will appear — click it and choose `ai-platform-engineer-test` (the repo from Part 1).
9. Scroll down further to **Permissions**. Click **Repository permissions** to expand it if it isn't already.
10. You'll see a long list of permission types, each with a dropdown defaulted to "No access". Find these two and change them:
    - **Contents** → click its dropdown → choose **Read and write**
    - **Pull requests** → click its dropdown → choose **Read and write**
    - Leave every other permission on this list as "No access" — we don't need them.
11. Scroll to the very bottom and click the green **Generate token** button.
12. GitHub will show you the new token ONE TIME ONLY — a long string starting with `github_pat_`. Click the little copy icon next to it to copy it.
13. Paste this token somewhere you can retrieve it in the next 10 minutes — a plain text note, Notes app, whatever you're comfortable with temporarily. Do not paste it back into this chat with me — treat it like a password.

That's Part 2 done — you now have a token that can act on just that one test repo.

---

## Part 3 — Install the project's Python dependencies

Now switch to your own computer's Terminal (not this chat) — this step needs your machine's real internet connection.

1. Open the **Terminal** app (press `Cmd + Space`, type `Terminal`, press Enter).
2. Navigate into the project folder. Type this and press Enter (adjust the path if your folder name/location differs):
   ```
   cd "/Users/akanksha./Documents/AI Platform Assistant/ai-platform-engineer"
   ```
3. Confirm you're in the right place — type `ls` and press Enter. You should see folders like `backend`, `templates`, `docs`, `scripts`, and a `README.md`.
4. Check Python is installed — type `python3 --version` and press Enter. You should see something like `Python 3.10.x` or higher. (If you get "command not found", you'll need to install Python from python.org first.)
5. (Recommended, keeps things tidy) Create an isolated environment just for this project:
   ```
   python3 -m venv venv
   ```
   This creates a `venv` folder inside the project — a private copy of Python just for this project's packages.
6. Activate it:
   ```
   source venv/bin/activate
   ```
   Your terminal prompt should now show `(venv)` at the start of the line — that confirms it's active.
7. Install the project's dependencies:
   ```
   pip install -r requirements.txt
   ```
   This downloads FastAPI, Pydantic, Jinja2, Requests, and PyGithub. It may take a minute or two. You should see a series of "Successfully installed ..." lines with no red "ERROR" text at the end.

That's Part 3 done. (Any time you close and reopen Terminal, you'll need to re-run step 6 — `source venv/bin/activate` — before running Python commands in this project again.)

---

## Part 4 — Set the environment variables

Still in the same Terminal window/tab (so the venv from Part 3 stays active), set three values so the code knows which repo and token to use. Replace the placeholders with your real values from Parts 1-2.

1. Set your token:
   ```
   export GITHUB_TOKEN="github_pat_paste_your_real_token_here"
   ```
2. Set your repo:
   ```
   export GITHUB_REPO="YOUR-USERNAME/ai-platform-engineer-test"
   ```
   (Use the exact `username/repo-name` string you noted in Part 1, step 10.)
3. (Optional — the default is already "main", so you can skip this unless you renamed your default branch)
   ```
   export GITHUB_BASE_BRANCH="main"
   ```
4. Double-check they're set by typing:
   ```
   echo $GITHUB_TOKEN
   echo $GITHUB_REPO
   ```
   Each should print back what you just set.

Important: these `export` commands only last for this one Terminal window. If you close Terminal and reopen it, you'll need to redo Parts 3 (step 6) and 4 before testing again.

---

## Part 5 — Run the test

Still in the same Terminal window:

1. Run:
   ```
   python3 scripts/test_pr_client.py
   ```
2. Watch the output. Within a few seconds you should see something like:
   ```
   Calling pr_client.open_pull_request() ...

   attempted: True
   created:   True
   url:       https://github.com/YOUR-USERNAME/ai-platform-engineer-test/pull/1
   branch:    ai-platform/pr-client-smoke-test-1234567890
   message:   PR opened.

   Success! Open the PR here: https://github.com/YOUR-USERNAME/ai-platform-engineer-test/pull/1
   ```
3. Copy that URL into your browser — you should see a real, live pull request in your test repo, containing one file (`ai-platform-engineer-test.txt`) and the description text the Docs Agent would normally generate.

### If something goes wrong instead

- `attempted: False` — your `GITHUB_TOKEN` or `GITHUB_REPO` environment variable isn't set in this terminal session. Redo Part 4 in this same window.
- `created: False` with a 401/403-style message — the token is wrong, expired, or doesn't have the right permissions. Redo Part 2, double-checking the Contents and Pull requests permissions were both set to "Read and write" and that you selected the right repo.
- `created: False` mentioning "404" or "main" — your repo's default branch isn't actually called `main` (some older repos default to `master`). Check the branch name shown on your repo's GitHub page and set `GITHUB_BASE_BRANCH` to match in Part 4, step 3.
- `ModuleNotFoundError` — the venv from Part 3 isn't active. Run `source venv/bin/activate` again in this terminal, then retry.

### Cleaning up afterwards

Once you've confirmed it works, you can safely:
- Close/delete the test pull request on GitHub (its "Close pull request" button).
- Delete the `ai-platform-engineer-test` repo entirely (Settings tab on the repo → scroll to the bottom → "Delete this repository").
- Revoke the token (Settings → Developer settings → Fine-grained tokens → find it → Delete), especially since it will already auto-expire in 30 days anyway.

You now have real, verified proof the PR automation works end-to-end — the same `pr_client.open_pull_request()` function is what the full pipeline (`backend/main.py` → `/generate`) calls automatically once a request comes in.
