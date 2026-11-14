"""Record the footage for the product tours from the real app, cut to the narration and framed by a camera.

Chrome's own screencast (every repaint, 2x pixel density) is recorded while a script drives the app; every action
waits for the narration word that describes it (word timings in frontend/tour/voice.json). Each action also logs a
camera move, and the frames are then rendered through that camera: a smooth, eased zoom and pan onto what matters,
so the app's text shows at its real size even in a small player. A cursor is drawn in the page so clicks are visible.

    PORT=8765 python frontend/server.py               # the app, with the AI model (Build clips use it; replies cached)
    python frontend/tour/capture.py                   # every clip, light theme
    python frontend/tour/capture.py --dark            # every clip, dark theme (<step>-dark.mp4)
    python frontend/tour/capture.py canvas-fix        # some clips
    python frontend/tour/capture.py --media           # only rebuild media.json (captions and durations)

Writes frontend/static/tour/<step>[-dark].mp4 and .jpg (poster), and media.json. Recording uses its own user name
("tour"), so nobody's history changes. Needs: pip install playwright pillow; Chrome; ffmpeg on PATH.
"""
import asyncio
import base64
import bisect
import io
import json
import math
import pathlib
import re
import subprocess
import sys
import time
import urllib.request

from PIL import Image
from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
OUT = REPO / "frontend" / "static" / "tour"
BASE = "http://127.0.0.1:8765"
W, H, DPR, FPS = 1024, 640, 2, 30
OUT_W, OUT_H = 1280, 800
USER = "tour"
THEME = "dark" if "--dark" in sys.argv else "light"
SUFFIX = "-dark" if THEME == "dark" else ""
CFG = json.loads((OUT / "tours.json").read_text(encoding="utf-8"))
VOICE = json.loads((HERE / "voice.json").read_text(encoding="utf-8"))

# A cursor drawn in the page (screencasts don't include the system cursor), kept above dialogs (top layer); the
# canvas toolbar keeps its labels, as on a wider screen.
INIT = """
try { localStorage.setItem('cs-theme', '__THEME__'); localStorage.setItem('cv-layout', 'flow'); localStorage.setItem('cs-user', '__USER__');
      localStorage.setItem('cs-tour', JSON.stringify({ seen: { build: 1, canvas: 1, compare: 1 } })); } catch (e) {}
(() => {
  const make = () => {
    if (document.getElementById('__cur')) return;
    const box = document.createElement('div');
    box.id = '__cur';
    box.setAttribute('popover', 'manual');
    box.style.cssText = 'position:fixed;inset:0;width:100vw;height:100vh;margin:0;padding:0;border:0;background:transparent;overflow:visible;pointer-events:none;';
    box.innerHTML = '<svg id="__arrow" width="24" height="24" viewBox="0 0 24 24" style="position:absolute;left:0;top:0;transform:translate(-80px,-80px);filter:drop-shadow(0 1.5px 2px rgba(0,0,0,.35));transition:transform 60ms linear">'
      + '<path d="M5 3 L5 19.5 L9.3 15.6 L12.2 21.6 L15 20.4 L12.1 14.5 L18 14.5 Z" fill="#17161c" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(box);
    const top = () => { try { box.hidePopover(); } catch (e) {} try { box.showPopover(); } catch (e) {} };
    top();
    window.__curTop = top;
    const arrow = box.querySelector('#__arrow');
    addEventListener('mousemove', e => { arrow.style.transform = `translate(${e.clientX - 5}px, ${e.clientY - 3}px)`; }, true);
    addEventListener('mousedown', e => {
      const r = document.createElement('div');
      r.style.cssText = `position:absolute;left:${e.clientX - 18}px;top:${e.clientY - 18}px;width:36px;height:36px;border-radius:50%;`
        + 'border:2.5px solid rgba(201,58,99,.85);background:rgba(201,58,99,.14);transform:scale(.3);opacity:1;transition:transform .45s cubic-bezier(.2,.7,.1,1),opacity .5s ease';
      box.appendChild(r);
      requestAnimationFrame(() => { r.style.transform = 'scale(1.25)'; r.style.opacity = '0'; });
      setTimeout(() => r.remove(), 600);
    }, true);
    new MutationObserver(() => setTimeout(top, 0)).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['open'] });
  };
  const css = () => { const st = document.createElement('style');
    st.textContent = '.tb .tb-label{display:inline!important}.tb{padding:0 12px!important}'; document.head.appendChild(st); };
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', () => { make(); css(); }); else { make(); css(); }
})();
""".replace("__USER__", USER).replace("__THEME__", THEME)


def tokens(text):
    return re.findall(r"[a-z0-9]+", text.lower().replace("x-ray", "xray"))


