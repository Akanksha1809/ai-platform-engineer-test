# Verifying the full pipeline for real (Ollama + all validation tools)

So far, every tool call (Ollama, Terraform, Helm, Checkov, Conftest, Infracost) has been "SKIPPED" because none of them are installed anywhere we've tested. Your GitHub PR test proved the automation itself works -- this guide proves the rest: real NL understanding, real Terraform/Helm validation, real security findings, and a real dollar cost estimate, all in one pipeline run.

Written for macOS, every command spelled out. Total time: 30-45 minutes, mostly waiting for downloads.

---

## Part 0 — Check what you already have

Open **Terminal**, then `cd` into your project:

```
cd "/Users/akanksha./Documents/AI Platform Assistant/ai-platform-engineer"
```

Run the checker script that's already in the project:

```
bash scripts/check_tools.sh
```

You'll see a ❌ next to everything right now. Re-run this command any time during this guide to see your progress -- it's the fastest way to confirm each install worked.

Check whether you have Homebrew (the standard macOS package manager we'll use for most of these):

```
brew --version
```

If you see "command not found", install it first by pasting this into Terminal and pressing Enter (it will ask for your Mac password):

```
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Follow any "Next steps" instructions it prints at the end (it sometimes asks you to run 1-2 more lines to add Homebrew to your PATH) before continuing.

---

## Part 1 — Install Ollama + pull a model

1. Go to **ollama.com/download/mac** in your browser and click the download button. This downloads a `.dmg` file.
2. Open the downloaded `.dmg` file (double-click it in Finder/Downloads).
3. Drag the Ollama icon into the **Applications** folder shown in that window.
4. Open **Applications** (in Finder) and double-click **Ollama** to launch it. It'll ask to install a command-line tool -- allow it. You should see a small llama icon appear in your Mac's menu bar (top-right area) -- that's Ollama's background server, now running.
5. Back in Terminal, confirm the command-line tool is there:
   ```
   ollama --version
   ```
6. Pull a code-capable model. This downloads several gigabytes, so it may take a while depending on your connection:
   ```
   ollama pull qwen2.5-coder:7b
   ```
   (If your Mac has 24GB+ RAM and you want better generation quality, you can instead pull `qwen2.5-coder:14b` -- just substitute that name everywhere below. Stick with `7b` if you're unsure.)
7. Confirm the model downloaded:
   ```
   ollama list
   ```
   You should see `qwen2.5-coder:7b` in the list.
8. Confirm the server is actually reachable (this is what our Python code checks):
   ```
   curl -s http://localhost:11434/api/tags
   ```
   You should see a JSON response, not an error. As long as the menu bar app is running, this stays up -- you don't need to manually run `ollama serve`.

Re-run `bash scripts/check_tools.sh` -- Ollama should now show ✅.

---

## Part 2 — Install Terraform

Terraform is no longer in Homebrew's default formulae, so we add HashiCorp's own tap first.

```
brew tap hashicorp/tap
brew install hashicorp/tap/terraform
```

Confirm it worked:

```
terraform --version
```

---

## Part 3 — Install Helm

```
brew install helm
```

Confirm:

```
helm version --short
```

---

## Part 4 — Install kubeconform

```
brew install kubeconform
```

Confirm:

```
kubeconform -v
```

---

## Part 5 — Install Checkov

Checkov is a Python tool, so install it into the same project virtual environment you created earlier (not globally). Make sure your venv is active first -- your prompt should show `(venv)` at the start of the line. If it doesn't:

```
source venv/bin/activate
```

Then install:

```
pip install checkov
```

This one can take a minute or two. Confirm:

```
checkov --version
```

---

## Part 6 — Install Conftest

```
brew install conftest
```

Confirm:

```
conftest --version
```

---

## Part 7 — Install Infracost

```
brew install infracost
```

Register for a free API key (no credit card -- this just unlocks Infracost's public pricing data):

```
infracost auth login
```

(An earlier version of this guide said `infracost register` -- that command doesn't exist in current versions. Use `infracost auth login` instead.) This opens your browser for a quick login/signup, then saves a key locally automatically; you don't need to set any environment variable for it. Confirm it worked with:

```
infracost auth whoami
```

**One more required step:** current versions of Infracost tie even local/CLI cost estimates to an "organization" on their dashboard. If you skip this, you'll get `"User has no associated organization"` instead of a cost number. Go to **dashboard.infracost.io** in your browser, log in with the same account, and follow the prompt to create an organization (still free, no credit card -- it's just an onboarding step their newer CLI now requires).

Confirm:

```
infracost --version
```

---

## Part 8 — Confirm everything is green

```
bash scripts/check_tools.sh
```

Every line should now show ✅. If anything still shows ❌, re-check that step above before moving on -- the pipeline will still run with tools missing (it degrades gracefully), but the whole point of this pass is to see real results instead of "skipped."

---

## Part 9 — Run the real end-to-end test

1. Make sure your venv is active (`(venv)` visible in the prompt); if not: `source venv/bin/activate`.

2. (Optional) If you still want this run to also open a real GitHub PR with real findings baked in, re-set the two variables from the earlier guide, in this same terminal:
   ```
   export GITHUB_TOKEN="github_pat_..."
   export GITHUB_REPO="YOUR-USERNAME/ai-platform-engineer-test"
   ```
   If you'd rather just see the JSON results without opening another PR, skip this step -- `pr_result.attempted` will just come back `false`.

3. Start the server:
   ```
   uvicorn backend.main:app --reload --port 8000
   ```
   Leave this Terminal tab/window running -- it's now your live server. You'll see log lines here as requests come in.

4. Open a **new** Terminal tab (`Cmd + T`), `cd` back into the project (`source venv/bin/activate` isn't needed here since this tab only runs `curl`), and send the same test request as before:
   ```
   curl -s -X POST http://localhost:8000/generate \
     -H "Content-Type: application/json" \
     -d '{"request": "I need a new public service called orders-api using image myorg/orders-api:1.4.0 on port 8080 with 3 replicas, needs an s3 bucket and a postgres database"}' \
     | python3 -m json.tool
   ```
   The `| python3 -m json.tool` at the end just pretty-prints the JSON so it's readable. This request may take 10-30 seconds now, since it's making a real Ollama call and running six real external tools.

---

## Part 10 — What to look for in the output

- **`reports[0].notes`** (the planner): should now say something like `"Planned via Ollama model 'qwen2.5-coder:7b'."` instead of the heuristic-parser message. This is the real NL-understanding path working for the first time.
- **`terraform validate`**: expect `"ran": true, "ok": true` -- the generated HCL should be syntactically valid.
- **`helm template`** and **`kubeconform`**: expect `"ran": true, "ok": true` -- the chart should render and pass schema validation.
- **`checkov (terraform)`**: expect `"ran": true, "ok": false`. This is correct, not a bug -- the generated security group allows inbound traffic broadly and the RDS instance isn't using every hardening option Checkov checks for. A real scanner finding real, expected issues is exactly what this agent is for. Read `output` in the response for the specific rule IDs it flagged.
- **`conftest`**: expect `"ran": true, "ok": true` -- our own Rego policy (resource requests/limits set, nothing privileged, Service has a selector) is satisfied by design, since the Helm templates were written to comply with it.
- **`infracost`**: expect `"ran": true` with a real `"monthly_cost_usd"` number instead of `null`.
- **`pr_result`**: if you set `GITHUB_TOKEN`/`GITHUB_REPO` in step 2, `created` should be `true` with a real `url` -- open it and you'll see the PR body now contains real ✅/❌ results and a real dollar figure, instead of the "skipped" messages from before.

---

## If something doesn't work

- **Ollama planning still says "not reachable"**: confirm the menu bar app is still running (look for the llama icon), and re-run `curl -s http://localhost:11434/api/tags` in a fresh terminal to double check.
- **`uvicorn` request hangs for a long time then times out**: the first Ollama call after pulling a model can be slow while it loads into memory. Try the `curl` command again -- it should be much faster the second time.
- **Checkov/Conftest/Infracost show `"ran": false` still**: re-run `bash scripts/check_tools.sh` -- if any show ❌, that install step needs to be redone in the same terminal session (make sure you didn't open a brand new Terminal window that hasn't seen the `brew install` yet -- new terminals should still see Homebrew-installed tools, but double check with `which <tool>`).
- **Port 8000 already in use**: another `uvicorn` from a previous test may still be running in an old Terminal tab. Find that tab and press `Ctrl + C` to stop it, or start this one on a different port (`--port 8001`) and adjust the `curl` URL to match.

Once you see real, non-skipped results across the board, the entire "first half" of the architecture -- NL understanding, Terraform/Helm generation and validation, security/policy scanning, cost estimation, PR automation -- is proven end-to-end on real tools, not just reviewed code.
