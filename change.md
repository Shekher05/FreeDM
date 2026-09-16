# TDM Architectural Solutions & Counter-Strategies (`change.md`)

This document serves as the finalized reference guide for countering TDM's known download limitations. All approaches documented here are strictly designed to preserve TDM's core invariants: **single runtime dependency (`requests`), stdlib-first architecture, pure library engine (`tdm/engine.py`), fixed threading concurrency, and zero credential leaks to disk.**

---

## 📋 Summary of Finalized Approaches

| # | Limitation Area | Finalized Approach | Key Mechanism |
| :--- | :--- | :--- | :--- |
| **1** | **Google Drive & JS File Lockers** | **Approach 1: Extension Catch** | Browser handles countdowns/interstitials; `background.js` catches direct stream URL. |
| **2** | **Authenticated / Session-Gated Links** | **Approach 1: Cookie & Header Forwarding** | Extension reads `HttpOnly` cookies via `chrome.cookies.getAll`; passes RAM-only to TDM. |
| **3** | **Expired Presigned URLs (S3/CDN)** | **Approach B: Extension Auto-Match** | Re-clicking download in Chrome attaches fresh URL to existing `.part` via matching `ETag`/`size`. |
| **4** | **Cloudflare & Anti-Bot Filters** | **Approach B: Realistic Engine Headers** | Upgrade default session headers in `engine.py` to modern Chrome signatures. |
| **5** | **Streaming & Adaptive Media** | **Approach C: Browser Media Flow** | Extension catches converted progressive `.mp4` streams from web converter tools. |
| **6** | **Premature Segment Stream Drops** | **Approach A: Sub-Segment Resubmission** | Post-pool scan in `_download_segmented()` re-fetches only missing byte slices `[start+done, end]`. |

---

## 🛠️ Detailed Architecture & Implementation Reference

---

### 1. Google Drive & JavaScript File Lockers (MediaFire, RapidGator, etc.)

* **The Problem:** 
  * Google Drive serves an HTML virus-scan warning with a `confirm=<token>` form for files >100 MB.
  * MediaFire / file lockers generate direct download buttons via client-side JavaScript timers.
* **The Counter-Strategy:** **Browser Catch (`tdm catch`)**
  * **Flow:**
    1. User navigates to the page in Chrome and clicks "Download anyway" / waits out the timer.
    2. Chrome evaluates JavaScript and executes redirects to the direct CDN stream (e.g. `https://drive.usercontent.google.com/download?...` or `https://download123.mediafire.com/...`).
    3. `chrome.downloads.onCreated` fires in `extension/background.js`.
    4. Extension sends `POST /ext/flag` with the direct URL to `tdm catch` (port 8765) and cancels Chrome's built-in 1-connection download.
    5. TDM prompts `[y/n]` in the terminal and downloads the file across 8 parallel segments.
* **Invariants Preserved:**
  * Zero web scrapers in Python.
  * Immune to website HTML/CSS redesigns.
  * `tdm/engine.py` remains 100% pure.

---

### 2. Authenticated / Session-Gated Downloads (Private Drive, Paywalls, Intranets)

* **The Problem:** 
  * Downloading private files requires session cookies (`Cookie: session_id=...`) or `Authorization: Bearer ...` headers. Plain Python `requests` calls return `401 Unauthorized` or `403 Forbidden`.
* **The Counter-Strategy:** **Extension Cookie & Header Forwarding**
  * **Flow:**
    1. Manifest gains `"cookies"` permission: `permissions: ["downloads", "cookies"]`.
    2. In `extension/background.js`:
       ```javascript
       const cookies = await chrome.cookies.getAll({ url: item.url });
       const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join('; ');
       
       await fetch(`${BASE}/ext/flag`, {
         method: "POST",
         headers: { "Content-Type": "application/json", "X-Ext-Token": token },
         body: JSON.stringify({
           url: item.url,
           headers: {
             "Cookie": cookieHeader,
             "Referer": item.referrer || "",
             "User-Agent": navigator.userAgent
           }
         })
       });
       ```
    3. `tdm/service.py` puts `(url, headers)` into the pending queue.
    4. `tdm/engine.py` accepts optional `headers: dict[str, str] | None = None` in `download()`.