def centre(box, dx=0, dy=0):
    return (box["x"] + box["width"] / 2 + dx, box["y"] + box["height"] / 2 + dy)


class Clip:
    """One recording, timed against its narration, with a camera."""

    def __init__(self, page, cdp, sid):
        self.page, self.cdp, self.sid = page, cdp, sid
        self.words = VOICE[sid]["words"]
        self.seconds = VOICE[sid]["seconds"]
        self.frames, self.t0, self.cursor, self.wi = [], None, (W * 0.72, H * 0.7), 0
        self.cams = []

    def _frame(self, params):
        self.frames.append((params["metadata"].get("timestamp") or time.time(), params["data"]))
        asyncio.ensure_future(self.cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]}))

    async def start(self, z=1.0, point=None):
        await self.page.mouse.move(*self.cursor)
        self.cdp.on("Page.screencastFrame", self._frame)
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": W * DPR, "maxHeight": H * DPR, "everyNthFrame": 1})
        self.t0 = time.time()
        cx, cy = point or (W / 2, H / 2)
        self.cams.append({"t": 0.0, "dur": 0.0, "z": z, "cx": cx, "cy": cy})
        return self

    def now(self):
        return time.time() - self.t0

    # ---- camera: logged now, applied when the frames are rendered
    def cam(self, z=1.0, point=None, dur=0.8, lead=0.1):
        cx, cy = point or (W / 2, H / 2)
        self.cams.append({"t": max(0.0, self.now() - lead), "dur": dur, "z": z, "cx": cx, "cy": cy})

    async def look(self, locator, z=1.5, dur=0.8, lead=0.1, dx=0, dy=0):
        box = await locator.bounding_box()
        if box:
            self.cam(z, centre(box, dx, dy), dur, lead)
        return box

    # ---- narration timing
    def when(self, word):
        want = tokens(word)[0]
        for i in range(self.wi, len(self.words)):
            got = tokens(self.words[i]["w"])
            if got and got[0].startswith(want):
                self.wi = i + 1
                return self.words[i]["start"]
        raise ValueError(f"{self.sid}: '{word}' not found in the narration after word {self.wi}")

    async def at(self, word, offset=0.0):
        t = self.when(word) + offset
        if t > self.now():
            await asyncio.sleep(t - self.now())

    async def stop(self, tail=1.1, poster=0.62):
        end_at = max(self.seconds + tail, self.now() + 0.5)
        await asyncio.sleep(max(0, end_at - self.now()))
        end = time.time()
        await self.cdp.send("Page.stopScreencast")
        self.cdp.remove_listener("Page.screencastFrame", self._frame)
        await asyncio.sleep(0.2)
        render(self.sid, sorted(self.frames), self.t0, end, self.cams, poster)

    # ---- cursor (eased, so the drawn cursor glides)
    async def move(self, x, y, dur=0.7):
        x0, y0 = self.cursor
        n = max(8, int(dur * 45))
        for i in range(1, n + 1):
            k = i / n
            k = k * k * (3 - 2 * k)
            await self.page.mouse.move(x0 + (x - x0) * k, y0 + (y - y0) * k)
            await asyncio.sleep(dur / n)
        self.cursor = (x, y)

    async def to(self, locator, dur=0.7, dx=0.5, dy=0.5):
        box = await locator.bounding_box()
        await self.move(box["x"] + box["width"] * dx, box["y"] + box["height"] * dy, dur)
        return box

    async def click(self, locator=None, dur=0.7, dx=0.5, dy=0.5):
        if locator is not None:
            await self.to(locator, dur, dx, dy)
        await asyncio.sleep(0.08)
        await self.page.mouse.down()
        await asyncio.sleep(0.07)
        await self.page.mouse.up()

    async def drag(self, src, x, y, dur=1.3):
        box = await self.to(src, 0.6)
        await self.page.mouse.down()
        sx, sy = centre(box)
        await self.move(sx + 30, sy - 8, 0.2)
        await self.move(x, y, dur)
        await asyncio.sleep(0.25)
        await self.page.mouse.up()

    async def type(self, text, per=0.042):
        for ch in text:
            await self.page.keyboard.type(ch)
            await asyncio.sleep(per * (0.7 + 0.6 * ((ord(ch) * 7) % 10) / 10))

    async def scroll_to(self, y, dur=1.0):
        await self.page.evaluate("""([to, ms]) => new Promise(done => {
            const from = window.scrollY, t0 = performance.now();
            const ease = k => k < .5 ? 4*k*k*k : 1 - Math.pow(-2*k + 2, 3) / 2;
            const tick = now => { const k = Math.min(1, (now - t0) / ms);
                window.scrollTo(0, from + (to - from) * ease(k)); k < 1 ? requestAnimationFrame(tick) : done(); };
            requestAnimationFrame(tick); })""", [y, int(dur * 1000)])

    async def scroll_into(self, locator, top=90, dur=1.0):
        box = await locator.bounding_box()
        y = await self.page.evaluate("window.scrollY")
        await self.scroll_to(max(0, y + box["y"] - top), dur)

    async def pan(self, dx, dur=1.2, vertical=False):
        """Pan the canvas with small wheel steps (shift: vertically), a smooth move the screencast follows."""
        n = max(6, int(dur * 40))
        if vertical:
            await self.page.keyboard.down("Shift")
        for _ in range(n):
            await self.page.mouse.wheel(0, dx / n)
            await asyncio.sleep(dur / n)
        if vertical:
            await self.page.keyboard.up("Shift")


