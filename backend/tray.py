"""Optional system-tray icon for LlamaForge.

This is the one piece of LlamaForge that isn't pure stdlib: it needs `pystray`
and `Pillow`. Both are imported lazily and every entry point degrades to a
harmless no-op when they're absent, so the default install stays stdlib-only and
nothing here can break the dashboard. Enable it with `pip install pystray pillow`.

The tray shows the loaded-model count in its tooltip, refreshes it on a timer,
and offers Open dashboard / Quit from the menu. `mark_image()` also draws the
app icons (web/icons/), so the tray, the shortcuts and the panel share one mark.
"""
import threading
import webbrowser

try:                                    # optional deps - absence is fine
    from PIL import Image, ImageDraw
    _PIL = True
except Exception:                       # ImportError, or a broken partial install
    _PIL = False
try:
    import pystray
    _DEPS = _PIL
except Exception:
    _DEPS = False


def available():
    return _DEPS


# The Stowage mark on a 32-unit grid, the favicon's geometry (web/index.html):
# an amber hold, orange + blue stowed on top, green bottom-left, one empty bay.
_HULL, _AMBER, _EMPTY = "#13243f", "#ffc21a", "#a6bad0"
_BOXES = (((6, 6, 18, 15), "#e8661c"), ((19.5, 6, 26, 15), "#2c6fb7"), ((6, 17, 14, 26), "#26754f"))


def mark_image(size, loaded=False, rounded=False):
    """The mark as a size x size RGBA image. `loaded` stows an amber container in
    the empty bay; `rounded` gives the tile transparent rounded corners (app icons)."""
    ss = 4                                         # supersample, then downscale
    k = size * ss / 32
    big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    u = lambda *v: [round(x * k) for x in v]
    if rounded:
        d.rounded_rectangle(u(0, 0, 32, 32), radius=round(4 * k), fill=_HULL)
    else:
        d.rectangle(u(0, 0, 32, 32), fill=_HULL)
    d.rectangle(u(1.5, 1.5, 30.5, 30.5), outline=_AMBER, width=round(2 * k))
    for box, colour in _BOXES:
        d.rectangle(u(*box), fill=colour)
    if loaded:
        d.rectangle(u(15, 17, 26, 26), fill=_AMBER)
    else:
        d.rectangle(u(15, 17, 26, 26), outline=_EMPTY, width=round(1.5 * k))
    return big.resize((size, size), Image.LANCZOS)


def _icon_image(loaded):
    """The 64x64 tray icon: the mark, with the empty bay filled while a model is loaded."""
    return mark_image(64, loaded=loaded)


def start(panel_port, counts_fn, refresh_secs=5):
    """Start the tray in a background thread. `counts_fn` returns (loaded,total).
    Returns the pystray Icon, or None when deps are missing / startup fails."""
    if not _DEPS:
        return None
    url = f"http://127.0.0.1:{panel_port}/"

    def _title(loaded, total):
        return f"LlamaForge - {loaded}/{total} loaded"

    try:
        loaded, total = counts_fn()
    except Exception:
        loaded, total = 0, 0

    icon = pystray.Icon(
        "llamaforge", _icon_image(loaded), _title(loaded, total),
        menu=pystray.Menu(
            pystray.MenuItem("Open dashboard", lambda *_: webbrowser.open(url),
                             default=True),
            pystray.MenuItem("Quit", lambda ic, *_: ic.stop()),
        ))

    def _refresh():
        while True:
            threading.Event().wait(refresh_secs)
            try:
                loaded, total = counts_fn()
                icon.icon = _icon_image(loaded)
                icon.title = _title(loaded, total)
            except Exception:
                pass

    try:
        threading.Thread(target=_refresh, daemon=True, name="tray-refresh").start()
        threading.Thread(target=icon.run, daemon=True, name="tray").start()
        return icon
    except Exception:
        return None
