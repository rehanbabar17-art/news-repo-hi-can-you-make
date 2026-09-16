import subprocess, sys, time, importlib.util, os

WORKER = (
    "import sys, requests; "
    "r = requests.get(sys.argv[1], timeout=(6, 10), "
    'headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}); '
    "r.raise_for_status(); "
    "sys.stdout.buffer.write(r.content)"
)

FEEDS = [
    ("Dawn",               "https://www.dawn.com/feeds/home"),
    ("Business Recorder",  "https://news.google.com/rss/search?q=site:brecorder.com+Pakistan&hl=en-PK&gl=PK&ceid=PK:en"),
    ("GNews Pakistan",     "https://news.google.com/rss/search?q=Pakistan+news+today&hl=en-PK&gl=PK&ceid=PK:en"),
    ("GNews Pakistan Intl","https://news.google.com/rss/search?q=Pakistan+world+news&hl=en-PK&gl=PK&ceid=PK:en"),
    ("GNews Imran Intl",   "https://news.google.com/rss/search?q=Imran+Khan&hl=en-US&gl=US&ceid=US:en"),
]

print(f"python: {sys.version}", flush=True)
for name, url in FEEDS:
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", WORKER, url],
            capture_output=True,
            timeout=12,
        )
        dt = time.time() - t0
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            print(f"{name}: FAILED rc={proc.returncode} in {dt:.2f}s -> {(err or ['?'])[-1][:120]}", flush=True)
        else:
            print(f"{name}: OK {len(proc.stdout)} bytes in {dt:.2f}s", flush=True)
    except subprocess.TimeoutExpired:
        dt = time.time() - t0
        print(f"{name}: TIMEOUT killed after {dt:.2f}s", flush=True)
    except Exception as e:
        dt = time.time() - t0
        print(f"{name}: EXC {type(e).__name__}: {e} after {dt:.2f}s", flush=True)

# Now test feedparser.parse on whatever we managed to grab (isolates parse vs fetch)
t0 = time.time()
spec = importlib.util.spec_from_file_location("fetch_news", "scripts/fetch_news.py")
os.environ["NTFY_TOPIC"] = "diag-topic"
mod = importlib.util.module_from_spec(spec)
# fetch_news.py exits if NTFY_TOPIC missing; diag sets it above
spec.loader.exec_module(mod)
print(f"module import ok in {time.time()-t0:.2f}s", flush=True)