def ease(k):
    return 4 * k * k * k if k < 0.5 else 1 - (-2 * k + 2) ** 3 / 2


def render(sid, frames, t0, end, cams, poster_at):
    """Frames → video through the camera: each output frame crops the screencast (sub-pixel, eased) and scales it."""
    if not frames:
        raise RuntimeError(f"{sid}: no frames captured")
    times = [ts - t0 for ts, _ in frames]
    total = end - t0
    mp4, jpg = OUT / f"{sid}{SUFFIX}.mp4", OUT / f"{sid}{SUFFIX}.jpg"
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{OUT_W}x{OUT_H}",
                             "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-profile:v", "high",
                             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(mp4)], stdin=subprocess.PIPE)
    ks = sorted(cams, key=lambda k: k["t"])
    cur, last, ki = dict(ks[0]), None, 0
    cached_i, img = -1, None
    poster_i = int(total * poster_at * FPS)
    for i in range(int(total * FPS)):
        t = i / FPS
        while ki < len(ks) and ks[ki]["t"] <= t:
            last = dict(ks[ki], frm={k: cur[k] for k in ("z", "cx", "cy")})
            ki += 1
        if last:
            p = 1.0 if last["dur"] <= 0 else min(1.0, (t - last["t"]) / last["dur"])
            e = ease(p)
            cur = {k: last["frm"][k] + (last[k] - last["frm"][k]) * e for k in ("z", "cx", "cy")}
        z = max(1.0, cur["z"])
        w, h = W / z, H / z
        x = min(max(cur["cx"] - w / 2, 0), W - w)
        y = min(max(cur["cy"] - h / 2, 0), H - h)
        j = max(0, bisect.bisect_right(times, t) - 1)
        if j != cached_i:
            img = Image.open(io.BytesIO(base64.b64decode(frames[j][1]))).convert("RGB")
            cached_i = j
        sx, sy = img.width / W, img.height / H                 # the screencast's real pixel size
        out = img.resize((OUT_W, OUT_H), Image.LANCZOS,
                         box=(x * sx, y * sy, min(img.width, (x + w) * sx), min(img.height, (y + h) * sy)))
        proc.stdin.write(out.tobytes())
        if i == poster_i:
            out.save(jpg, quality=82, optimize=True)
    proc.stdin.close()
    proc.wait()
    print(f"  {sid}{SUFFIX}: {len(frames)} frames, {total:.1f}s, {mp4.stat().st_size / 1e6:.2f} MB")


# ---------------------------------------------------------------- helpers for the app
async def settle_canvas(page, ms=500):
    await page.wait_for_function("() => { const p = document.querySelector('.cv-stat-pill'); return p && !p.classList.contains('pending'); }", timeout=60000)
    await page.wait_for_timeout(ms)


async def open_canvas(ctx, url="/canvas?template=0", zoom_out=0):
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    await page.goto(BASE + url)
    await page.wait_for_selector(".cv-node.end .cvn-n", timeout=60000)
    await settle_canvas(page, 900)
    await page.locator("#cv-zoom-n").click()                 # 100%, then step out if the clip needs more of the pipeline
    for _ in range(zoom_out):
        await page.wait_for_timeout(250)
        await page.locator("#cv-zoom-out").click()
    await page.wait_for_timeout(500)
    await page.mouse.move(W / 2, H / 2)
    return page, cdp


async def bring(page, locator, x, y=None):
    """Pan (before recording) so the element's centre sits at x (and y)."""
    box = await locator.bounding_box()
    await page.mouse.move(W / 2, H * 0.62)
    await page.mouse.wheel(0, box["x"] + box["width"] / 2 - x)
    if y is not None:
        await page.keyboard.down("Shift")
        await page.mouse.wheel(0, box["y"] + box["height"] / 2 - y)
        await page.keyboard.up("Shift")
    await page.wait_for_timeout(450)


