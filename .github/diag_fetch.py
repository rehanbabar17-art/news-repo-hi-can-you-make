import subprocess, sys, time

WORKER = (
    "import sys, requests; "
    "r = requests.get(sys.argv[1], timeout=(6, 10), "
    'headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}); '
    "r.raise_for_status(); "
    "sys.stdout.buffer.write(r.content)"
)
URL = "https://news.google.com/rss/search?q=Imran+Khan&hl=en-US&gl=US&ceid=US:en"

def attempt(i):
    t0 = time.time()
    # 1) curl with its own max-time
    try:
        c = subprocess.run(["curl", "-sS", "--max-time", "15", "-o", "/dev/null", "-w", "%{http_code} %{size_download}", URL],
                           capture_output=True, timeout=20)
        curl_info = f"curl rc={c.returncode} out={c.stdout.decode(errors='replace').strip()[:60]}"
    except subprocess.TimeoutExpired:
        curl_info = "curl TIMEOUT(20s)"
    except Exception as e:
        curl_info = f"curl EXC {e}"
    dt1 = time.time() - t0

    # 2) python subprocess worker with 12s kill
    t1 = time.time()
    try:
        p = subprocess.run([sys.executable, "-c", WORKER, URL], capture_output=True, timeout=12)
        if p.returncode != 0:
            py_info = f"py FAIL rc={p.returncode} err={p.stderr.decode(errors='replace').strip().splitlines()[-1][:80]}"
        else:
            py_info = f"py OK {len(p.stdout)}B"
    except subprocess.TimeoutExpired:
        py_info = f"py KILLED({time.time()-t1:.2f}s)"
    except Exception as e:
        py_info = f"py EXC {type(e).__name__}: {e}"
    dt2 = time.time() - t1
    print(f"[{i:02d}] curl {dt1:5.2f}s | {curl_info} | py {dt2:5.2f}s | {py_info}", flush=True)

print(f"start {time.strftime('%H:%M:%SZ')} python {sys.version.split()[0]}", flush=True)
for i in range(30):
    attempt(i)
    time.sleep(1)
print("DONE", flush=True)
