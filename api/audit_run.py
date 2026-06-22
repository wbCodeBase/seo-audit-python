"""
/api/audit_run  — Deep Crawler + AI Explainer
Architecture:
  1. CRAWL  — fetch homepage + up to 5 internal pages, robots.txt, sitemap,
               HTTP headers, DNS/SPF/DMARC records, all via urllib (no browser)
  2. COLLECT — extract every SEO signal from raw HTML/headers/DNS
  3. EXPLAIN — send the raw signals to Claude; AI writes issue explanations
               and prioritised recommendations (no searching, no hallucination)
  4. STORE   — merge crawler data + AI prose into the Redis job record
"""

import json, os, re, threading, time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, urljoin, quote as url_quote
import urllib.request, urllib.error


# ── Redis ─────────────────────────────────────────────────────────────────────

def get_redis():
    url   = os.environ.get("UPSTASH_REDIS_REST_URL")
    token = os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    if not url or not token:
        return None
    try:
        from upstash_redis import Redis
        return Redis(url=url, token=token)
    except Exception:
        return None

def store_set(job_id, value):
    r = get_redis()
    if r:
        r.set(f"seo:{job_id}", json.dumps(value), ex=3600)

def store_get(job_id):
    r = get_redis()
    if not r:
        return {}
    v = r.get(f"seo:{job_id}")
    return json.loads(v) if v else {}


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — DEEP CRAWLER
# ══════════════════════════════════════════════════════════════════════════════

UA = "Mozilla/5.0 (compatible; DeepSEOBot/2.0)"
MAX_PAGES     = 6
FETCH_TIMEOUT = 10

def _fetch(url, timeout=FETCH_TIMEOUT, method="GET"):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            body = r.read().decode("utf-8", errors="replace")
            return body, r.url, r.status, hdrs
    except urllib.error.HTTPError as e:
        return "", url, e.code, {}
    except Exception:
        return "", url, 0, {}

def _head(url, timeout=5):
    _, final, status, hdrs = _fetch(url, timeout=timeout, method="HEAD")
    return status, hdrs

def _parallel_head(urls, timeout=5):
    results = {}
    lock = threading.Lock()
    def _chk(u):
        st, hdrs = _head(u, timeout)
        with lock:
            results[u] = {"ok": 0 < st < 400, "status": st, "headers": hdrs}
    threads = [threading.Thread(target=_chk, args=(u,)) for u in urls]
    for t in threads: t.daemon = True; t.start()
    for t in threads: t.join(timeout + 1)
    return results


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _tag_text(html, tag):
    m = re.search(rf'<{tag}[^>]*>(.*?)</{tag}>', html, re.I | re.S)
    return re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else ""

def _meta(html, name):
    for p in [
        rf'<meta\s+name=["\']?{re.escape(name)}["\']?\s+content=["\']([^"\']*)["\']',
        rf'<meta\s+content=["\']([^"\']*)["\']?\s+name=["\']?{re.escape(name)}["\']?',
    ]:
        m = re.search(p, html, re.I)
        if m: return m.group(1).strip()
    return ""

def _og(html, prop):
    for p in [
        rf'<meta\s+property=["\']?og:{re.escape(prop)}["\']?\s+content=["\']([^"\']*)["\']',
        rf'<meta\s+content=["\']([^"\']*)["\']?\s+property=["\']?og:{re.escape(prop)}["\']?',
    ]:
        m = re.search(p, html, re.I)
        if m: return m.group(1).strip()
    return ""

def _twitter(html, name):
    for p in [
        rf'<meta\s+name=["\']?twitter:{re.escape(name)}["\']?\s+content=["\']([^"\']*)["\']',
        rf'<meta\s+content=["\']([^"\']*)["\']?\s+name=["\']?twitter:{re.escape(name)}["\']?',
    ]:
        m = re.search(p, html, re.I)
        if m: return m.group(1).strip()
    return ""

def _strip_scripts(html):
    """Remove <script>, <style>, <noscript> blocks before text extraction."""
    html = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.I | re.S)
    html = re.sub(r'<style[^>]*>.*?</style>',   ' ', html, flags=re.I | re.S)
    html = re.sub(r'<noscript[^>]*>.*?</noscript>', ' ', html, flags=re.I | re.S)
    return html

def _strip(html):
    return re.sub(r'<[^>]+>', ' ', html)

def _strip_clean(html):
    """Strip scripts then tags — use for word count, keywords, email scan."""
    return _strip(_strip_scripts(html))

def _wc(html):
    text = _strip_clean(html)
    return len(re.sub(r'\s+', ' ', text).split())

def _headings(html):
    result = {}
    for i in range(1, 7):
        tags = re.findall(rf'<h{i}[^>]*>(.*?)</h{i}>', html, re.I | re.S)
        result[f"h{i}"] = [re.sub(r'<[^>]+>', '', t).strip() for t in tags]
    return result

def _canonical(html):
    m = re.search(r'<link[^>]*rel=["\']?canonical["\']?[^>]*href=["\']([^"\']+)["\']', html, re.I)
    return m.group(1).strip() if m else ""

def _schema_types(html):
    return list(set(re.findall(r'"@type"\s*:\s*"([^"]+)"', html)))

def _all_links(html, base):
    hrefs = re.findall(r'<a[^>]+href=["\']([^"\'#?][^"\']*)["\']', html, re.I)
    out = []
    for h in hrefs:
        try:
            full = urljoin(base, h)
            if full.startswith("http"):
                out.append(full)
        except Exception:
            pass
    return list(dict.fromkeys(out))

def _internal(links, netloc):
    return [l for l in links if urlparse(l).netloc == netloc]

def _images(html):
    return re.findall(r'<img[^>]+>', html, re.I)

def _missing_alt(imgs):
    """Return images with no alt attribute at all (truly missing)."""
    return [i for i in imgs if 'alt=' not in i.lower()]

def _tech(html, headers):
    checks = [
        ("WordPress",      r'wp-content|wp-includes'),
        ("Shopify",        r'cdn\.shopify\.com'),
        ("Wix",            r'wix\.com|wixstatic\.com'),
        ("Webflow",        r'webflow\.com'),
        ("Next.js",        r'__NEXT_DATA__|/_next/'),
        ("Nuxt.js",        r'__nuxt|/_nuxt/'),
        ("React",          r'react\.production|data-reactroot'),
        ("Vue.js",         r'__vue__'),
        ("Angular",        r'ng-version'),
        ("jQuery",         r'jquery\.min\.js|jquery-\d'),
        ("Bootstrap",      r'bootstrap\.min\.(css|js)'),
        ("Tailwind CSS",   r'tailwindcss'),
        ("Cloudflare",     r'__cf_bm'),
        ("Google Analytics", r'google-analytics\.com|gtag\(|G-[A-Z0-9]{6,}'),
        ("Google Tag Manager", r'googletagmanager\.com'),
        ("Facebook Pixel", r"fbq\(|facebook\.com/tr"),
        ("HubSpot",        r'hs-scripts'),
        ("Hotjar",         r'hotjar\.com'),
        ("Django",         r'csrfmiddlewaretoken'),
        ("AMP",            r'<html[^>]*\bamp\b'),
    ]
    server = headers.get("server", "").lower()
    found = []
    if "nginx"  in server: found.append("Nginx")
    if "apache" in server: found.append("Apache")
    for name, pat in checks:
        if re.search(pat, html, re.I):
            found.append(name)
    return list(dict.fromkeys(found))

