---
name: setup-icloud-cleaner
description: Set up iCloud Cleaner on this Mac - first-time install, permission problems (Full Disk Access, Photos access, empty Photos or Messages tabs), or a helper that fails to build. Use when asked to set up, install, run or fix iCloud Cleaner.
---

# Set up iCloud Cleaner

Work from the repo root (the folder with `cleaner.py`). `python3 cleaner.py --check` is the source of truth: one line per requirement marked `ok`, `FIX` or `note`, the exact fix printed under each `FIX`, exit code 0 when ready. Get past every check by fixing the Mac, leaving the code as it is.

## Steps

1. Run `python3 cleaner.py --check`. Done when you have its full output.

2. Clear every `FIX` line, top to bottom, rerunning the check after each fix. Done when the check exits 0 and prints `Ready`.
   - **Xcode Command Line Tools:** run `xcode-select --install`. It opens an installer window: ask the user to click Install and tell you when it finishes (5 to 15 minutes).
   - **Full Disk Access for <app>:** run the `open "x-apple.systempreferences:..."` command from the check output. Tell the user to turn on <app> in that list (if it isn't listed: click +, pick it from Applications), then quit <app> with Cmd+Q and reopen it. When <app> is the app you are running in, quitting ends this session, so say this first: "after reopening, ask me to finish setting up iCloud Cleaner". The check picks up where it left off.
   - **Photos access for <app>:** run the `open` command from the check output and have the user turn on <app>. Takes effect without a restart.
   - **macOS version** or **Swift helper builds:** these need a person. Show the user the line and any compiler error above it, and stop.

   Pass each `note` line on to the user once; notes never block.

3. Start the app: run `python3 cleaner.py` as a background process. It is a local server that runs until Ctrl+C and opens the browser itself. Give the user the URL from its first line (`iCloud cleaner running at http://127.0.0.1:<port>/`). Done when the user has the URL.

4. Tell the user in two lines: nothing leaves their Mac, and deleted photos go to Recently Deleted and files to the Trash, so iCloud frees the space only once those are emptied.

## Gotchas

- **Sandboxed shells:** the check reads a protected file to detect Full Disk Access. If it reports Full Disk Access off after the user turned it on and reopened the app, your shell is likely sandboxed: rerun the check outside the sandbox (or ask the user to run it in their own terminal).
- **Server blocked:** starting the server binds a port on 127.0.0.1. If your sandbox blocks that, ask the user to run `python3 cleaner.py` in their own terminal app, and remind them that app needs Full Disk Access too.
