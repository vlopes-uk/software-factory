---
name: init
description: Initialise the software factory in the product repo (state, config for the product/api/web GitHub repos, folders, labels, code-repo scaffolding).
disable-model-invocation: true
argument-hint: "[owner] [product-name]"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py *) Bash(gh label create *) Bash(gh repo view *) Bash(gh repo create *) Bash(gh repo clone *) Bash(gh auth status *) Bash(git *) Bash(dotnet --version) Bash(node --version) Bash(npm --version) Read Write Edit
---

# Initialise the factory

Run this from the **product repo**, the one that will hold the specs. It works best as a fresh repo named `<product>-product`.

## Result of initialisation

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py init`

## Current config

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/gate.py config`

## Your job

1. **Repos.** The factory uses three GitHub repos: `product` (specs and stories, which is this repo), `api` (.NET backend) and `web` (React frontend). Ask the user for the GitHub owner and product name (args: `$0` `$1`) and confirm the names. Suggest `<product>-product`, `<product>-api` and `<product>-web`, with local clones as siblings (`../<product>-api`, `../<product>-web`). Then write `.factory/config.json` with the real names. Keep `"stack": "dotnet-react-azure"` and `"label": "be"/"fe"`.
   If the user wants specs inside the API repo instead, set `repos.product` equal to `repos.api` and keep `path: "."`.
2. **GitHub.** `gh auth status`. For each repo in the config, check it exists (`gh repo view <repo>`). Offer to create any that are missing (`gh repo create <repo> --private`) and to clone `api` and `web` to their configured paths. Ask before creating anything.
3. **Folder access.** Claude Code only sees the product repo by default. Add the code clones to the product repo's `.claude/settings.json`, merging with any existing content:
   ```json
   { "permissions": { "additionalDirectories": ["<repos.api.path>", "<repos.web.path>"] } }
   ```
   Then tell the user to restart Claude Code so the change takes effect. As a one-off alternative, they can run `claude --add-dir <api path> --add-dir <web path>`.
4. **Labels.** Create these in **all three** repos with `gh label create <name> --repo <repo> --color <c> --description <d> --force`:

   | Label | Colour | Description |
   | --- | --- | --- |
   | `factory:epic` | `5319e7` | Epic created by the PO |
   | `factory:story` | `1d76db` | User story created by the PO |
   | `factory:task` | `0e8a16` | Implementation task created in planning |
   | `be` | `c5def5` | Backend task (.NET) |
   | `fe` | `fef2c0` | Frontend task (React) |
   | `priority:must` | `b60205` | MoSCoW must |
   | `priority:should` | `d93f0b` | MoSCoW should |
   | `priority:could` | `fbca04` | MoSCoW could |
   | `factory:blocked` | `000000` | Agent stopped: needs a human or an upstream fix |
   | `qa:passed` | `0e8a16` | QA reviewer passed the PR |
   | `qa:changes-requested` | `d93f0b` | QA reviewer requested changes |

5. **Code repo guidance.** In each local code clone, offer to add a `CLAUDE.md` from `${CLAUDE_PLUGIN_ROOT}/templates/code-repo/CLAUDE.api.md` or `CLAUDE.web.md`, filling in the product repo name, and commit it on a branch with a PR. Don't scaffold application code here; that is task T-00 of the plan.
6. **Toolchain check.** Report the output of `dotnet --version` (needs 10.x), `node --version` (LTS) and `npm --version`. Missing tools only matter on the machine where `/factory:build` runs.
7. In the product repo, add to `CLAUDE.md` (offer to do it):
   ```
   ## Software factory
   - Specs live in docs/factory/ and are owned by their role; developers never edit them.
   - Code lives in <api repo> (.NET 10) and <web repo> (React + TS); see .factory/config.json.
   - Phase state: `/factory:status`. Only humans approve phases (`/factory:approve <phase>`).
   ```
8. Finish with: "Factory ready. Start with `/factory:po <your product idea>`."