STOP = {
    'the','a','an','and','or','but','in','on','at','to','for','of','with',
    'is','are','was','were','be','been','this','that','it','by','from','as',
    'into','about','which','have','has','had','not','do','does','did','we',
    'you','your','our','more','can','will','get','all','any','its','use',
    'also','so','if','than','then','up','out','no','my','he','she','they',
    'their','us','page','click','here','read','view','learn','find',
}

def _top_kws(html, title, meta, n=8):
    text  = _strip_clean(html).lower()  # exclude script/style content
    words = re.sub(r'[^\w\s]', '', text).split()
    words = [w for w in words if len(w) > 3 and w not in STOP]
    freq  = {}
    for w in words: freq[w] = freq.get(w, 0) + 1
    top = sorted(freq.items(), key=lambda x: -x[1])[:n]
    hdg  = " ".join(re.sub(r'<[^>]+>', '', h).lower()
                    for h in re.findall(r'<h[1-6][^>]*>.*?</h[1-6]>', html, re.I | re.S))
    return [{"p": kw, "ti": kw in title.lower(), "me": kw in meta.lower(),
             "hd": kw in hdg, "f": f} for kw, f in top]

def _social(html):
    links = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
    nets  = [
        ("Facebook",  "facebook.com",  "F",  "#1877F2"),
        ("Instagram", "instagram.com", "Ig", "#E1306C"),
        ("LinkedIn",  "linkedin.com",  "in", "#0A66C2"),
        ("X/Twitter", "x.com",         "X",  "#000000"),
        ("YouTube",   "youtube.com",   "▶",  "#FF0000"),
        ("Pinterest", "pinterest.com", "P",  "#E60023"),
        ("TikTok",    "tiktok.com",    "Tt", "#000000"),
    ]
    return [{"name": n, "url": next((l for l in links if d in l), ""),
             "ico": ic, "bg": bg, "c": "#fff",
             "linked": any(d in l for l in links), "stat": ""}
            for n, d, ic, bg in nets]

def _dns_txt(domain):
    try:
        api = f"https://dns.google/resolve?name={domain}&type=TXT"
        req = urllib.request.Request(api, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read())
        return [a.get("data","").strip('"').replace('" "','')
                for a in data.get("Answer", [])]
    except Exception:
        return []

def _spf(records):
    for r in records:
        if r.startswith("v=spf1"): return True, r
    return False, ""

def _dmarc(domain):
    records = _dns_txt(f"_dmarc.{domain}")
    for r in records:
        if "v=DMARC1" in r: return True, r
    return False, ""

def _parse_robots(txt):
    """Parse robots.txt, collecting Disallow rules only from User-agent: * block."""
    dis, delay, smaps = [], None, []
    in_star_block = False  # True when inside a User-agent: * section
    for line in txt.splitlines():
        l = line.strip()
        if not l or l.startswith("#"):
            continue
        ll = l.lower()
        if ll.startswith("user-agent:"):
            agent = l[11:].strip()
            in_star_block = (agent == "*")
        elif ll.startswith("sitemap:"):
            smaps.append(l[8:].strip())
        elif in_star_block:
            if ll.startswith("disallow:"):
                v = l[9:].strip()
                if v: dis.append(v)
            elif ll.startswith("crawl-delay:"):
                try: delay = float(l[12:].strip())
                except: pass
    return dis, delay, smaps

def _parse_sitemap(xml):
    return re.findall(r'<loc>\s*(https?://[^<]+?)\s*</loc>', xml, re.I)[:200]

def _grade(score):
    for t, g in [(93,"A+"),(87,"A"),(82,"A-"),(77,"B+"),(72,"B"),(67,"B-"),
                 (60,"C+"),(53,"C"),(45,"C-"),(35,"D+"),(25,"D"),(15,"D-")]:
        if score >= t: return g
    return "F"




# ══════════════════════════════════════════════════════════════════════════════
# CHROME UX REPORT (CrUX) — extracted from PSI API response (free with PSI key)
# Falls back to a keyless attempt against the public CrUX endpoint
# ══════════════════════════════════════════════════════════════════════════════

