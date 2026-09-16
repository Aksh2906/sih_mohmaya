# How to start Dev Privacy Guard

Your project uses Python from its own `.venv` environment. You do not need to activate it manually. The existing Python environment ran successfully in the previous session.

## Start on your Mac

Open Terminal and run:

```bash
cd /Users/aksh-aggarwal/Desktop/Workspace/SIH/Agent
./scripts/start.sh
```

Keep this terminal open. Open the dashboard in your browser:

**http://127.0.0.1:8765**

1. Copy the pairing code printed in Terminal into the dashboard.
2. Create a vault passphrase of at least 12 characters, or unlock your existing vault.
3. Open **Settings** and configure your reasoning API key, model and API base URL. Select **Remote** mode for the Browser Use agent. The screenshot workflow requires a model supporting images and Chat Completions JSON output.
4. Choose **Launch browser** to open the dedicated Chromium window.

Starting the app does not run tests or rebuild the dashboard. If the script reports missing dependencies or a missing dashboard build, run `./scripts/setup.sh` once, then start again. Full installation instructions are in [setup.md](setup.md).

## Open the extension

In the dedicated Chromium window:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select:

   ```text
   /Users/aksh-aggarwal/Desktop/Workspace/SIH/Agent/apps/extension
   ```

4. Pin **Dev Privacy Guard**, open its side panel and enter the terminal pairing code.

If the extension is already installed, just open its side panel and pair. Use this controlled Chromium window for agent tasks.

## Run the portal demo

1. In the dashboard, choose **New task → Prepare portal demo**. It prepares a partial synthetic profile and the starting URL:

   ```text
   http://127.0.0.1:8766/portal.html
   ```

2. Use **Remote** mode and enter this task:

   ```text
   Open this website and complete the application using my selected profile. Fill available details first, ask for missing documents, and stop before final submission.
   ```

3. Keep visual planning and stop-before-submission enabled, then start. The website does not need to be open beforehand.
4. When screenshot approval appears, review in the extension or dashboard. Compare the original local image and the redacted outgoing image, add masks if needed, and approve the latest request.
5. When the agent asks for missing information, upload `demo/documents/portal-statement.txt` in **Documents**. Review and confirm the extracted facts.
6. Return to the waiting task, select the newly confirmed records along with the original profile records, and choose **Resume**.
7. Inspect the completed form or review page. Final submission is withheld by default.

The portal is fictional. The original form at `http://127.0.0.1:8766/` also has a deterministic **Demo** mode that needs no API key; that mode does not run the multi-step portal agent.

## Start from a task without a URL

Choose **New task**, select **Remote**, and describe what to do—for example, “Find wireless headphones on Amazon, compare three options, and show me the best match.” Leave **Starting website** empty and keep **Let the agent choose a website** selected. The agent opens the controlled browser, discovers a relevant site, then works through the task. You can also supply a URL or select an existing controlled tab.

Watch the blue **Agent** cursor move before clicks, field entry, dropdown selections and scrolling. Destination changes and sensitive actions still use the existing review flow, and final submission stays withheld by default. In the extension, **Use the current tab instead of finding a website** binds the task to the current page. Reload the extension after updating its files.

## Optional voice input

Run `.venv/bin/python scripts/speech_install.py` once if the local speech model is missing. Record in English or Hindi, stop, then choose **Transcribe locally**. Audio stays on this machine; no API key is needed. Review the editable transcript before explicitly starting a task. In the extension, choose **Use transcript in task** to update the draft.

## Login and other browser handoffs

When a service needs login, OTP, CAPTCHA or a manual action, the task waits for you
without losing its goal, selected vault fields or stage plan. Complete the step on
the website in the controlled browser, then choose **I've finished — continue** in
the dashboard or extension. Do not save passwords, OTPs or CAPTCHA answers as vault
records for this handoff. The agent reads fresh page state before continuing; if the
challenge is still present, it asks you to complete it again.

The task plan shows stages and their success criteria. The agent can consult public
search engines and official help pages, record observed sources, and revise the plan
as it proceeds. Public searches must not contain private profile values. Reaching an
intermediate page does not count as completion: a separate model check evaluates the
fresh page against the original goal. A per-run step limit pauses for review and can
resume the same task.

For “Open UIDAI's official myAadhaar beta portal and navigate to the Download Aadhaar
page in English”, success means reaching that page. It does not mean a PDF has been
saved; automated file saving is not added by the login handoff.

## Stop and restart

Press **Ctrl+C** in the running terminal to stop the companion. Start it again with the same two commands above. Each restart creates a new pairing code; pair and unlock again. Saved vault records remain in `~/Library/Application Support/Dev Privacy Guard`.

If a port is already in use, stop the previous companion terminal before starting another instance. If Chromium is missing, follow the browser installation step in [setup.md](setup.md).