* **Security Guard:**
  * Per `CONSTRAINTS.md`, forwarded `Cookie` and `Authorization` headers are held **in RAM only** for the active session and **never persisted** to `queue.json` or `.tdm.json`.

---

### 3. Expired Presigned URLs (AWS S3, CloudFront, GCS)

* **The Problem:**
  * Long or paused downloads fail when the query signature (`?X-Amz-Expires=3600&X-Amz-Signature=...`) expires, returning HTTP `403 Forbidden`.
* **The Counter-Strategy:** **Extension Auto-Match & Sidecar Resume**
  * **Flow:**
    1. A long download halts with `403 Forbidden (Link expired)`.
    2. User refreshes the web page in Chrome and clicks "Download" again.
    3. Extension flags the fresh presigned URL to `tdm catch`.
    4. `tdm catch` probes the new link, detecting that:
       * `new_probe.filename == existing_download.filename`
       * `new_probe.size == existing_download.size`
       * `new_probe.validator == existing_download.etag`
    5. TDM prompts: `"Found existing incomplete download (32 GB / 50 GB done). Resume with fresh link? [y/n]"`.
    6. Pressing `'y'` updates the URL in memory and `.tdm.json`, seamlessly resuming from the exact byte offset without re-downloading existing `.part` data.

---

### 4. Cloudflare & Anti-Bot Protection

* **The Problem:**
  * Many CDNs block default `python-requests` User-Agents with `403 Forbidden` or `503 Service Unavailable` challenge pages.
* **The Counter-Strategy:** **Realistic Default Browser Headers**
  * **Flow:**
    * In `tdm/engine.py`, replace generic default session headers with modern browser signatures:
      ```python
      _DEFAULT_HEADERS = {
          "User-Agent": (
              "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36"
          ),
          "Accept": "*/*",
          "Accept-Language": "en-US,en;q=0.9",
          "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24"',
          "Sec-Ch-Ua-Mobile": "?0",
          "Sec-Ch-Ua-Platform": '"Windows"',
      }
      ```
    * Requests in both CLI and service bypass naive user-agent filters out of the box.

---

### 5. Streaming & Adaptive Media (YouTube, Twitch, HLS, DASH)

* **The Problem:**
  * Streaming platforms use playlist manifests (`.m3u8`, `.mpd`) and tiny 2-second segments rather than single range-capable media files. Embedding `ffmpeg` or media extractors violates TDM's scope.
* **The Counter-Strategy:** **Browser Extension Media Flow**
  * **Flow:**
    1. User converts or requests media via standard web converter tools (e.g. SaveFrom, Y2Mate) in Chrome.
    2. Converter generates a signed progressive `.mp4` direct stream link (e.g. `sf-converter.com/prod-new/download/...`).
    3. Chrome extension intercepts the direct stream download event.
    4. TDM's 8 worker threads accelerate the progressive MP4 download at maximum bandwidth.
    5. *Note:* Filename sanitization in `derive_filename()` automatically strips Windows NTFS illegal characters (`|`, `<`, `>`, `"`, `?`, `*`) from video titles.

---

### 6. Premature / Incomplete Segment Stream Drops (Finding 5)

* **The Problem:**
  * A remote CDN closes a segment connection early (`TCP FIN`) without raising an exception. The worker thread exits normally with `progress[idx] < need`, causing TDM's final integrity gate to reject the entire download.
* **The Counter-Strategy:** **Sub-Segment Resubmission Loop**
  * **Flow:**
    1. In `tdm/engine.py` (`_download_segmented`), after the initial `ThreadPoolExecutor` completes:
       ```python
       # Bounded retry loop for incomplete segment slices
       for attempt in range(3):
           incomplete = []
           for idx, (done, (start, end)) in enumerate(zip(progress, ranges)):
               need = end - start + 1
               if done < need:
                   missing_start = start + done
                   incomplete.append((idx, missing_start, end))
           
           if not incomplete:
               break
               
           # Resubmit only missing slices [missing_start, end]
           with ThreadPoolExecutor(max_workers=len(incomplete)) as ex:
               futures = [
                   ex.submit(_download_segment, resolved_url, part, s, e, idx, progress, cb)
                   for idx, s, e in incomplete
               ]
               wait(futures, return_when=FIRST_EXCEPTION)
       ```
    2. Runs the final integrity check only after retrying missing byte slices.
    3. Preserves all previously written bytes with zero bandwidth wasted.
