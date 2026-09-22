# Frozen-network detector demand resume

Use this path after a corridor network and its official signal topology have
already passed the structural gates. It does not download OSM or Hamburg data,
does not rebuild the network, and does not repeat generic nearest-lane or TLS
binding.

The stable CLI is `plugins/torii-sumo/scripts/build_cached_detector_demand.py`.
It requires:

- the accepted official-TLS manifest (not a loose network file);
- the raw Hamburg count-stream metadata snapshot;
- Torii's canonical 15-minute count CSV;
- an explicit list of dated closed/construction route edges;
- Eclipse SUMO's `routeSampler.py`.

The manifest is the authority for the network, the six MAP/OCIT assets, the
44/44 effective MAP-lane contract, and the 27 official signal bindings. Every
required artifact role must be unique and every recorded SHA-256 must match.
The gate also requires 18 active plus 9 redundant TLD bindings and 33/33 unique
physical movement endpoints.

Example for the audited Sandtorkai Saturday package:

```powershell
.venv\Scripts\python.exe plugins\torii-sumo\scripts\build_cached_detector_demand.py `
  --official-tls-manifest artifacts\hamburg_sandtorkai_twin_20260711\network\official_tls_torii_native_v9\hamburg_official_tls_rebuild.manifest.json `
  --count-stream-snapshot artifacts\hamburg_sandtorkai_twin_20260711\twin_rebuilt_probe\official\counts\count_streams.raw.json `
  --canonical-count-file artifacts\hamburg_sandtorkai_twin_20260711\twin_rebuilt_probe\official\counts\canonical_counts_15min.csv `
  --output-dir artifacts\hamburg_sandtorkai_twin_20260711\twin_v9_saturday_demand `
  --prefix hamburg_sandtorkai_20260711 `
  --simulation-begin 0 --simulation-end 9000 `
  --comparison-begin 1800 --comparison-end 9000 --interval 900 `
  --exclude-route-edge 158068424 `
  --route-sampler-optimize full `
  --route-sampler-script "C:\Program Files (x86)\Eclipse\Sumo\tools\routeSampler.py"
```

`--route-sampler-optimize full` uses SUMO's official SciPy optimizer. The
package declares SciPy as a runtime dependency because stochastic sampling can
leave a non-zero mismatch even when an exact legal solution exists.

The command writes:

- one E1 and one E2 per `(physical node, SUMO lane)` virtual sensor group;
- source-membership and expected-count CSVs for the warm-up plus formal window;
- complete passenger-lane cross-section edgeData for routeSampler;
- candidate-route, source/sink, and detector-incidence audits;
- one detector-constrained plausible SUMO demand realization;
- `cached_detector_demand.manifest.json` with hashes and fail-closed gates.

An exact routeSampler fit is not proof of a unique OD matrix. A dated closed
edge must be absent from both candidate and generated routes. The whole digital
twin remains `review-pending` while the official-TLS manifest's automatic
promotion gate is blocked or historical signal states have not been replayed.
Do not interpret a successful SUMO input load as operational calibration.
