# Set up Dev Privacy Guard v0.2

This checkout targets the existing Apple M1 Mac with macOS 15 and 8 GB RAM. The dashboard, vault, document processing and Browser Use agent run locally. Reasoning uses your hosted LLM/VLM API; voice transcription uses local multilingual faster-whisper without an API key.

Already installed? Follow [startup.md](startup.md) for the short launch guide. The start script uses `.venv/bin/python` directly; activating the environment or running verification is not required to start the app.

## 1. Install prerequisites

With Homebrew:

```bash
brew install uv node tesseract
```

Use Node 22.12 or later. Python 3.12 is managed by uv; the system Python is not used. Initial dependency and Chromium downloads require internet access and a few GB of free disk space. Docker, Redis and a cloud database are not required.

```bash
uv --version
node --version
npm --version
tesseract --version
```

## 2. Install the project

```bash
cd /Users/aksh-aggarwal/Desktop/Workspace/SIH/Agent
./scripts/setup.sh
```

On another machine, use the directory where you copied this project. The script installs the locked Python environment, builds the dashboard and downloads compatible Chromium into the application's data directory. The extension is bundled HTML/CSS/JavaScript and requires no build.

## 3. Start and pair

```bash
./scripts/start.sh
```

Open [the dashboard](http://127.0.0.1:8765). Keep the terminal running.

1. Copy the pairing code printed in the terminal into the dashboard.
2. Create a vault passphrase, or unlock your existing vault. The UI asks for at least 12 characters.
3. Keep the passphrase safe: there is no password recovery or cloud account.

Pairing codes last 30 minutes. Restarting the companion generates a new code and invalidates previous paired sessions. Pairing is separate from unlocking the encrypted vault.

Default application data: `~/Library/Application Support/Dev Privacy Guard`. Documents, records and provider credentials are stored encrypted there. Your normal Chrome profile is not imported.

## 4. Configure the reasoning API

Open **Settings** in the unlocked dashboard.

- Choose **Remote** reasoning mode.
- Use the default model ID: `google/gemini-2.5-flash` (Gemini 2.5 Flash through OpenRouter).
- Use the default API base URL: `https://openrouter.ai/api/v1`.
- Enter your [OpenRouter API keys](https://openrouter.ai/settings/keys), using **Add another key** for up to 10 keys, and save. On unlock, legacy direct-Gemini credentials move to fallback; legacy direct-OpenAI reasoning credentials are cleared. Enter a new primary OpenRouter key.

The model must accept **Chat Completions, JSON object output and image inputs** for the screenshot workflow. A text-only model can be used only with visual checkpoints disabled. This project does not train or host an LLM, and a configured model is not a guarantee of successful automation on every website.

Keys are encrypted locally and are never placed in the extension or frontend bundle. The model adapter sends sanitized text automatically unless you enable text review. Every request containing a screenshot waits for your approval. Unnecessary auxiliary model calls and unreviewed image retries are disabled. Optionally save a [Google AI Studio API key](https://aistudio.google.com/apikey) under **Optional fallback · Gemini**, with model `gemini-2.5-flash`. Remote tasks may switch once to the direct Gemini API after a primary-provider error (including exhausted OpenRouter credits). Image requests require fresh approval for Gemini. Without a fallback key, only OpenRouter is used.

You can save up to 10 keys in each provider pool. The agent tries the next key on key-related or transient failures, then switches from OpenRouter to Gemini when needed. Blank rows keep the saved pool; entering keys replaces it. Use **Check saved keys** after saving to test every slot with a small synthetic JSON request before your demo. This consumes provider credits. A successful text check does not prove image support or full task success. Gemini keys in the same project share quota, and OpenRouter keys may share account credits.

Gemini requests omit the unsupported `store` field. If a request still returns 400, the dashboard now classifies the error; generic malformed requests are not retried with every key. Check the Gemini model name (for example `gemini-2.5-flash`, without `google/`), key restrictions and Google AI Studio billing/project setup.

## 5. Local English/Hindi voice input

Setup installs multilingual Whisper small weights once. For an existing checkout, run:

```sh
uv sync
.venv/bin/python scripts/speech_install.py
```

The initial public model download requires internet access. Transcription loads only local files, runs on the CPU, and never uploads recordings or requires an API key. The default model lives under the companion data directory in `models/whisper-small`; `GUARD_SPEECH_MODEL` can select a local compatible model directory. Old stored Whisper API keys are removed on unlock.

Choose **Record task** (dashboard) or **Record voice task** (extension), grant microphone permission, speak in English or Hindi, and stop. Recordings are limited to 60 seconds and 10 MiB. Listen to the recording, choose **Transcribe locally**, and edit the returned text. In the extension, choose **Use transcript in task** to place it in the task draft. Start the task explicitly when the text and selected records are correct.

Audio and drafts are kept in memory, are not automatically saved to the vault, and are discarded when their recording surface closes. Locking the vault cancels local recognition. If microphone permission is denied, check browser and macOS microphone permissions. Typed input remains available.

Use **Language / भाषा** to switch the dashboard or extension interface between English and Hindi. Both accept English, Hindi and mixed-language task input. The main agent receives a structured execution brief preserving the original request and the selected response language.

## 6. Launch Chromium and load the extension

Choose **Launch browser** in the dashboard. The companion launches dedicated Chromium with a separate profile and local debugging connection.

If the extension icon is missing:

1. In that controlled browser, open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this project's `apps/extension` directory.
4. Pin **Dev Privacy Guard**.
5. Open its side panel and enter the same current pairing code.

The extension's debugger permission is used for exact target discovery. Browser Use owns the actual browser connection. Use the extension in the controlled browser; it does not silently attach to your everyday Chrome windows.

The dashboard can be opened in a different browser. Tasks can start from either interface.

## 7. Run the new portal demonstration

Use a **Remote** model for this flow. A real API key is required for live planning and visual interpretation.

1. Open **New task → Prepare portal demo**. This adds synthetic name/email/phone records and selects only those records, leaving the document fields for the missing-information round.
2. Use `http://127.0.0.1:8766/portal.html` as the starting website. The form does not have to be open beforehand.
3. Enter or dictate: `Open this website and complete the application using my selected profile. Fill available details first, ask for missing documents, and stop before final submission.`
4. Keep **Redacted images at every planning step** and **stop before final submission** enabled. Start the task.
5. Follow the task activity. Approve reviewed navigation clicks if requested.
6. At a screenshot checkpoint, review in the extension or dashboard. Compare the original local image with the actual redacted outgoing image. Drag rectangles or use the numeric controls to add masks, then apply them. Each edit replaces the approval.
7. Approve the latest image/context, or deny to block transmission.
8. When information is requested, upload `demo/documents/portal-statement.txt` in **Documents**. Review the PAN, address and statement total. Confirm the appropriate facts; choose profile scope only for reusable details.
9. Return to the waiting task, select the newly confirmed records alongside the original selected profile, and Resume.
10. Inspect the completed application/review page. Final submission is withheld by default.

The portal and its documents are fictional. They demonstrate the workflow and do not prepare or file a real tax return. See [startup.md](startup.md#run-the-portal-demo) for the short demo sequence.

## 8. Run the original no-key form check

The **Demo planner** is deterministic and does not use a remote AI model. It tests the original form, vault, reference mapping and approvals.

1. Add the full demo profile and open the original demo form at `http://127.0.0.1:8766/`.
2. Select **Demo** mode and that controlled tab.
3. Enter `Fill this form using my saved profile. Stop before submitting.`
4. Approve the sanitized contexts and private-value disclosures.

The old planner cannot navigate the multi-step portal. Its optional image path remains completely black; use Remote mode to demonstrate selective visual redaction.

## 9. Pause, resume and stop

- **Pause:** cancels current work. Resume observes the current page before continuing.
- **Waiting for information:** add/review facts, update the task's selected records, then Resume. It continues in the same tab.
- **Stop:** ends the task and revokes further actions; it cannot undo an action already delivered to the site.
- **Lock vault:** clears runtime access to credentials, records and image artifacts; cancels transcription and active browser work.
- **Ctrl+C:** stops the companion and its controlled browser.

After a service restart, pair and unlock again. Unfinished tasks remain stopped. If an action outcome is uncertain, inspect the website before starting another task; do not assume a timeout means nothing happened.

## 10. Verify the installation

```bash
./scripts/verify.sh
```

The checks use synthetic data. Hosted model responses and most speech tests are mocked unless explicitly configured for a live run. See [docs/VALIDATION.md](docs/VALIDATION.md) for exact evidence and opt-in browser checks.

After changing dashboard sources:

```bash
npm --prefix apps/dashboard run build
```

Refresh the dashboard. After extension source changes, use **Reload** on its `chrome://extensions` card and reopen the side panel. The app trusts the dashboard served by the companion, not an arbitrary Vite development-server origin.

## Troubleshooting

| Problem | Action |
|---|---|
| Dashboard cannot connect | Keep the companion running and use `http://127.0.0.1:8765`. |
| Pairing expired | Restart the companion and pair both interfaces using the new terminal code. |
| Browser missing | Run `uv run scripts/browser_install.py` from the project directory. |
| Current tab is not controllable | Use the dedicated Chromium, or provide a starting URL to create a controlled tab. |
| Model returns an error | Check API credit, key, model ID, image support and Chat Completions JSON support. No action is authorized by a failed response. |
| Local speech model is missing | Run `.venv/bin/python scripts/speech_install.py`, then refresh Settings. |
| Local transcription error or empty transcript | Record a shorter command or type it. No browser task has started. |
| Image approval changed | Reload the current preview and approve its newest version. Old approval IDs cannot authorize edited images. |
| Screenshot geometry cannot be verified | Let the page settle, stop animations if possible, and retry from fresh state. No uncertain image is sent. |
| Missing information repeats | Select the confirmed record in the task's available-information list before Resume; remove ambiguous duplicates from the selection. |
| Cross-origin redirect, frame or custom control blocked | Complete that step manually, then start a task for the intended destination. |
| OCR unavailable | Install Tesseract and restart the companion. Correct OCR candidates before confirming. |
| Ports occupied | Stop the process already using 8765/8766, then restart. |
| Forgotten vault passphrase | There is no recovery. Preserve the encrypted data before creating a separate vault. |

Advanced settings: `GUARD_DATA_DIR` chooses another local data directory; use the same value when installing the browser. `GUARD_BROWSER_EXECUTABLE` can select compatible Chromium. `GUARD_PORT` and `GUARD_DEMO_PORT` are backend overrides; changing ports also requires updating the extension's fixed localhost URLs/permissions.

Keep the companion and debugging socket on loopback. No signed installer, Chrome Web Store distribution, Firefox support, local vision model or local speech model is included in this version.