def card(page, text):
    return page.locator(".cv-node", has_text=text).first


# ---------------------------------------------------------------- canvas clips
async def canvas_read(ctx):
    page, cdp = await open_canvas(ctx, zoom_out=1)
    await bring(page, card(page, "All samples"), 120)
    c = await Clip(page, cdp, "canvas-read").start(1.0)
    await c.at("pipeline", -0.2)
    await c.pan(150, 1.4)
    await c.at("card", -0.4)
    await c.look(card(page, "Primary tumours"), 1.8, 0.9)
    await c.to(card(page, "Primary tumours").locator(".cvn-title"), 0.8)
    await c.at("count", -0.2)
    await c.to(card(page, "Primary tumours").locator(".cvn-n"), 0.6)
    await c.at("wire", -0.4)
    await c.look(page.locator(".cv-wl").nth(2), 1.7, 0.9, dy=10)
    await c.to(page.locator(".cv-wl").nth(2), 0.7)
    await c.at("drop", -0.4)
    await c.look(page.locator(".cv-drop").nth(2), 1.6, 0.8, dy=-50)
    await c.to(page.locator(".cv-drop").nth(2), 0.7)
    await c.at("away", 0.1)
    c.cam(1.15, (W / 2, H * 0.48), 1.0)
    await c.stop(tail=1.4)
    await page.close()


async def canvas_fix(ctx):
    page, cdp = await open_canvas(ctx)
    join = page.locator(".cv-node.infl").first
    await bring(page, join, 380)
    jb = await join.bounding_box()
    c = await Clip(page, cdp, "canvas-fix").start(1.35, centre(jb, 0, 40))
    await c.at("pink", -0.4)
    await c.look(join, 1.7, 0.8, dy=36)
    await c.to(join.locator(".cvn-title"), 0.8)
    await c.at("tap", -0.2)
    await c.click(join.locator(".cvn-title"), 0.3)
    await page.wait_for_selector("#cv-panel .cv-ins-head")
    c.cam(1.2, (W * 0.62, H * 0.42), 0.8)
    await page.wait_for_timeout(350)
    await c.at("xray", -0.6)
    await c.click(page.locator("#cv-panel button", has_text="See why").first, 0.6)
    await page.wait_for_selector("#cv-xray .cv-x-tree", timeout=20000)
    await page.evaluate("window.__curTop && window.__curTop()")
    await c.look(page.locator("#cv-xray .cv-x-tree").first, 1.25, 0.7, dy=-20)
    await c.at("watch", -0.3)
    hits = page.locator("#cv-xray .cv-x-slide.hit")
    if await hits.count():
        await c.look(hits.first, 1.7, 0.7, dx=60)
    for i in range(min(2, await hits.count())):
        await c.to(hits.nth(i), 0.55)
        await asyncio.sleep(0.35)
    await c.at("then", -0.2)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(250)
    pill = page.locator(".cv-pill button", has_text="Fix").first
    await c.look(pill, 1.6, 0.7, dy=-50)
    await c.at("fix", -0.5)
    await c.click(pill, 0.5)
    await settle_canvas(page, 200)
    await c.at("once", -0.6)
    await c.to(page.locator(".cv-node.selected .cvn-title"), 0.6)
    c.cam(1.3, (W * 0.4, H * 0.5), 1.0)
    await c.stop(tail=1.3)
    await page.close()


async def canvas_add(ctx):
    page, cdp = await open_canvas(ctx)
    await bring(page, card(page, "Primary tumours"), 470)
    c = await Clip(page, cdp, "canvas-add").start(1.0)
    await c.at("add", -0.4)
    c.cam(1.3, (W * 0.36, H * 0.52), 0.9)
    await c.click(page.locator("#cv-toolbar .tb[title='Add']"), 0.6)
    await page.wait_for_selector("#cv-library .cv-block")
    await c.at("drag", -0.4)
    target = await page.locator(".cv-wl").nth(1).bounding_box()
    await c.drag(page.locator("#cv-library .cv-block", has_text="ER status").first, target["x"] + target["width"] / 2, target["y"] - 26, 1.3)
    await settle_canvas(page, 300)
    await page.keyboard.press("Escape")                      # the library steps aside so the new cards show
    await page.wait_for_timeout(350)
    auto = page.locator(".cv-node:has(.cvn-line.auto)").first
    box = await auto.bounding_box()
    if box["x"] < 40:
        await c.pan(box["x"] - 220, 0.7)
    await c.at("join", -0.5)
    await c.look(auto, 1.6, 0.8, dy=-24)
    await c.to(auto.locator(".cvn-title"), 0.7)
    await c.stop(tail=1.3)
    await page.close()