def fetch_crux(url, psi_data=None):
    """
    Extract real-user CrUX field data.

    Priority:
      1. If PSI was already called, parse loadingExperience from that response
      2. Try CrUX API with PAGESPEED_API_KEY (same key, free quota)
      3. Return None — no data available

    Metrics (75th percentile, real Chrome users):
      lcp, inp, cls, fcp, ttfb — with FAST/MODERATE/SLOW ratings
    """
    def _fmt_ms(v):
        if v is None: return "N/A"
        return f"{v/1000:.2f}s" if v >= 1000 else f"{int(v)}ms"

    def _fmt_cls(v):
        if v is None: return "N/A"
        return f"{float(v):.3f}"

    def _rating(key, val):
        thresholds = {
            "lcp":  (2500, 4000),
            "inp":  (200,  500),
            "cls":  (0.1,  0.25),
            "fcp":  (1800, 3000),
            "ttfb": (800,  1800),
        }
        if val is None: return ""
        good, poor = thresholds.get(key, (0, 0))
        if val <= good: return "FAST"
        if val >= poor: return "SLOW"
        return "MODERATE"

    def _parse_le(le, source):
        """Parse a loadingExperience block from PSI response."""
        metrics = le.get("metrics", {})
        if not metrics:
            return None

        def _p75(key):
            return metrics.get(key, {}).get("percentile")

        lcp_ms  = _p75("LARGEST_CONTENTFUL_PAINT_MS")
        inp_ms  = _p75("INTERACTION_TO_NEXT_PAINT")
        cls_raw = _p75("CUMULATIVE_LAYOUT_SHIFT_SCORE")
        fcp_ms  = _p75("FIRST_CONTENTFUL_PAINT_MS")
        ttfb_ms = _p75("EXPERIMENTAL_TIME_TO_FIRST_BYTE")

        cls_score = (cls_raw / 100) if (cls_raw and cls_raw > 1) else cls_raw

        cwv_pass = (
            lcp_ms  is not None and lcp_ms  <= 2500 and
            (inp_ms is None     or  inp_ms  <= 200) and
            cls_score is not None and cls_score <= 0.1
        )

        return {
            "lcp":         _fmt_ms(lcp_ms),
            "inp":         _fmt_ms(inp_ms),
            "cls":         _fmt_cls(cls_score),
            "fcp":         _fmt_ms(fcp_ms),
            "ttfb":        _fmt_ms(ttfb_ms),
            "lcp_rating":  _rating("lcp",  lcp_ms),
            "inp_rating":  _rating("inp",  inp_ms),
            "cls_rating":  _rating("cls",  cls_score),
            "fcp_rating":  _rating("fcp",  fcp_ms),
            "ttfb_rating": _rating("ttfb", ttfb_ms),
            "pass":        cwv_pass,
            "source":      source,
            "has_data":    True,
        }

    # ── Path 1: reuse PSI data already fetched ──
    if psi_data:
        le = psi_data.get("loadingExperience", {})
        if le.get("metrics"):
            src_type = "url" if le.get("id","").rstrip("/") == url.rstrip("/") else "origin"
            parsed = _parse_le(le, src_type)
            if parsed:
                return parsed
        # Try originLoadingExperience as fallback
        ole = psi_data.get("originLoadingExperience", {})
        if ole.get("metrics"):
            parsed = _parse_le(ole, "origin")
            if parsed:
                return parsed

    # ── Path 2: CrUX API with PSI key ──
    api_key = os.environ.get("PAGESPEED_API_KEY", "").strip()
    if not api_key:
        return None

    parsed_url = urlparse(url)
    origin     = f"{parsed_url.scheme}://{parsed_url.netloc}"

    for payload, source in [
        ({"url": url,       "formFactor": "PHONE"},   "url"),
        ({"origin": origin, "formFactor": "PHONE"},   "origin"),
        ({"origin": origin},                           "origin"),
    ]:
        try:
            endpoint = f"https://chromeuxreport.googleapis.com/v1/records:queryRecord?key={api_key}"
            data_b   = json.dumps(payload).encode()
            req      = urllib.request.Request(
                endpoint, data=data_b,
                headers={"Content-Type": "application/json", "User-Agent": UA},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                resp = json.loads(r.read())
            if "record" not in resp:
                continue

            # CrUX API uses different key names
            def _crux_p75(key):
                m = resp["record"].get("metrics", {}).get(key, {})
                return m.get("percentiles", {}).get("p75")

            lcp_ms  = _crux_p75("largest_contentful_paint")
            inp_ms  = _crux_p75("interaction_to_next_paint")
            cls_raw = _crux_p75("cumulative_layout_shift")
            fcp_ms  = _crux_p75("first_contentful_paint")
            ttfb_ms = _crux_p75("experimental_time_to_first_byte")
            cls_score = (cls_raw / 100) if (cls_raw and cls_raw > 1) else cls_raw

            cwv_pass = (
                lcp_ms  is not None and lcp_ms  <= 2500 and
                (inp_ms is None     or  inp_ms  <= 200) and
                cls_score is not None and cls_score <= 0.1
            )

            return {
                "lcp":         _fmt_ms(lcp_ms),
                "inp":         _fmt_ms(inp_ms),
                "cls":         _fmt_cls(cls_score),
                "fcp":         _fmt_ms(fcp_ms),
                "ttfb":        _fmt_ms(ttfb_ms),
                "lcp_rating":  _rating("lcp",  lcp_ms),
                "inp_rating":  _rating("inp",  inp_ms),
                "cls_rating":  _rating("cls",  cls_score),
                "fcp_rating":  _rating("fcp",  fcp_ms),
                "ttfb_rating": _rating("ttfb", ttfb_ms),
                "pass":        cwv_pass,
                "source":      source,
                "has_data":    True,
            }
        except Exception:
            continue

    return None

# ══════════════════════════════════════════════════════════════════════════════
# PAGESPEED INSIGHTS API
# ══════════════════════════════════════════════════════════════════════════════

def _psi(url, strategy, api_key):
    """
    Call PageSpeed Insights API for mobile or desktop.
    Returns dict with score, CWV metrics, lab data, opportunities.
    Returns None on any failure.
    """
    endpoint = (
        f"https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
        f"?url={url_quote(url, safe='')}"
        f"&strategy={strategy}"
        f"&key={api_key}"
        f"&category=PERFORMANCE"
    )
    try:
        req = urllib.request.Request(endpoint, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=55) as r:
            data = json.loads(r.read())
    except Exception:
        return None

    cats  = data.get("lighthouseResult", {}).get("categories", {})
    audits = data.get("lighthouseResult", {}).get("audits", {})
    crux  = data.get("loadingExperience", {}).get("metrics", {})

    def _ms(key):
        v = audits.get(key, {}).get("displayValue", "")
        return v if v else "N/A"

    def _num(key):
        return audits.get(key, {}).get("numericValue")

    def _crux(key, field="category"):
        m = crux.get(key, {})
        if not m: return None
        return m.get("percentile") or m.get(field)

    score = round((cats.get("performance", {}).get("score") or 0) * 100)

    # Core metrics
    lcp_ms  = _num("largest-contentful-paint")
    inp_ms  = _num("total-blocking-time")   # TBT as INP proxy when CrUX unavailable
    cls_num = _num("cumulative-layout-shift")
    fcp_ms  = _num("first-contentful-paint")

    def _fmt_sec(ms):
        if ms is None: return "N/A"
        return f"{ms/1000:.1f}s"

    def _fmt_cls(v):
        if v is None: return "N/A"
        return f"{v:.3f}"

    # CrUX field data (real user metrics) — preferred over lab data
    crux_lcp = crux.get("LARGEST_CONTENTFUL_PAINT_MS", {}).get("percentile")
    crux_inp = crux.get("INTERACTION_TO_NEXT_PAINT", {}).get("percentile")
    crux_cls = crux.get("CUMULATIVE_LAYOUT_SHIFT_SCORE", {}).get("percentile")
    crux_overall = data.get("loadingExperience", {}).get("overall_category")

    # Opportunities (improvement suggestions)
    opp_keys = [
        "render-blocking-resources", "unused-css-rules", "unused-javascript",
        "uses-optimized-images", "uses-webp-images", "uses-responsive-images",
        "efficiently-encode-images", "enable-text-compression",
        "uses-long-cache-ttl", "server-response-time",
        "eliminate-render-blocking-resources", "defer-offscreen-images",
    ]
    opps = []
    for k in opp_keys:
        a = audits.get(k, {})
        if a.get("score", 1) is not None and (a.get("score") or 1) < 0.9:
            savings = a.get("displayValue", "")
            if savings and savings != "N/A":
                opps.append({"n": a.get("title", k), "s": savings})
    opps = opps[:6]

    # Build CWV pass/fail — use CrUX if available, else lab thresholds
    if crux_lcp and crux_cls is not None:
        cwv_pass = (
            crux_lcp <= 2500 and
            (crux_inp is None or crux_inp <= 200) and
            (crux_cls / 100 if crux_cls > 1 else crux_cls) <= 0.1
        )
        lcp_display = _fmt_sec(crux_lcp)
        inp_display = f"{crux_inp}ms" if crux_inp else _ms("total-blocking-time")
        cls_display = _fmt_cls((crux_cls / 100) if crux_cls and crux_cls > 1 else crux_cls)
        crux_source = True
    else:
        cwv_pass = (
            (lcp_ms or 9999) <= 2500 and
            (cls_num or 9999) <= 0.1
        )
        lcp_display = _fmt_sec(lcp_ms)
        inp_display = _ms("total-blocking-time")
        cls_display = _fmt_cls(cls_num)
        crux_source = False

    # Extract screenshot (PSI provides base64 final screenshot)
    screenshot_b64 = ""
    try:
        ss = audits.get("final-screenshot", {}).get("details", {})
        screenshot_b64 = ss.get("data", "").replace("data:image/jpeg;base64,", "")
    except Exception:
        pass

    return {
        "score":     score,
        "fcp":       _fmt_sec(fcp_ms),
        "si":        _ms("speed-index"),
        "lcp":       lcp_display,
        "tti":       _ms("interactive"),
        "tbt":       _ms("total-blocking-time"),
        "cls":       cls_display,
        "opps":      opps,
        "cwv_pass":  cwv_pass,
        "crux":      crux_source,
        "crux_overall": crux_overall or "",
        "_screenshot": screenshot_b64,
        "_raw":      data,
    }


def fetch_pagespeed(url):
    """
    Fetch both mobile and desktop PSI results in parallel.
    Requires PAGESPEED_API_KEY environment variable.
    Returns (mobile_dict, desktop_dict, cwv_dict) or (None, None, None).
    """
    api_key = os.environ.get("PAGESPEED_API_KEY", "").strip()
    if not api_key:
        return None, None, None

    mob_result  = [None]
    desk_result = [None]

    def _fetch_mob():
        mob_result[0] = _psi(url, "mobile", api_key)

    def _fetch_desk():
        desk_result[0] = _psi(url, "desktop", api_key)

    t1 = threading.Thread(target=_fetch_mob)
    t2 = threading.Thread(target=_fetch_desk)
    t1.daemon = True; t2.daemon = True
    t1.start(); t2.start()
    t1.join(60); t2.join(60)

    mob  = mob_result[0]
    desk = desk_result[0]

    # Derive CWV from mobile (Google uses mobile for CWV assessment)
    cwv = None
    if mob:
        cwv = {
            "lcp":  mob["lcp"],
            "inp":  mob["tbt"],   # TBT proxy when INP not in lab data
            "cls":  mob["cls"],
            "pass": mob["cwv_pass"],
        }
        if mob.get("crux"):
            cwv["inp"] = next(
                (v for v in [mob.get("crux_inp")] if v), mob["tbt"]
            )

    return mob, desk, cwv

# ══════════════════════════════════════════════════════════════════════════════
# LAYER 2 — COLLECT ALL SIGNALS
# ══════════════════════════════════════════════════════════════════════════════

def collect_signals(start_url):
    parsed = urlparse(start_url)
    domain = parsed.netloc.replace("www.", "")
    base   = f"{parsed.scheme}://{parsed.netloc}"

    # Fetch homepage
    t0 = time.time()
    html, final_url, status, headers = _fetch(start_url)
    ttfb = round(time.time() - t0, 3)

    https_ok   = final_url.startswith("https://")
    redirected = final_url.rstrip("/") != start_url.rstrip("/")

    # Title / meta
    title_t   = _tag_text(html, "title")
    title_len = len(title_t)
    title_ok  = 45 <= title_len <= 65

    meta_t   = _meta(html, "description")
    meta_len = len(meta_t)
    meta_ok  = 120 <= meta_len <= 165

    # Headings
    headings  = _headings(html)
    h1s       = headings.get("h1", [])
    h1_count  = len(h1s)
    h1_status = "good" if h1_count == 1 else ("multiple" if h1_count > 1 else "missing")
    hfreq     = [{"t": f"H{i}", "n": len(headings.get(f"h{i}", []))}
                 for i in range(2, 7) if headings.get(f"h{i}")]

    canon    = _canonical(html)
    lang_m   = re.search(r'<html[^>]*lang=["\']([^"\']+)["\']', html, re.I)
    lang     = lang_m.group(1).strip() if lang_m else ""
    noindex  = bool(re.search(
        r'<meta[^>]*name=["\']robots["\'][^>]*content=["\'][^"\']*noindex', html, re.I))
    noindex_header = "noindex" in headers.get("x-robots-tag", "").lower()
    hreflang = bool(re.search(r'hreflang', html, re.I))

    imgs        = _images(html)
    missing_alt = _missing_alt(imgs)
    img_alt_ok  = len(missing_alt) == 0

    schema_types = _schema_types(html)
    has_schema   = bool(schema_types)

    og_map  = {k: _og(html, k) for k in ["title","description","image","type","url","site_name"]}
    og_tags = [{"t": f"og:{k}", "v": v} for k, v in og_map.items() if v]
    tw_map  = {k: _twitter(html, k) for k in ["card","title","description","image","site"]}
    tw_tags = [{"t": f"twitter:{k}", "v": v} for k, v in tw_map.items() if v]
    twitter_card = bool(tw_map.get("card"))

    wc    = _wc(html)
    wc_ok = wc >= 500

    fpxm  = re.search(r"fbq\('init',\s*['\"](\d+)['\"]", html)
    fb_px = fpxm.group(1) if fpxm else ""

    has_ga  = bool(re.search(r'google-analytics\.com|gtag\(|G-[A-Z0-9]{6,}', html, re.I))
    has_gtm = bool(re.search(r'googletagmanager\.com', html, re.I))

    tech_list   = _tech(html, headers)
    viewport    = bool(re.search(r'viewport', html, re.I))
    iframes     = bool(re.search(r'<iframe[\s>]', html, re.I))
    favicon     = bool(re.search(r'<link[^>]*rel=["\']?(?:shortcut )?icon', html, re.I))
    has_amp     = bool(re.search(r'<html[^>]*\bamp\b', html, re.I))
    # Scan only visible text (not JS/schema/meta) for exposed emails
    _visible_text = _strip_clean(html)
    email_exp   = bool(re.search(r'[\w.+-]+@[\w-]+\.[a-z]{2,}', _visible_text, re.I))
    # Inline styles: only flag on body content tags, not html/head/meta injected attrs
    _body_m = re.search(r'<body[^>]*>(.*)', html, re.I | re.S)
    _body_html = _body_m.group(1) if _body_m else html
    inline_sty  = bool(re.search(r'<(?!(?:html|head|meta|link|script|style|noscript)\b)[^>]+\bstyle\s*=', _body_html, re.I))
    dep_html    = bool(re.search(r'<(font|center|marquee|blink)\b', html, re.I))
    # HTTP/2 detection: check Alt-Svc (signals h2/h3 support) and Cloudflare CF-Ray header
    alt_svc = headers.get("alt-svc", "").lower()
    http2 = (
        "h2" in alt_svc or
        "h3" in alt_svc or
        "h2" in headers.get("via", "").lower() or
        bool(headers.get("cf-ray", ""))  # Cloudflare always serves H2+
    )

    server_hdr = headers.get("server", "")
    charset_m  = re.search(r'charset=([^\s;]+)', headers.get("content-type", ""))
    charset    = charset_m.group(1).upper() if charset_m else "UTF-8"

    sec_headers = {k: headers.get(k, "") for k in [
        "x-frame-options", "x-content-type-options",
        "strict-transport-security", "content-security-policy",
        "referrer-policy", "permissions-policy",
    ]}

    html_kb = len(html.encode("utf-8", errors="replace")) / 1024
    kws     = _top_kws(html, title_t, meta_t)

    # Internal links + crawl sub-pages
    all_links  = _all_links(html, base)
    int_links  = _internal(all_links, parsed.netloc)
    broken     = []
    sub_pages  = []

    static_ext = re.compile(r'\.(jpg|jpeg|png|gif|svg|webp|pdf|zip|mp4|css|js)$', re.I)
    # Exclude homepage and already-fetched URL from link checks
    _skip = {start_url.rstrip("/"), final_url.rstrip("/")}
    check_urls = [l for l in int_links
                  if not static_ext.search(l) and l.rstrip("/") not in _skip][:12]

    lock = threading.Lock()
    def _chk(url):
        st, _ = _head(url, timeout=5)
        with lock:
            if st == 0 or st >= 400:
                broken.append({"url": url, "status": st})

    threads = [threading.Thread(target=_chk, args=(u,)) for u in check_urls]
    for t in threads: t.daemon = True; t.start()

    # Fetch sub-pages while link checks run
    for link in int_links[1:MAX_PAGES]:
        try:
            sh, _, sst, _ = _fetch(link, timeout=6)
            if sst == 200 and sh:
                sub_pages.append({
                    "url":    link,
                    "title":  _tag_text(sh, "title"),
                    "wc":     _wc(sh),
                    "h1":     _headings(sh).get("h1", []),
                    "schema": _schema_types(sh),
                    "canon":  _canonical(sh),
                })
        except Exception:
            pass

    for t in threads: t.join(6)

    dup_titles = [p["title"] for p in sub_pages if p["title"] == title_t]
    thin_pages = [p for p in sub_pages if p.get("wc", 0) < 300]

    # Robots.txt
    robots_url  = f"{base}/robots.txt"
    r_html, _, r_st, _ = _fetch(robots_url, timeout=6)
    robots_ok   = r_st == 200 and bool(r_html.strip())
    disallowed, crawl_delay, sitemap_refs = (
        _parse_robots(r_html) if robots_ok else ([], None, []))

    # Check if the audited URL's path is actually blocked (not just any path)
    audited_path = parsed.path or "/"
    def _is_blocked(path, rules):
        """Return True only if the given path matches a Disallow rule."""
        for rule in rules:
            if not rule or rule == "/":
                # Disallow: / blocks everything — but check Allow: first (simplified)
                return True
            if path.startswith(rule):
                return True
        return False
    page_is_blocked = _is_blocked(audited_path, disallowed)

    # Sitemap
    sitemap_url, sitemap_urls = "", []
    # Normalize sitemap refs (some robots.txt use relative paths — non-standard but real)
    abs_sitemap_refs = [
        r if r.startswith("http") else urljoin(base, r)
        for r in sitemap_refs
    ]
    for su in [f"{base}/sitemap_index.xml", f"{base}/sitemap.xml"] + abs_sitemap_refs:
        sm, _, sm_st, _ = _fetch(su, timeout=6)
        if sm_st == 200 and "<" in sm:
            sitemap_url  = su
            sitemap_urls = _parse_sitemap(sm)
            break

    # llms.txt
    llms_url = f"{base}/llms.txt"
    _, _, llms_st, _ = _fetch(llms_url, timeout=5)
    has_llms = llms_st == 200

    # DNS
    txt_records = _dns_txt(domain)
    spf_ok, spf_rec   = _spf(txt_records)
    dmarc_ok, dmarc_rec = _dmarc(domain)

    # Social / local
    social    = _social(html)
    addr_m    = re.search(r'<address[^>]*>(.*?)</address>', html, re.I | re.S)
    address   = re.sub(r'<[^>]+>', '', addr_m.group(1)).strip() if addr_m else ""
    # Phone: strict pattern matching real phone formats, not arbitrary digit strings
    phone_m   = re.search(
        r'(\+?\d[\d\s\-().]{7,18}\d)',
        re.sub(r'[a-zA-Z]{3,}', ' ', _strip_clean(html))  # remove word-heavy lines
    )
    phone     = phone_m.group(1).strip() if phone_m else ""
    local_sch = any("LocalBusiness" in t or "Organization" in t for t in schema_types)

    return {
        "domain": domain, "base": base, "final_url": final_url,
        "status": status, "ttfb": ttfb,
        "https_ok": https_ok, "redirected": redirected,
        "title_t": title_t, "title_len": title_len, "title_ok": title_ok,
        "meta_t": meta_t, "meta_len": meta_len, "meta_ok": meta_ok,
        "h1s": h1s, "h1_count": h1_count, "h1_status": h1_status, "hfreq": hfreq,
        "headings": headings, "canon": canon, "lang": lang,
        "noindex": noindex, "noindex_header": noindex_header,
        "hreflang": hreflang, "wc": wc, "wc_ok": wc_ok,
        "img_total": len(imgs), "img_missing_alt": len(missing_alt), "img_alt_ok": img_alt_ok,
        "schema_types": schema_types, "has_schema": has_schema,
        "og_tags": og_tags, "tw_tags": tw_tags, "twitter_card": twitter_card,
        "kws": kws, "fb_px": fb_px, "has_ga": has_ga, "has_gtm": has_gtm,
        "has_amp": has_amp, "viewport": viewport, "iframes": iframes,
        "favicon": favicon, "email_exp": email_exp,
        "inline_sty": inline_sty, "dep_html": dep_html,
        "tech_list": tech_list, "server": server_hdr, "charset": charset,
        "http2": http2, "sec_headers": sec_headers,
        "html_kb": round(html_kb, 1),
        "int_link_count": len(int_links),
        "broken": broken, "sub_pages": sub_pages,
        "dup_titles": dup_titles, "thin_pages": thin_pages,
        "robots_ok": robots_ok, "robots_url": robots_url,
        "page_is_blocked": page_is_blocked,
        "disallowed": disallowed, "crawl_delay": crawl_delay,
        "sitemap_ok": bool(sitemap_url), "sitemap_url": sitemap_url,
        "sitemap_count": len(sitemap_urls),
        "has_llms": has_llms, "llms_url": llms_url,
        "spf_ok": spf_ok, "spf_rec": spf_rec,
        "dmarc_ok": dmarc_ok, "dmarc_rec": dmarc_rec,
        "social": social, "address": address, "phone": phone, "local_sch": local_sch,
    }


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — AI EXPLAINER
# ══════════════════════════════════════════════════════════════════════════════

def ai_explain(s):
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None

    # Compact signal summary for the prompt — drop raw lists to save tokens
    compact = {k: v for k, v in s.items() if k not in
               ("headings", "kws", "social", "sub_pages", "og_tags", "tw_tags")}
    compact["h1s_sample"] = s["h1s"][:3]
    compact["schema_types"] = s["schema_types"]
    compact["og_present"]   = bool(s["og_tags"])
    compact["tw_present"]   = s["twitter_card"]
    compact["broken_urls"]  = [b["url"] for b in s["broken"][:5]]
    compact["thin_urls"]    = [p["url"] for p in s["thin_pages"][:5]]
    compact["sec_headers"]  = s["sec_headers"]

    prompt = f"""You are a senior SEO consultant reviewing a REAL crawl report.
The data below was collected by a live crawler — do NOT invent or guess anything not in the signals.
Write expert explanations and specific, actionable recommendations.

CRAWL SIGNALS:
{json.dumps(compact, indent=2, default=str)[:5500]}

Return ONLY valid JSON (no markdown, no backticks):

{{
  "overall_summary": "2-3 sentence expert verdict based strictly on these signals.",
  "title_advice": "Explain the title tag issue and the exact fix needed.",
  "meta_advice": "Explain the meta description issue and the exact fix needed.",
  "h1_advice": "Explain H1 situation and what to do.",
  "content_advice": "Explain word count and thin/duplicate content findings.",
  "schema_advice": "Explain schema findings. Name the specific schema types to add.",
  "technical_advice": "Explain HTTPS, canonical, security headers, robots, sitemap findings.",
  "performance_advice": "Explain TTFB, page size, inline styles, deprecated HTML.",
  "link_advice": "Explain broken links, internal link depth, crawlability.",
  "email_security_advice": "Explain SPF/DMARC findings and exact DNS records to add.",
  "geo_advice": "Explain llms.txt, hreflang, structured data for AI/GEO visibility.",
  "social_advice": "Explain social profile linking and OG/Twitter card findings.",
  "recommendations": [
    {{"priority": 1, "title": "Concise title", "detail": "Specific 2-3 sentence fix with exact values/tags."}},
    {{"priority": 2, "title": "...", "detail": "..."}},
    {{"priority": 3, "title": "...", "detail": "..."}},
    {{"priority": 4, "title": "...", "detail": "..."}},
    {{"priority": 5, "title": "...", "detail": "..."}},
    {{"priority": 6, "title": "...", "detail": "..."}}
  ]
}}

Sort recommendations by SEO impact (critical issues first). Only include real issues from the signals."""

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=3000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw   = "".join(b.text for b in resp.content if b.type == "text")
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            return json.loads(match.group(0))
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — ASSEMBLE REPORT
# ══════════════════════════════════════════════════════════════════════════════


def _build_cwv_advice(cwv_psi, crux_data):
    """Build a human-readable CWV advice string from available data."""
    if not cwv_psi and not crux_data:
        return "Core Web Vitals require real-user data. No CrUX data available for this domain yet — it may be too new or have low traffic."

    c = crux_data or cwv_psi or {}
    source = c.get("source", "")
    origin_note = " (origin-level data — URL-level not available)" if "origin" in source else ""

    parts = []
    if c.get("lcp") and c["lcp"] != "N/A":
        rating = c.get("lcp_rating", "")
        col = "✓" if rating == "FAST" else ("⚠" if rating == "MODERATE" else "✕")
        parts.append(f"LCP: {c['lcp']} {col}")
    if c.get("inp") and c["inp"] != "N/A":
        rating = c.get("inp_rating", "")
        col = "✓" if rating == "FAST" else ("⚠" if rating == "MODERATE" else "✕")
        parts.append(f"INP: {c['inp']} {col}")
    if c.get("cls") and c["cls"] != "N/A":
        rating = c.get("cls_rating", "")
        col = "✓" if rating == "FAST" else ("⚠" if rating == "MODERATE" else "✕")
        parts.append(f"CLS: {c['cls']} {col}")
    if c.get("fcp") and c["fcp"] != "N/A":
        parts.append(f"FCP: {c['fcp']}")
    if c.get("ttfb") and c["ttfb"] != "N/A":
        parts.append(f"TTFB: {c['ttfb']}")

    verdict = "✓ Passed" if cwv_psi and cwv_psi.get("pass") else "✕ Failed"
    metrics_str = "  |  ".join(parts) if parts else "No metrics available"
    source_str = "Real-user field data from Chrome UX Report" + origin_note

    return f"{verdict} — {metrics_str}. {source_str}."

def build_report(s, ai, mob_psi=None, desk_psi=None, cwv_psi=None, crux_data=None):
    def _a(key, fb=""):
        return (ai or {}).get(key, fb) if ai else fb

    # Score each category from real crawler data
    op_checks = [
        s["title_ok"], s["meta_ok"], s["h1_count"] == 1,
        s["wc_ok"], bool(s["canon"]), bool(s["lang"]),
        not s["noindex"], s["https_ok"], s["robots_ok"], s["sitemap_ok"],
        s["has_schema"], bool(s["og_tags"]), s["img_alt_ok"], s["favicon"],
        s["viewport"], not s["dep_html"],
    ]
    op_sc = round(sum(op_checks) / len(op_checks) * 100)

    geo_checks = [
        s["has_llms"], s["has_schema"], s["hreflang"],
        bool(s["og_tags"]), s["twitter_card"],
        bool([p for p in s["sub_pages"] if p.get("schema")]),
    ]
    geo_sc = round(sum(geo_checks) / len(geo_checks) * 100)

    cwv_pass = (cwv_psi or {}).get("pass")
    us_checks = [
        s["https_ok"], not s["iframes"], s["favicon"], s["viewport"],
        not s["email_exp"],
        bool(s["sec_headers"].get("strict-transport-security")),
        bool(s["sec_headers"].get("x-frame-options")),
        bool(s["sec_headers"].get("x-content-type-options")),
        not s["inline_sty"],
        cwv_pass is True,  # CWV pass counts toward usability score
    ]
    us_sc = round(sum(us_checks) / len(us_checks) * 100)

    # Performance scoring: use PSI score when available
    mob_score  = (mob_psi or {}).get("score", 0)
    desk_score = (desk_psi or {}).get("score", 0)
    psi_score  = max(mob_score, desk_score)
    pf_checks = [
        s["html_kb"] < 150,
        s["ttfb"] < 1.0,
        s["http2"],
        not s["dep_html"],
        not s["inline_sty"],
        len(s["broken"]) == 0,
        s["spf_ok"],
        s["dmarc_ok"],
        # PSI performance bonus/penalty
        psi_score >= 90 if psi_score > 0 else True,
        psi_score >= 50 if psi_score > 0 else True,
    ]
    pf_sc = round(sum(pf_checks) / len(pf_checks) * 100)

    ov_sc = round(op_sc * 0.35 + geo_sc * 0.15 + us_sc * 0.25 + pf_sc * 0.25)

    summary = _a("overall_summary",
        f"{s['domain']} scored {ov_sc}/100. "
        f"{'HTTPS OK' if s['https_ok'] else 'HTTPS missing'}. "
        f"{len(s['broken'])} broken link(s). "
        f"{'Schema found' if s['has_schema'] else 'No schema'}. "
        "(Add ANTHROPIC_API_KEY for full AI explanations.)")

    recs = _a("recommendations", _fallback_recs(s))

    return {
        "domain": s["domain"],
        "mode":   "deep_crawl_ai" if ai else "deep_crawl",
        "overall": {"grade": _grade(ov_sc), "summary": summary},
        "cats": [
            {"k": "op",  "grade": _grade(op_sc),  "lbl": "On-Page SEO", "c": "#7F77DD"},
            {"k": "geo", "grade": _grade(geo_sc), "lbl": "GEO / AI",    "c": "#1e8449"},
            {"k": "us",  "grade": _grade(us_sc),  "lbl": "Usability",   "c": "#c0392b"},
            {"k": "pf",  "grade": _grade(pf_sc),  "lbl": "Performance", "c": "#2980b9"},
        ],
        "op": {
            "title":        {"t": s["title_t"], "len": s["title_len"], "ok": s["title_ok"]},
            "titleAdvice":  _a("title_advice", f"Title is {s['title_len']} chars. Aim for 50–60."),
            "meta":         {"t": s["meta_t"], "len": s["meta_len"], "ok": s["meta_ok"]},
            "metaAdvice":   _a("meta_advice", f"Meta is {s['meta_len']} chars. Aim for 120–160."),
            "serpUrl":      s["final_url"],
            "serpTitle":    s["title_t"][:57],
            "serpDesc":     s["meta_t"][:155] + ("..." if s["meta_len"] > 155 else ""),
            "h1":           [{"tag": "H1", "v": h[:120]} for h in s["h1s"][:3]]
                            or [{"tag": "H1", "v": "Not found"}],
            "h1Count":      s["h1_count"],
            "h1Status":     s["h1_status"],
            "h1Advice":     _a("h1_advice"),
            "hfreq":        s["hfreq"],
            "kws":          s["kws"],
            "wc":           s["wc"], "wcOk": s["wc_ok"],
            "contentAdvice": _a("content_advice"),
            "imgAlt":       s["img_alt_ok"],
            "imgAltDesc":   (f"All {s['img_total']} images have alt attributes."
                             if s["img_alt_ok"]
                             else f"{s['img_missing_alt']} of {s['img_total']} images missing alt."),
            "canon":        s["canon"] or "Not detected", "canonOk": bool(s["canon"]),
            "noindex":      s["noindex"], "noindexOk": not s["noindex"],
            "noindexHeader": s["noindex_header"],
            "httpsRedir":   s["https_ok"],
            "robots":       s["robots_url"] if s["robots_ok"] else "Not found",
            "robotsOk":     s["robots_ok"],
            "robotsBlocked": s["page_is_blocked"],
            "disallowedPaths": s["disallowed"][:10],
            "crawlDelay":   s["crawl_delay"],
            "sitemap":      s["sitemap_url"] or "Not found", "sitemapOk": s["sitemap_ok"],
            "sitemapCount": s["sitemap_count"],
            "analytics":    s["has_ga"] or s["has_gtm"],
            "analyticsTools": [t for t in [
                "Google Analytics" if s["has_ga"] else None,
                "Google Tag Manager" if s["has_gtm"] else None,
            ] if t],
            "schema":       s["has_schema"], "schemaTypes": s["schema_types"][:8],
            "schemaAdvice": _a("schema_advice"),
            "lang":         s["lang"], "langOk": bool(s["lang"]),
            "hreflang":     s["hreflang"],
            "hreflangDesc": ("Hreflang tags found." if s["hreflang"]
                             else "No hreflang tags detected."),
            "amp":          s["has_amp"],
            "ampDesc":      "AMP enabled." if s["has_amp"] else "AMP not enabled.",
            "flash":        False,
        },
        "geo": {
            "renderPct":  "N/A", "renderOk": True,
            "renderDesc": "JS render % needs headless browser.",
            "llmsTxt":    s["has_llms"],
            "llmsTxtUrl": s["llms_url"] if s["has_llms"] else "",
            "llmsDesc":   ("llms.txt found — good for AI crawler guidance."
                           if s["has_llms"]
                           else "No llms.txt. Add one for AI/LLM visibility."),
            "geoAdvice":  _a("geo_advice"),
            "traffic":    {"org": 0, "paid": 0, "ai": 0},
            "kws":        [],
            "positions":  [{"r": r, "n": 0} for r in [
                "Position 1","Position 2-3","Position 4-10",
                "Position 11-20","Position 21-30","Position 31-100"]],
        },
        "us": {
            "cwv": cwv_psi if cwv_psi else {"lcp": "N/A", "inp": "N/A", "cls": "N/A", "pass": None},
            "crux": crux_data if crux_data else {},
            "cwvAdvice": _build_cwv_advice(cwv_psi, crux_data),
            "mob": mob_psi if mob_psi else {"score": 0, "fcp": "N/A", "si": "N/A", "lcp": "N/A",
                            "tti": "N/A", "tbt": "N/A", "cls": "N/A", "opps": []},
            "desk": desk_psi if desk_psi else {"score": 0, "fcp": "N/A", "si": "N/A", "lcp": "N/A",
                            "tti": "N/A", "tbt": "N/A", "cls": "N/A", "opps": []},
            "viewport":    s["viewport"],
            "iframes":     s["iframes"],
            "iframesDesc": ("iFrames detected." if s["iframes"] else "No iFrames."),
            "fontSizes":   True, "tapTargets": True,
            "favicon":     s["favicon"],
            "emailPrivacy": not s["email_exp"],
            "emailAdvice": ("Email address exposed in HTML." if s["email_exp"] else ""),
            "flash":       False,
            "secHeaders":  s["sec_headers"],
            "secAdvice":   _a("technical_advice"),
            "ttfb":        s["ttfb"],
        },
        "pf": {
            "speed":   {"srv": f"{s['ttfb']}s", "cnt": "N/A", "scr": "N/A",
                        "ok": s["ttfb"] < 1.0},
            "size":    {"tot": f"{s['html_kb']:.0f}KB (HTML only)",
                        "html": f"{s['html_kb']:.0f}KB",
                        "css": "N/A", "js": "N/A", "img": "N/A", "other": "N/A",
                        "ok": s["html_kb"] < 500},
            "comp":    {"rate": "N/A", "html": "N/A", "css": "N/A",
                        "js": "N/A", "img": "N/A", "other": "N/A", "ok": True},
            "http2":   s["http2"], "imgOpt": True,
            "minify":  False, "minifyDesc": "Minification needs full resource loading.",
            "jsErrors": False, "jsErrDesc": "",
            "inlineStyles": s["inline_sty"],
            "inlineDesc": ("Inline styles found — move to external CSS." if s["inline_sty"] else ""),
            "depHtml":  s["dep_html"],
            "res":      {"tot": len(s["sub_pages"]) + 1, "html": len(s["sub_pages"]) + 1,
                         "js": 0, "css": 0, "img": 0, "other": 0},
            "perfAdvice": _a("performance_advice"),
        },
        "crawl": {
            "pages_crawled":  len(s["sub_pages"]) + 1,
            "broken_links":   s["broken"],
            "broken_count":   len(s["broken"]),
            "internal_links": s["int_link_count"],
            "thin_pages":     [p["url"] for p in s["thin_pages"]][:5],
            "dup_titles":     s["dup_titles"][:3],
            "sitemap_count":  s["sitemap_count"],
            "link_advice":    _a("link_advice"),
        },
        "social":      s["social"],
        "fbPixel":     s["fb_px"], "fbPixelOk": bool(s["fb_px"]),
        "ogTags":      s["og_tags"],
        "twitterCard": s["twitter_card"],
        "twitterTags": s["tw_tags"],
        "socialAdvice": _a("social_advice"),
        "local": {
            "hasAddress": bool(s["address"]),
            "phone":      s["phone"],
            "addr":       s["address"],
            "localSchema": s["local_sch"],
            "schemaType": "LocalBusiness" if s["local_sch"] else "",
            "gbp":        {"found": False, "name": "", "addr": "", "phone": "", "site": ""},
            "reviews":    {"rating": 0, "count": 0, "dist": [0,0,0,0,0]},
        },
        "tech": {
            "list":       [{"name": n, "ver": ""} for n in s["tech_list"]],
            "dmarc":      s["dmarc_ok"],
            "dmarcDesc":  (f"DMARC found: {s['dmarc_rec']}" if s["dmarc_ok"]
                           else "No DMARC record — domain can be spoofed."),
            "spf":        s["spf_ok"],
            "spfRecord":  s["spf_rec"],
            "emailSecAdvice": _a("email_security_advice"),
            "server":     s["server"],
            "serverIp":   "",
            "charset":    s["charset"],
            "http2":      s["http2"],
            "http3":      False,
            "secHeaders": s["sec_headers"],
        },
        "recommendations": recs,
    }


def _fallback_recs(s):
    recs, p = [], 1
    def add(title, detail):
        nonlocal p
        recs.append({"priority": p, "title": title, "detail": detail}); p += 1

    if not s["title_ok"]:
        add("Fix title tag", f"Title is {s['title_len']} chars. Aim for 50–60.")
    if not s["meta_ok"]:
        add("Improve meta description", f"Meta is {s['meta_len']} chars. Aim for 120–160.")
    if s["h1_status"] != "good":
        add("Fix H1 tags", f"{s['h1_count']} H1(s) found. Use exactly one per page.")
    if s["broken"]:
        urls = ", ".join(b["url"] for b in s["broken"][:3])
        add("Fix broken links", f"{len(s['broken'])} broken link(s): {urls}")
    if not s["has_schema"]:
        add("Add Schema.org markup", "No structured data. Add JSON-LD schema.")
    if not s["dmarc_ok"]:
        add("Add DMARC record", "No DMARC — domain can be spoofed in phishing emails.")
    if not s["spf_ok"]:
        add("Add SPF record", "No SPF record. Add to prevent email spoofing.")
    if not s["has_llms"]:
        add("Add llms.txt", f"Create {s['llms_url']} for AI crawler guidance.")
    if not s["robots_ok"]:
        add("Create robots.txt", "No robots.txt found at the root.")
    if not s["sitemap_ok"]:
        add("Create XML sitemap", "No sitemap found. Create and submit to Search Console.")
    return recs[:6] or [{"priority": 1, "title": "No critical issues found",
                          "detail": "Basic crawl passed. Add ANTHROPIC_API_KEY for AI analysis."}]


# ══════════════════════════════════════════════════════════════════════════════
# VERCEL HANDLER
# ══════════════════════════════════════════════════════════════════════════════

class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        job_id = ""
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            job_id = body.get("job_id", "")
            url    = body.get("url", "").strip()
            stored = store_get(job_id)

            signals  = collect_signals(url)       # Layer 1+2: deep crawl

            # CrUX (free, always) + PSI (if key set) — run in parallel
            mob_psi, desk_psi, cwv_psi = fetch_pagespeed(url)  # PageSpeed API (optional)

            # CrUX: extract from PSI response (free) or call CrUX API separately
            mob_raw = (mob_psi or {}).get("_raw")
            crux_data = fetch_crux(url, psi_data=mob_raw)

            # Merge: PSI CWV takes priority if available, else use CrUX
            if cwv_psi is None and crux_data:
                cwv_psi = {
                    "lcp":  crux_data["lcp"],
                    "inp":  crux_data["inp"],
                    "cls":  crux_data["cls"],
                    "fcp":  crux_data["fcp"],
                    "ttfb": crux_data["ttfb"],
                    "pass": crux_data["pass"],
                    "source": crux_data["source"],
                    "lcp_rating":  crux_data.get("lcp_rating", ""),
                    "inp_rating":  crux_data.get("inp_rating", ""),
                    "cls_rating":  crux_data.get("cls_rating", ""),
                }

            ai_prose = ai_explain(signals)         # Layer 3: AI explanations
            report   = build_report(signals, ai_prose, mob_psi, desk_psi, cwv_psi, crux_data)  # Layer 4

            store_set(job_id, {
                "status": "done",
                "data":   report,
                "name":   stored.get("name", ""),
                "email":  stored.get("email", ""),
                "mode":   report["mode"],
            })

        except Exception as e:
            if job_id:
                store_set(job_id, {"status": "error", "message": str(e)})

        out = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a): pass
