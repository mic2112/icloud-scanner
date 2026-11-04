#!/usr/bin/env python3
"""iCloud cleaner: see what fills iCloud and delete what you pick.

Run:  python3 cleaner.py
Reads the Photos database read-only. Photos you delete go to Recently Deleted (macOS asks first);
iCloud Drive files go to the Trash.
"""
import json, os, re, secrets, sqlite3, stat, subprocess, sys, threading, webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "helper")
CACHE = os.path.join(HERE, ".cache", "distances.json")
HOME = os.path.expanduser("~")
LIB = os.path.join(HOME, "Pictures", "Photos Library.photoslibrary")
DB = f"file:{LIB}/database/Photos.sqlite?mode=ro"
MOBILE_DOCS = os.path.join(HOME, "Library", "Mobile Documents")
DRIVE = os.path.join(MOBILE_DOCS, "com~apple~CloudDocs")
MESSAGES = os.path.join(HOME, "Library", "Messages", "Attachments")
APPLE_EPOCH = 978307200
BURST_SECONDS = 60  # only photos taken this close together can be near-duplicates
TOKEN = secrets.token_urlsafe(24)
UUID_RE = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$")
UUID_NAME = re.compile(r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\.", re.I)

SETTINGS_URLS = {
    "icloud": "x-apple.systempreferences:com.apple.systempreferences.AppleIDSettings:icloud",
    "storage": "x-apple.systempreferences:com.apple.settings.Storage",
    "fulldisk": "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
    "photos-privacy": "x-apple.systempreferences:com.apple.preference.security?Privacy_Photos",
}
TERMINALS = {"Apple_Terminal": "Terminal", "iTerm.app": "iTerm", "vscode": "Visual Studio Code",
             "WarpTerminal": "Warp", "ghostty": "Ghostty", "WezTerm": "WezTerm", "Hyper": "Hyper"}
PHOTOS_DENIED = 2  # helper exit code when Photos access is off


def terminal_name():
    """The app macOS grants permissions to: the terminal (or agent app) that started us."""
    bundle = os.environ.get("__CFBundleIdentifier", "")
    if re.fullmatch(r"[\w.-]+", bundle):
        r = subprocess.run(["lsappinfo", "info", "-only", "name", bundle], capture_output=True, text=True)
        name = re.search(r'"LSDisplayName"="(.+)"', r.stdout)  # the app is running: it started us
        if name:
            return name.group(1)
    return TERMINALS.get(os.environ.get("TERM_PROGRAM", ""), "your terminal app")


def has_full_disk_access():
    """Reading the user's TCC database needs Full Disk Access, so it is a reliable probe."""
    try:
        with open(os.path.join(HOME, "Library", "Application Support", "com.apple.TCC", "TCC.db"), "rb"):
            return True
    except PermissionError:
        return False
    except OSError:
        return True  # file missing: can't tell, so don't nag

# ---------- Photos ----------

def load_assets():
    db = sqlite3.connect(DB, uri=True)
    rows = db.execute("""
        select a.ZUUID, a.ZKIND, a.ZKINDSUBTYPE, a.ZDATECREATED, a.ZWIDTH, a.ZHEIGHT, a.ZDURATION,
               a.ZFAVORITE, coalesce(a.ZOVERALLAESTHETICSCORE, 0), coalesce(b.ZORIGINALFILESIZE, 0),
               coalesce(b.ZORIGINALFILENAME, ''), a.ZTRASHEDSTATE
        from ZASSET a left join ZADDITIONALASSETATTRIBUTES b on b.ZASSET = a.Z_PK
        where a.ZVISIBILITYSTATE = 0
    """).fetchall()
    db.close()
    out = []
    for u, kind, sub, date, w, h, dur, fav, score, size, name, trashed in rows:
        out.append({
            "id": u, "video": kind == 1, "date": (date or 0) + APPLE_EPOCH, "w": w or 0, "h": h or 0,
            "dur": round(dur or 0), "fav": bool(fav), "score": score, "size": size, "name": name,
            "trashed": bool(trashed),
            "type": "screenshot" if (kind == 0 and sub == 10) else "screenrec" if (kind == 1 and sub == 103)
                    else "video" if kind == 1 else "photo",
        })
    return out


def thumb_path(u):
    return f"{LIB}/resources/derivatives/masters/{u[0]}/{u}_4_5005_c.jpeg"


def preview_path(u):
    big = f"{LIB}/resources/derivatives/{u[0]}/{u}_1_105_c.jpeg"
    return big if os.path.exists(big) else thumb_path(u)


def neighbour_pairs(photos):
    """Consecutive photos (by date) taken within BURST_SECONDS of each other."""
    return [(a, b) for a, b in zip(photos, photos[1:]) if b["date"] - a["date"] <= BURST_SECONDS]


def is_best(a, b):
    """True if a should be kept over b."""
    return (a["fav"], a["score"], a["w"] * a["h"], a["size"]) > (b["fav"], b["score"], b["w"] * b["h"], b["size"])


def group(photos, dist, threshold):
    """Chain neighbouring photos whose distance <= threshold into groups of 2+."""
    groups, cur = [], [photos[0]] if photos else []
    for a, b in zip(photos, photos[1:]):
        d = dist.get(f"{a['id']}|{b['id']}")
        if b["date"] - a["date"] <= BURST_SECONDS and d is not None and 0 <= d <= threshold:
            cur.append(b)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur = [b]
    if len(cur) > 1:
        groups.append(cur)
    result = []
    for g in groups:
        best = g[0]
        for p in g[1:]:
            if is_best(p, best):
                best = p
        result.append({"items": [dict(p, keep=(p is best or p["fav"])) for p in g]})
    result.sort(key=lambda r: -r["items"][0]["date"])
    return result


class Similarity:
    """Computes and caches similarity distances in the background."""

    def __init__(self):
        self.dist, self.done, self.total, self.error = {}, 0, 0, None
        try:
            with open(CACHE) as f:
                self.dist = json.load(f)
        except (OSError, ValueError):
            pass

    def run(self, photos):
        todo = [(a, b) for a, b in neighbour_pairs(photos)
                if f"{a['id']}|{b['id']}" not in self.dist
                and os.path.exists(thumb_path(a["id"])) and os.path.exists(thumb_path(b["id"]))]
        self.total = len(todo)
        if not todo:
            return
        try:
            proc = subprocess.Popen([HELPER, "similar"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            feed = threading.Thread(target=self._feed, args=(proc, todo), daemon=True)
            feed.start()
            for line in proc.stdout:
                a, b, d = line.rstrip("\n").split("\t")
                self.dist[f"{a}|{b}"] = float(d)
                self.done += 1
            proc.wait()
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE + ".tmp", "w") as f:
                json.dump(self.dist, f)
            os.replace(CACHE + ".tmp", CACHE)
        except Exception as e:  # surfaced in the UI instead of killing the server
            self.error = str(e)

    @staticmethod
    def _feed(proc, todo):
        for a, b in todo:
            proc.stdin.write(f"{a['id']}\t{thumb_path(a['id'])}\t{b['id']}\t{thumb_path(b['id'])}\n")
        proc.stdin.close()

    @property
    def ready(self):
        return self.done >= self.total


# ---------- Files ----------

def walk_files(root):
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            p = os.path.join(d, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            if stat.S_ISLNK(st.st_mode):  # symlinks (e.g. inside .framework bundles) would double-count
                continue
            out.append({"path": p, "rel": os.path.relpath(p, root), "size": st.st_size, "mtime": st.st_mtime})
    return out


def total_size(root):
    return sum(f["size"] for f in walk_files(root)) if os.path.isdir(root) else 0


def inside(path, root):
    real = os.path.realpath(path)
    return real.startswith(os.path.realpath(root) + os.sep) and os.path.isfile(real)


def app_containers():
    """iCloud data stored by apps (Books, Pages, games...) besides iCloud Drive."""
    out = []
    for name in os.listdir(MOBILE_DOCS) if os.path.isdir(MOBILE_DOCS) else []:
        p = os.path.join(MOBILE_DOCS, name)
        if name == "com~apple~CloudDocs" or name.startswith(".") or not os.path.isdir(p):
            continue
        size = total_size(p)
        if size >= 1_000_000:  # skip tiny app settings
            label = name.split("~")[-1]
            out.append({"name": "Books" if label == "iBooks" else label, "size": size, "app": "~" in name})
    return sorted(out, key=lambda x: -x["size"])


# ---------- Messages ----------

CHAT_DB = f"file:{HOME}/Library/Messages/chat.db?mode=ro"
THUMBS = os.path.join(HERE, ".cache", "mthumbs")
QL_SLOTS = threading.Semaphore(4)  # the browser asks for dozens of thumbnails at once


def contact_names():
    """Phone digits (last 10) / lowercase email -> contact name, from every Contacts source."""
    import glob
    names = {}
    base = os.path.join(HOME, "Library", "Application Support", "AddressBook")
    for path in glob.glob(f"{base}/AddressBook-v22.abcddb") + glob.glob(f"{base}/Sources/*/AddressBook-v22.abcddb"):
        try:
            db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            q = """select {col}, trim(coalesce(r.ZFIRSTNAME,'') || ' ' || coalesce(r.ZLASTNAME,'')), r.ZORGANIZATION
                   from {table} x join ZABCDRECORD r on r.Z_PK = x.ZOWNER"""
            for col, table in (("x.ZFULLNUMBER", "ZABCDPHONENUMBER"), ("x.ZADDRESS", "ZABCDEMAILADDRESS")):
                for key, name, org in db.execute(q.format(col=col, table=table)):
                    if key and (name or org):
                        names[handle_key(key)] = name or org
            db.close()
        except sqlite3.Error:
            continue
    return names


def handle_key(h):
    digits = re.sub(r"\D", "", h)
    return digits[-10:] if "@" not in h and len(digits) >= 7 else h.strip().lower()


def attachment_chats():
    """Attachment folder -> {"who": contact or chat name, "fromMe": bool}."""
    names = contact_names()
    out = {}
    try:
        db = sqlite3.connect(CHAT_DB, uri=True)
        rows = db.execute("""
            select a.filename, c.display_name, c.chat_identifier, h.id, m.is_from_me
            from attachment a
            join message_attachment_join j on j.attachment_id = a.ROWID
            join message m on m.ROWID = j.message_id
            left join chat_message_join cj on cj.message_id = m.ROWID
            left join chat c on c.ROWID = cj.chat_id
            left join handle h on h.ROWID = m.handle_id
            where a.filename like '~/Library/Messages/Attachments/%'""").fetchall()
        db.close()
    except sqlite3.Error:
        return out  # no Full Disk Access: rows just show without names
    for fn, display, ident, handle, from_me in rows:
        folder = os.path.dirname(os.path.expanduser(fn))
        other = ident or handle or ""
        who = display or names.get(handle_key(other)) or other
        out[folder] = {"who": who, "fromMe": bool(from_me)}
    return out


def message_attachments(limit=300):
    """One entry per attachment folder (Messages often keeps an original plus a converted copy)."""
    folders = {}
    for f in walk_files(MESSAGES):
        folders.setdefault(os.path.dirname(f["path"]), []).append(f)
    chats = attachment_chats()
    out = []
    for folder, files in folders.items():
        files.sort(key=lambda f: -f["size"])
        main = next((f for f in files if not UUID_NAME.match(os.path.basename(f["path"]))), files[0])
        out.append({"path": main["path"], "name": os.path.basename(main["path"]), "copies": len(files),
                    "size": sum(f["size"] for f in files), "mtime": main["mtime"], **chats.get(folder, {})})
    return sorted(out, key=lambda a: -a["size"])[:limit]


def quicklook_thumb(path):
    """PNG thumbnail of any file via Quick Look, cached. None if Quick Look can't make one in time."""
    import hashlib, shutil, tempfile
    os.makedirs(THUMBS, exist_ok=True)
    out = os.path.join(THUMBS, hashlib.sha1(path.encode()).hexdigest() + ".png")
    if not os.path.exists(out):
        tmp = tempfile.mkdtemp(prefix="tmp-", dir=THUMBS)
        try:
            with QL_SLOTS:
                subprocess.run(["qlmanage", "-t", "-s", "360", "-o", tmp, path], capture_output=True, timeout=15)
            made = os.listdir(tmp)
            if made:
                os.replace(os.path.join(tmp, made[0]), out)
        except subprocess.TimeoutExpired:
            pass  # the page shows the file type instead
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return out if os.path.exists(out) else None


# ---------- State ----------

STATE = {}  # filled in main: {"assets": [...], "sim": Similarity}


def live():
    return [a for a in STATE["assets"] if not a["trashed"]]


def photos_by_date():
    return sorted((a for a in live() if not a["video"]), key=lambda a: a["date"])


def overview():
    assets = live()
    cats = {}
    for a in assets:
        c = cats.setdefault(a["type"], {"count": 0, "size": 0})
        c["count"] += 1
        c["size"] += a["size"]
    trashed = [a for a in STATE["assets"] if a["trashed"]]
    sim = STATE["sim"]
    return {
        "photos": cats,
        "photosTotal": sum(a["size"] for a in assets),
        "recentlyDeleted": {"count": len(trashed), "size": sum(a["size"] for a in trashed)},
        "drive": total_size(DRIVE),
        "driveTrash": total_size(os.path.join(MOBILE_DOCS, ".Trash")),
        "apps": app_containers(),
        "messages": total_size(MESSAGES),
        "similarity": {"done": sim.done, "total": sim.total, "error": sim.error},
    }


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store" if ctype == "application/json" else "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    def send_file(self, path):
        try:
            with open(path, "rb") as f:
                self.send(200, f.read(), "image/jpeg")
        except OSError:
            self.send(404, {"error": "no thumbnail"})

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        parts = url.path.strip("/").split("/")
        if url.path == "/":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read().replace(b"__TOKEN__", TOKEN.encode()), "text/html; charset=utf-8")
        if parts[0] in ("thumb", "preview") and len(parts) == 2 and UUID_RE.match(parts[1]):
            return self.send_file(thumb_path(parts[1]) if parts[0] == "thumb" else preview_path(parts[1]))
        if url.path == "/api/access":
            return self.send(200, {"fullDisk": STATE["fullDisk"], "terminal": terminal_name()})
        if url.path == "/api/overview":
            return self.send(200, overview())
        if url.path == "/api/dupes":
            sim = STATE["sim"]
            threshold = float(q.get("t", ["0.45"])[0])
            groups = group(photos_by_date(), sim.dist, threshold) if sim.ready else []
            return self.send(200, {"ready": sim.ready, "done": sim.done, "total": sim.total,
                                   "error": sim.error, "groups": groups})
        if url.path == "/api/videos":
            return self.send(200, sorted((a for a in live() if a["type"] == "video"), key=lambda a: -a["size"]))
        if url.path == "/api/screens":
            return self.send(200, sorted((a for a in live() if a["type"] in ("screenshot", "screenrec")),
                                         key=lambda a: -a["size"]))
        if url.path == "/api/drive":
            return self.send(200, sorted(walk_files(DRIVE), key=lambda f: -f["size"])[:500])
        if url.path == "/api/messages":
            return self.send(200, message_attachments())
        if url.path == "/mthumb":
            p = q.get("p", [""])[0]
            thumb = quicklook_thumb(p) if inside(p, MESSAGES) else None
            if not thumb:
                return self.send(404, {"error": "no thumbnail"})
            with open(thumb, "rb") as f:
                return self.send(200, f.read(), "image/png")
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if self.headers.get("X-Token") != TOKEN:
            return self.send(403, {"error": "bad token"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self.send(400, {"error": "bad json"})
        path = urlparse(self.path).path

        if path == "/api/delete-photos":
            ids = [u for u in body.get("ids", []) if isinstance(u, str) and UUID_RE.match(u)]
            if not ids:
                return self.send(400, {"error": "nothing selected"})
            r = subprocess.run([HELPER, "delete", *ids], capture_output=True, text=True)
            if r.returncode == PHOTOS_DENIED:
                return self.send(403, {"error": r.stderr.strip(), "permission": "photos",
                                       "terminal": terminal_name()})
            if r.returncode:
                return self.send(409, {"error": r.stderr.strip() or "delete failed"})
            gone = set(ids)
            for a in STATE["assets"]:
                if a["id"] in gone:
                    a["trashed"] = True
            return self.send(200, {"deleted": int(r.stdout.strip() or 0)})

        if path == "/api/trash-files":
            paths = body.get("paths", [])
            if not paths or not all(isinstance(p, str) and inside(p, DRIVE) for p in paths):
                return self.send(400, {"error": "only iCloud Drive files can be trashed"})
            r = subprocess.run([HELPER, "trash", *paths], capture_output=True, text=True)
            if r.returncode:
                return self.send(409, {"error": r.stderr.strip() or "trash failed"})
            return self.send(200, {"trashed": len(paths)})

        if path == "/api/reveal":
            p = body.get("path", "")
            if not isinstance(p, str) or not (inside(p, DRIVE) or inside(p, MESSAGES)):
                return self.send(400, {"error": "not allowed"})
            subprocess.run(["open", "-R", p])
            return self.send(200, {})

        if path == "/api/open-file":
            p = body.get("path", "")
            if not isinstance(p, str) or not inside(p, MESSAGES):
                return self.send(400, {"error": "not allowed"})
            subprocess.run(["open", p])
            return self.send(200, {})

        if path == "/api/open":
            what = body.get("what")
            if what == "photos":
                subprocess.run(["open", "-a", "Photos"])
            elif what in SETTINGS_URLS:
                subprocess.run(["open", SETTINGS_URLS[what]])
            else:
                return self.send(400, {"error": "unknown"})
            return self.send(200, {})

        self.send(404, {"error": "not found"})


def build_helper():
    src = os.path.join(HERE, "helper.swift")
    if os.path.exists(HELPER) and os.path.getmtime(HELPER) >= os.path.getmtime(src):
        return
    print("Building helper (one time)...")
    subprocess.run(["swiftc", "-O", src, "-o", HELPER, "-Xlinker", "-sectcreate", "-Xlinker", "__TEXT",
                    "-Xlinker", "__info_plist", "-Xlinker", os.path.join(HERE, "Info.plist")], check=True)


def selftest():
    p = lambda i, t, **k: {"id": i, "date": t, "fav": False, "score": 0, "w": 1, "h": 1, "size": 1, **k}
    photos = [p("a", 0), p("b", 5, size=9), p("c", 10, fav=True), p("d", 500), p("e", 505), p("f", 2000)]
    dist = {"a|b": 0.2, "b|c": 0.3, "c|d": 0.1, "d|e": 0.9, "e|f": 0.1}
    g = group(photos, dist, 0.45)
    assert len(g) == 1, g                                   # c|d too far apart in time, d|e too different
    keep = {x["id"]: x["keep"] for x in g[0]["items"]}
    assert keep == {"a": False, "b": False, "c": True}, keep  # favourite wins
    photos[2]["fav"] = False
    keep = {x["id"]: x["keep"] for x in group(photos, dist, 0.45)[0]["items"]}
    assert keep == {"a": False, "b": True, "c": False}, keep  # then biggest file
    assert group(photos, dist, 0.1) == []
    assert not inside("/etc/passwd", DRIVE) and not inside(DRIVE + "/../x", DRIVE)
    print("selftest ok")


def check():
    """Setup report for people and agents: one line per requirement. True when ready to run."""
    import platform
    app, todo = terminal_name(), []

    def report(good, what, fix="", required=True):
        print(("ok   " if good else "FIX  " if required else "note ") + what)
        if not good:
            if fix:
                print("     " + fix)
            if required:
                todo.append(what)

    mac = platform.mac_ver()[0] or "0"
    report(int(mac.split(".")[0]) >= 13, f"macOS {mac} (needs 13 or later)", "Not supported: update macOS.")
    clt = subprocess.run(["xcode-select", "-p"], capture_output=True).returncode == 0
    report(clt, "Xcode Command Line Tools", "Run: xcode-select --install   then click Install in the window that opens.")
    helper = False
    if clt:
        try:
            build_helper()
            helper = True
        except (subprocess.CalledProcessError, OSError):
            pass
        report(helper, "Swift helper builds", "The compiler error is printed above.")
    fda = has_full_disk_access()
    report(fda, f"Full Disk Access for {app}",
           f'Run: open "{SETTINGS_URLS["fulldisk"]}"   turn on {app}, then quit {app} (Cmd+Q) and reopen it.')
    if fda:
        try:
            report(True, f"Photos library ({len(load_assets()):,} items)")
        except sqlite3.Error:
            report(False, "Photos library not found at ~/Pictures/Photos Library.photoslibrary",
                   "Only the default library is supported. The Photos tabs stay empty; the rest works.", required=False)
    if helper:
        status = subprocess.run([HELPER, "auth"], capture_output=True, text=True).stdout.strip()
        report(status in ("authorized", "notDetermined"),
               f"Photos access for {app}: {status}" + (" (macOS asks on the first delete)" if status == "notDetermined" else ""),
               f'Run: open "{SETTINGS_URLS["photos-privacy"]}"   and turn on {app}.')
    print("\nReady. Start it with: python3 cleaner.py" if not todo
          else f"\n{len(todo)} to fix. Then run: python3 cleaner.py --check")
    return not todo


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    if "--check" in sys.argv:
        sys.exit(0 if check() else 1)
    import glob, shutil
    for d in glob.glob(os.path.join(THUMBS, "tmp-*")):  # left behind if a previous run was killed mid-thumbnail
        shutil.rmtree(d, ignore_errors=True)
    build_helper()
    full_disk = has_full_disk_access()
    try:
        assets = load_assets()
    except sqlite3.Error as e:
        assets = []
        print(f"Can't read the Photos library ({e}).")
    if not full_disk:
        print(f"Full Disk Access is off for {terminal_name()}. The page shows how to turn it on.")
    STATE.update(assets=assets, sim=Similarity(), fullDisk=full_disk)
    threading.Thread(target=STATE["sim"].run, args=(photos_by_date(),), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)  # OS picks a free port
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"iCloud cleaner running at {url}  (Ctrl+C to stop)")
    if "--no-open" not in sys.argv:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