async def canvas_lanes(ctx):
    page, cdp = await open_canvas(ctx, zoom_out=2)
    await bring(page, page.locator(".cv-node.infl").first, 640)
    c = await Clip(page, cdp, "canvas-lanes").start(1.0)
    await c.at("switch", -0.2)
    await c.click(page.locator("#cv-toolbar .tb[title='Grain lanes']"), 0.7)
    await page.wait_for_timeout(700)
    join = await page.locator(".cv-node.infl").first.bounding_box()   # the lanes move the cards: follow the join
    await page.mouse.move(W * 0.55, H * 0.5)
    c.cursor = (W * 0.55, H * 0.5)
    await c.pan(join["x"] + join["width"] / 2 - W * 0.6, 0.9)
    await c.pan(90, 0.5, vertical=True)                      # lift the lanes clear of the toolbar
    labels = page.locator(".cv-lanes > *")
    for word, i in (("patient", 0), ("sample", 1), ("several", 2)):
        await c.at(word, -0.25)
        if await labels.count() > i:
            b = await labels.nth(i).bounding_box()
            c.cam(1.5, (b["x"] + 300, b["y"] + 40), 0.6)
            await c.to(labels.nth(i), 0.5, dx=0.12)
    await c.at("double", -0.4)
    await c.look(page.locator(".cv-node.infl").first, 1.45, 0.8, dy=10)
    await c.to(page.locator(".cv-node.infl").first, 0.7)
    await c.at("fix", -0.5)
    await c.click(page.locator(".cv-pill button", has_text="Fix").first, 0.5)
    await settle_canvas(page, 200)
    c.cam(1.2, (W * 0.58, H * 0.42), 1.0)
    await c.stop(tail=1.6)
    await page.close()


async def canvas_code(ctx):
    page, cdp = await open_canvas(ctx)
    await bring(page, card(page, "Has RNA"), 300)
    c = await Clip(page, cdp, "canvas-code").start(1.0)
    await c.at("code", -0.4)
    await c.click(page.locator("#cv-toolbar .tb[title='Code']"), 0.7)
    c.cam(1.4, (W * 0.8, H * 0.46), 0.9)
    await page.wait_for_selector(".cv-verify.ok", timeout=30000)
    for word, tab in (("python", "Python"), ("raw", "JSON"), ("sql", "SQL")):
        await c.at(word, -0.35)
        await c.click(page.locator("#cv-panel .cv-code-bar button", has_text=tab).first, 0.45)
    await page.wait_for_selector(".cv-verify.ok", timeout=30000)
    await c.at("run", -0.3)
    await c.look(page.locator(".cv-verify.ok"), 1.8, 0.8, dx=40, dy=40)
    await c.to(page.locator(".cv-verify.ok"), 0.6)
    await c.at("step", -0.7)
    c.cam(1.15, (W * 0.6, H * 0.5), 0.9)
    await c.click(card(page, "Has RNA").locator(".cvn-title"), 0.7)
    await c.stop(tail=1.4)
    await page.close()


# ---------------------------------------------------------------- build clips (the AI model drafts; replies are cached)
DEMO = "primary tumors with RNA and protein data and a diagnostic slide"
ASK = "tumours from older patients with RNA data"


async def build_page(ctx):
    page = await ctx.new_page()
    cdp = await ctx.new_cdp_session(page)
    await page.goto(BASE + "/")
    await page.wait_for_selector("#q")
    await page.wait_for_timeout(1500)
    return page, cdp


async def submit(page, text):
    await page.fill("#q", text)
    await page.keyboard.press("Enter")


async def build_ask(ctx):
    page, cdp = await build_page(ctx)
    await submit(page, ASK)                                     # warm the model cache, so the take is quick
    await page.wait_for_selector(".question", timeout=180000)
    await page.close()
    page, cdp = await build_page(ctx)
    qb = await page.locator("#search-form").bounding_box()
    c = await Clip(page, cdp, "build-ask").start(1.0)
    await asyncio.sleep(0.2)
    c.cam(1.45, centre(qb), 0.9)
    await c.click(page.locator("#q"), 0.6)
    await c.type(ASK)
    await page.keyboard.press("Enter")
    await page.wait_for_selector(".question", timeout=120000)
    await page.wait_for_timeout(250)
    await c.at("asks", -0.3)
    q = page.locator(".question").last
    c.cam(1.25, (W / 2, H * 0.5), 0.9)
    await c.scroll_into(q, top=70, dur=0.9)
    await c.at("every", -0.3)
    opts = q.locator(".option")
    await c.look(opts.nth(1), 1.45, 0.8, dy=10)
    for i in range(min(3, await opts.count())):
        await c.to(opts.nth(i), 0.5)
        await asyncio.sleep(0.4)
    await c.at("choose", -0.3)
    await c.look(opts.nth(0), 1.55, 0.6, dx=120)
    await c.click(opts.nth(0), 0.5)
    await c.stop(tail=1.3)
    await page.close()


