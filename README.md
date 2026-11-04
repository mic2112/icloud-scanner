# iCloud Cleaner

A local web page that shows what fills your iCloud and lets you delete what you pick. It runs only on your Mac, and nothing is uploaded.

![iCloud Cleaner overview](docs/screenshot.png)

## What it shows

- **Overview:** space used by Photos, iCloud Drive, Messages attachments and app data in iCloud.
- **Near-duplicates:** similar photos taken within a minute of each other. The best shot in each group is kept and the rest are pre-selected.
- **Biggest videos** and **Screenshots & recordings**, largest first.
- **iCloud Drive:** your largest files.
- **Messages:** your largest attachments and the chat each one came from. You can only view these here, not delete them.

Nothing is permanently deleted:
- Photos go to **Recently Deleted** in Photos, and macOS asks you to confirm first.
- iCloud Drive files go to the **Trash**, where Put Back still works.

iCloud frees the space once you empty Recently Deleted or the Trash.

## Requirements

- macOS 11 or later, with your library in the default `~/Pictures/Photos Library.photoslibrary`
- Python 3.7+ (standard library only, no `pip install`)
- Xcode Command Line Tools, used to build the small Swift helper on first run

## Setup

1. Install the Command Line Tools, if you don't have them:
   ```sh
   xcode-select --install
   ```
2. Clone the repo:
   ```sh
   git clone git@github.com:mic2112/icloud-scanner.git
   cd icloud-scanner
   ```
3. Give your terminal app (Terminal, iTerm, VS Code...) **Full Disk Access**: System Settings → Privacy & Security → Full Disk Access. Without it, the app can't read your Photos library or your Messages history. Restart the terminal afterwards.

## Run

```sh
python3 cleaner.py
```

The first run builds the `helper` binary, which takes about 30 seconds. After that, the page opens in your browser at `http://127.0.0.1:<random port>/`. Press Ctrl+C in the terminal to stop it.

The first time you delete photos, macOS asks for **Photos** access. Click Allow.

### Options

| Flag | What it does |
|------|--------------|
| `--no-open` | Start the server without opening a browser |
| `--selftest` | Run the built-in checks and exit |

## Notes

- **First scan:** the near-duplicate scan takes a few minutes. The results are saved in `.cache/`, so later runs are fast.
- **Privacy:** `.cache/` holds thumbnails and IDs from your own library. It is gitignored, and you can delete it at any time.
- **Network:** the server only listens on `127.0.0.1`, and any action that changes something needs a token that is new each run.
- **Not covered:** iPhone backups, Mail and Notes can't be read from a Mac. Use the "Open iCloud settings" button on the Overview tab to check them.

## Files

| File | Purpose |
|------|---------|
| `cleaner.py` | Local server: reads Photos, Messages and iCloud Drive, and serves the page |
| `index.html` | The web page |
| `helper.swift` | Native helper that compares photos (Vision), deletes photos (PhotoKit) and moves files to the Trash |
| `Info.plist` | Gives the helper its Photos permission prompt |
