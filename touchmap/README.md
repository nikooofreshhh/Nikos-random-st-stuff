# touchmap

Turns capacitive touchscreen input into a static image plus a text summary:
a dwell heatmap, stroke paths (colour = speed, width = contact area, chevrons = direction),
and a continuity timeline (finger down / lifted). Meant to be read by a person or a language model.

Capture happens in Chrome on the phone; a tiny Python server in Termux saves and renders.
No root needed. Only dependency is Pillow.

## Setup (Termux)

```
pkg install python python-pillow
cd touchmap
python server.py
```

Then open `http://127.0.0.1:8765` in Chrome on the same phone. Touch the black area,
tap **Save**. The page shows links to the image, the summary and the raw JSON.
Files land in `touchmap/sessions/<timestamp>.{png,txt,json}`.

Tips

- `termux-wake-lock` keeps the server alive while Chrome is in front.
- The status line says whether your panel reports a varying contact size. If it says
  "constant", stroke width means nothing on that device and the summary says so.
- Re-render an old session after tweaking `render.py`: `python render.py sessions/<name>.json`.
- `python server.py --host 0.0.0.0` if you want to open the page from another device on the same network.

## What the output encodes

| Panel | Encodes |
|---|---|
| DWELL | Time spent per location, Gaussian-splatted. Bright = lingered. Holds show up strongly. |
| PATHS | One path per finger-down to finger-up. Colour = speed (fixed scale, dark red slow to light blue fast). Width = contact area, the stand-in for pressure. White chevrons = direction, hollow circle = start, dot = end. Ring = tap, ring with dot = hold (ring size grows with hold length). |
| CONTINUITY | One lane per simultaneous finger. Bars where the finger was down, gaps where it lifted. Colour = speed, thickness = contact area. |
| summary .txt | Per-stroke numbers: kind (tap, hold, straight drag, curved drag, back-and-forth, circular), timing, path length, mean and peak speed, start and end region, direction, firmness; plus overall tempo, mix and rhythm. |

The image carries shape and rhythm; the text carries the magnitudes. Send both.

## Raw JSON

```
{ "started": ISO time, "canvas": {"w","h","dpr"},
  "events": [ {"id": pointerId, "k": "d|m|u|c", "t": ms, "x", "y", "w", "h", "p"} ... ] }
```
`w`/`h` are the contact ellipse in CSS px (Chrome's PointerEvent.width/height).