async def demo_result(ctx):
    page, cdp = await build_page(ctx)
    await submit(page, DEMO)
    run = page.locator("button:has-text('Run protocol')").last
    await run.wait_for(timeout=180000)
    await run.click()
    await page.wait_for_selector(".result", timeout=120000)
    await page.wait_for_timeout(1500)
    return page, cdp


async def scroll_now(page, locator, top):
    y = await page.evaluate("window.scrollY")
    box = await locator.bounding_box()
    await page.evaluate(f"window.scrollTo(0, {y + box['y'] - top})")
    await page.wait_for_timeout(500)


async def build_fix_verdict_take(ctx, want):
    page, cdp = await demo_result(ctx)
    res = page.locator(".result").last
    if "build-fix" in want:
        await scroll_now(page, res, 70)
        c = await Clip(page, cdp, "build-fix").start(1.3, (W * 0.42, H * 0.45))
        flow_pink = res.locator("text=/rows counted twice/i").first
        await asyncio.sleep(0.3)
        await c.scroll_into(flow_pink, top=int(H * 0.52), dur=2.6)
        await c.at("pink", -0.5)
        await c.look(flow_pink, 1.6, 0.8, dx=40, dy=-28)
        await c.to(flow_pink, 0.6)
        await c.at("one", -0.5)
        fix = res.locator("button:has-text('Fix it')").first
        c.cam(1.3, (W / 2, H * 0.45), 0.8)
        await c.scroll_into(fix, top=int(H * 0.45), dur=0.9)
        await c.look(fix, 1.6, 0.6, dx=-200)
        await c.click(fix, 0.5)
        for _ in range(80):
            await asyncio.sleep(0.15)
            if not await page.locator(".result").last.locator("button:has-text('Fix it')").count():
                break
        res = page.locator(".result").last
        await c.at("once", -0.6)
        c.cam(1.3, (W * 0.42, H * 0.45), 0.9)
        box = await res.bounding_box()
        y = await page.evaluate("window.scrollY")
        await c.scroll_to(y + box["y"] - 70, 1.0)
        await c.stop(tail=1.3)
    else:
        await res.locator("button:has-text('Fix it')").first.click()
        await page.wait_for_timeout(2500)
    res = page.locator(".result").last
    if "build-verdict" in want:
        await scroll_now(page, res, 70)
        c = await Clip(page, cdp, "build-verdict").start(1.3, (W * 0.45, H * 0.4))
        await c.at("verdict", -0.5)
        await c.look(res.locator(".rh-side .verdict").first, 1.75, 0.8, dx=120, dy=30)
        await c.to(res.locator(".rh-side .verdict").first, 0.7)
        await c.at("ready", -0.3)
        pills = res.locator(".checks-strip .ck")
        await c.look(pills.nth(1), 1.6, 0.6)
        for i in range(min(3, await pills.count())):
            await c.to(pills.nth(i), 0.45)
            await asyncio.sleep(0.25)
        await c.at("open", -0.7)
        allc = res.locator("details.more > summary", has_text="All checks").first
        c.cam(1.3, (W / 2, H * 0.5), 0.8)
        await c.scroll_into(allc, top=int(H * 0.3), dur=0.9)
        await c.click(allc, 0.4)
        await c.at("person", -0.5)
        rev = res.locator("details.more > summary", has_text="Before anyone").first
        if await rev.count():
            await c.look(rev, 1.4, 0.6, dx=160, dy=60)
            await c.click(rev, 0.5)
        await c.stop(tail=1.3)
    if "build-take" in want:
        exp = res.locator(".exports").first
        await scroll_now(page, exp, int(H * 0.35))
        c = await Clip(page, cdp, "build-take").start(1.35, (W * 0.42, H * 0.45))
        await c.at("download", -0.4)
        await c.look(exp.locator("a.btn").first, 1.7, 0.7, dx=120, dy=-20)
        await c.to(exp.locator("a.btn").first, 0.7)
        await c.at("python", -0.6)
        other = exp.locator("details.files > summary").first
        await c.click(other, 0.5)
        await page.wait_for_timeout(300)
        repro = page.locator(".files-menu a", has_text="reproduce_cohort.py").first
        if await repro.count():
            await c.look(repro, 1.5, 0.6, dy=60)
            await c.to(repro, 0.5)
        await c.at("canvas", -1.3)
        await c.click(other, 0.4)
        oc = res.locator("a.btn", has_text="Open in canvas").first
        c.cam(1.3, (W / 2, H * 0.55), 0.6)
        await c.scroll_into(oc, top=int(H * 0.55), dur=0.6)
        await c.click(oc, 0.5)
        c.cam(1.0, None, 0.6, lead=0)
        await page.wait_for_selector(".cv-node.end .cvn-n", timeout=30000)
        await settle_canvas(page, 500)
        first = await page.locator(".cv-node:not(.start):not(.end)").nth(1).bounding_box()
        c.cam(1.45, centre(first, 120, 10), 1.0, lead=0)     # onto the cards, once the canvas has settled
        await c.at("visually", -0.6)
        await c.move(first["x"] + first["width"] * 0.6, first["y"] + 40, 0.6)
        await c.stop(tail=2.0)
    await page.close()


