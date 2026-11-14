# Motion review from SUMO runs

Use `astra review motion` to show one run, or two runs side by side, as an animated GIF.
It reads the FCD output of finished SUMO runs and draws every vehicle, bicycle and pedestrian at its recorded
position, one frame per simulated second. It needs no sumo-gui, TraCI or desktop session.

The frames show the named runs. They do not show field traffic, and a smooth picture does not prove topology,
demand or control.

## Input

Prepare a JSON request that follows the
[request schema](../schemas/product/astra.motion-review-request.v1.schema.json).
Relative input paths resolve from the request directory. Record the actual SHA-256 of each file.

```json
{
  "schema": "astra.motion-review-request/v1",
  "bbox": [4420, 2495, 4820, 2795],
  "begin_s": 1800,
  "end_s": 1890,
  "pixels_per_metre": 1.5,
  "buildings": {"path": "buildings.poly.xml.gz", "sha256": "<sha256>"},
  "panels": [
    {"title": "plain netconvert, same trips",
     "net": {"path": "plain.net.xml", "sha256": "<sha256>"},
     "fcd": {"path": "plain/fcd.xml", "sha256": "<sha256>"},
     "tls_states": {"path": "plain/tls_states.xml", "sha256": "<sha256>"},
     "summary": {"path": "plain/summary.xml", "sha256": "<sha256>"}},
    {"title": "ASTRA, same trips",
     "net": {"path": "astra.net.xml", "sha256": "<sha256>"},
     "fcd": {"path": "astra/fcd.xml", "sha256": "<sha256>"}}
  ]
}
```

- `bbox` is `[min x, min y, max x, max y]` in network coordinates. Two panels must share one network offset.
- `begin_s` to `end_s` is at most 599 seconds. Each whole second in it needs an FCD time step in every panel.
- `fcd` needs `x`, `y`, `angle`, `type` and `speed`; a `.gz` file is read directly.
- `tls_states` is the output of SUMO's `SaveTLSStates`; without it no signal is drawn.
- `summary` is SUMO's `--summary-output`; with it each frame names the whole-network counts of that second.
- `buildings` is a SUMO polygon file in the same coordinates.

For the runs of a [load replay](load-replay.md), `astra review replay-motion` records these files, writes the
request and renders it in one step.

A run writes the two inputs with options such as:

```powershell
sumo -n astra.net.xml -r trips.xml -a window.add.xml --fcd-output fcd.xml --fcd-output.filter-shapes fcd_window `
  --fcd-output.attributes id,x,y,angle,type,speed --device.fcd.period 1 --summary-output summary.xml
```

Here `window.add.xml` holds a polygon named `fcd_window` and one
`<timedEvent type="SaveTLSStates" source="<tls id>" dest="tls_states.xml"/>` for each signal in the view.
For a fair comparison both runs use the same trip file, seed and options.

## Run

```powershell
./.venv/Scripts/python.exe -m astra_sumo.cli review motion request.json outputs/motion-v1 --json
```

The output directory must be new. The command writes:

| File | Content |
|---|---|
| `motion.gif` | One frame per second, panels left to right |
| `frame-<second>.png` | The middle frame |
| `motion-review.json` | Status `measured`, input and output SHA-256, and per panel the range of cars, bicycles and pedestrians in view and the stopped cars in the first and last frame |

Stopped vehicles are drawn in magenta, so a queue is visible in a still frame.
The command stops without writing when a hash differs, a second has no FCD step, or the panels use different
network offsets.
