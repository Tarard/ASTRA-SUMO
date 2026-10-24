# Public network development references

`catalog.json` freezes four published SUMO models and their source descriptions.
It records exact repository revisions, artifact sizes, hashes, and licenses.
The two Ingolstadt models belong to the same city family.

Use the [corpus commands](../../docs/public-reference-corpus.md) to download,
check, and prepare the data. Downloads and generated records belong under
`outputs/` or another explicit local artifact directory. Keep the upstream
license files with the data.

The current paired development input is the published InTAS network immediately
before its documented connection correction. The other models provide published
structure targets. Their original raw OSM correspondence remains unresolved.
The current MoST prepared input was regenerated after its published network.
It must not receive labels from coincident numeric identifiers.

These are development references. They do not replace frozen human-review
datasets or supply field defect labels. A published model difference is an
observation about those exact revisions.