# ---------------------------------------------------------------- compare clips
def saved_cohorts():
    """A clean list for the picker: clear the recording user's cohorts, then save three (two of them overlap)."""
    sys.path.insert(0, str(REPO))
    from cohort_studio.data import DataStore
    from cohort_studio.parse import parse_request
    store = DataStore(REPO / "data" / "tcga-brca")
    lst = urllib.request.Request(f"{BASE}/api/searches", headers={"x-user": USER})
    for s in json.loads(urllib.request.urlopen(lst, timeout=60).read()).get("searches", []):
        urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/searches/{s['id']}", method="DELETE", headers={"x-user": USER}), timeout=60)
    ids = []
    for text in (DEMO, "stage II primary tumors with RNA data", "ER-positive primary tumors with RNA data"):
        req = urllib.request.Request(f"{BASE}/api/run", headers={"content-type": "application/json", "x-user": USER},
                                     data=json.dumps({"spec": parse_request(text, store), "query": text, "mode": "rules"}).encode())
        ids.append(json.loads(urllib.request.urlopen(req, timeout=120).read())["search_id"])
    return ids[2], ids[1]                                     # ER-positive (A) and stage II (B): 442 shared samples


async def compare_clips(ctx, want):
    a_id, b_id = saved_cohorts()
    if "compare-pick" in want:
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        await page.goto(BASE + "/compare")
        await page.wait_for_selector(".slot.a.empty-slot")
        await page.wait_for_timeout(1500)
        c = await Clip(page, cdp, "compare-pick").start(1.0)
        await c.at("pick", -0.3)
        await c.click(page.locator(".slot.a"), 0.6)
        await page.wait_for_selector("#picker[open] .pk-item")
        await page.evaluate("window.__curTop && window.__curTop()")
        await c.look(page.locator("#picker .pk-item").first, 1.3, 0.7, dy=60)
        items = page.locator("#pk-list .pk-item")
        await c.at("saved", -0.2)
        await c.to(items.nth(1), 0.5)
        await c.at("favor", -0.2)
        await c.to(page.locator("#pk-tab-fav"), 0.5)
        await c.at("versions", -0.1)
        await c.click(page.locator("#pk-list .pk-item", has_text="ER-positive").first, 0.6)
        await page.wait_for_timeout(300)
        await c.click(page.locator(".slot.b"), 0.6)
        await page.wait_for_selector("#picker[open] .pk-item")
        await page.evaluate("window.__curTop && window.__curTop()")
        await c.at("rerun", -0.6)
        await c.click(page.locator("#pk-list .pk-item", has_text="stage II").first, 0.6)
        await page.wait_for_selector("#c-overlap .venn", timeout=60000)
        c.cam(1.0, None, 0.7)
        await c.at("same", -0.2)
        await c.scroll_into(page.locator("#c-overlap"), top=150, dur=1.0)
        await c.at("fair", -0.4)
        await c.look(page.locator("#c-overlap .venn").first, 1.4, 0.8, dx=120)
        await c.to(page.locator("#c-overlap .venn").first, 0.7)
        await c.stop(tail=1.4)
        await page.close()
    if "compare-overlap" in want or "compare-outcomes" in want:
        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        await page.goto(f"{BASE}/compare?a=s:{a_id}&b=s:{b_id}")
        await page.wait_for_selector("#c-overlap .venn", timeout=60000)
        await page.wait_for_timeout(2200)
        await scroll_now(page, page.locator("#c-overlap"), 70)
        if "compare-overlap" in want:
            vb = await page.locator("#c-overlap .venn").first.bounding_box()
            c = await Clip(page, cdp, "compare-overlap").start(1.2, centre(vb, 150))
            await c.at("share", -0.3)
            await c.look(page.locator("#c-overlap .venn").first, 1.5, 0.8, dx=40)
            await c.to(page.locator("#c-overlap .venn").first, 0.7)
            await c.at("drawn", -0.1)
            tiles = page.locator("#c-overlap .tiles, #c-overlap .tile").first
            await c.look(tiles, 1.5, 0.7)
            await c.to(tiles, 0.6)
            await c.at("plain", -0.5)
            c.cam(1.25, (W / 2, H * 0.45), 0.8)
            await c.scroll_into(page.locator("#c-summary"), top=80, dur=1.0)
            await c.to(page.locator("#c-summary li").first, 0.6)
            await c.stop(tail=1.3)
        if "compare-outcomes" in want:
            await scroll_now(page, page.locator("#c-composition"), 70)
            c = await Clip(page, cdp, "compare-outcomes").start(1.25, (W * 0.48, H * 0.5))
            await c.at("who", -0.2)
            await c.to(page.locator("#c-composition .comp-grid > *").first, 0.7)
            await c.at("which", -0.3)
            await c.to(page.locator("#c-composition .comp-grid > *").nth(1), 0.6)
            await c.at("survival", -0.6)
            await c.scroll_into(page.locator("#c-outcomes"), top=70, dur=1.0)
            await c.look(page.locator("#c-outcomes svg").first, 1.35, 0.7, dx=60)
            await c.to(page.locator("#c-outcomes svg").first, 0.6)
            await c.stop(tail=1.3)
        await page.close()


# ---------------------------------------------------------------- captions and durations for the player
def chunks(text):
    parts = [p.strip() for p in re.split(r"(?<=[.!?:;])\s+|(?<=,)\s+", text) if p.strip()]
    out = []
    for p in parts:
        if out and (len(tokens(p)) < 3 or len(tokens(out[-1])) < 3) and len(tokens(out[-1] + " " + p)) <= 11:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def write_media():
    media = {}
    for tour in CFG["tours"].values():
        for st in tour["steps"]:
            v = VOICE.get(st["id"])
            if not v:
                continue
            words = v["words"]
            n_ref = len(tokens(st["narration"])) or 1
            cues, done = [], 0
            for part in chunks(st["narration"]):
                k = len(tokens(part))
                a = min(len(words) - 1, math.floor(done * len(words) / n_ref))
                b = min(len(words) - 1, max(a, math.ceil((done + k) * len(words) / n_ref) - 1))
                cues.append({"s": words[a]["start"], "e": words[b].get("end", words[b]["start"] + 0.3), "t": part})
                done += k
            durs = {}
            for suffix in ("", "-dark"):
                mp4 = OUT / f"{st['id']}{suffix}.mp4"
                if mp4.exists():
                    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
                                           capture_output=True, text=True)
                    durs[suffix or "light"] = round(float(probe.stdout.strip() or 0), 2)
            media[st["id"]] = {"voice": v["seconds"], "video": min(durs.values()) if durs else None,
                               "dark": "-dark" in durs or "dark" in durs, "captions": cues}
    (OUT / "media.json").write_text(json.dumps(media, indent=1), encoding="utf-8")
    print("media.json:", len(media), "steps")


async def main(only):
    want = set(only) or {s["id"] for t in CFG["tours"].values() for s in t["steps"]}
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", args=["--hide-scrollbars", "--force-color-profile=srgb"])
        ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=DPR, color_scheme=THEME, accept_downloads=False)
        await ctx.add_init_script(INIT)
        jobs = [("canvas-read", canvas_read), ("canvas-fix", canvas_fix), ("canvas-add", canvas_add),
                ("canvas-lanes", canvas_lanes), ("canvas-code", canvas_code), ("build-ask", build_ask)]
        for sid, fn in jobs:
            if sid in want:
                try:
                    await fn(ctx)
                except Exception as e:  # keep going; report what failed
                    print(f"  {sid}: FAILED {type(e).__name__}: {str(e).splitlines()[0][:200]}")
        build = want & {"build-fix", "build-verdict", "build-take"}
        if build:
            try:
                await build_fix_verdict_take(ctx, build)
            except Exception as e:
                print(f"  build: FAILED {type(e).__name__}: {str(e).splitlines()[0][:200]}")
        comp = want & {"compare-pick", "compare-overlap", "compare-outcomes"}
        if comp:
            try:
                await compare_clips(ctx, comp)
            except Exception as e:
                print(f"  compare: FAILED {type(e).__name__}: {str(e).splitlines()[0][:200]}")
        await browser.close()
    write_media()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--media" in sys.argv:
        write_media()
    else:
        asyncio.run(main(args))
