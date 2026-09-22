# Composable signal-device classification for Germany and Europe

Classify signal devices before binding them to lanes, movements, stop lines,
signal groups, or controllers. Use German law and engineering terminology as
the first profile, then preserve jurisdiction and edition so the same model can
carry European and ASAM OpenDRIVE mappings.

## Non-negotiable boundaries

- Do not create or infer one flat `signal_type`. A signal installation is a
  composition of legal profile, audience, physical head, display components,
  modality, logical signal group, applicability, and controller behavior.
- Keep classification read-only. A recognized device may rank binding
  hypotheses, but it never authorizes a lane/movement binding, signal-group
  assignment, controller assignment, phase plan, or live control action.
- Do not infer a signal group from a head shape or shared pole. In German usage,
  one `Signalgruppe` may contain several physical `Signalgeber` that always
  show the same state.
- Do not equate a displayed color with the traffic-engineering state. Preserve
  the observed `Signalbild` separately from `Frei`, `Gesperrt`, or another
  semantic state.
- Preserve `unknown`, alternatives, and contradictions. Do not fill missing
  device, group, movement, or controller evidence with a convenient default.

Emit these gates explicitly:

```text
classification_only       = true
automatic_authorization   = blocked
binding_decision          = review_required | blocked | not_applicable
control_decision          = blocked | not_applicable
```

## Canonical composition

Use stable identifiers and independent fields. Keep jurisdiction-specific
names and codes as references or adapters rather than canonical object types.

```text
SignalSystem
  system_id
  controller_refs[]
  group_ids[]

SignalGroup
  group_id
  head_ids[]
  audience[]
  applies_to[]
  allowed_displays[]
  semantic_states[]

SignalHead
  head_id
  family
  physical_role
  pose_and_dimensions
  display_components[]
  modalities[]

DisplayComponent
  component_id
  interface_slot?
  modality
  color?
  symbol?
  direction?
  mode
  chamber_index?

StandardRef
  authority
  document
  jurisdiction
  edition_or_revision
  native_code?
  uri
  retrieved_at
```

Use finite values where evidence supports them, with `other` and `unknown`
escape values:

- `family`: `alternating_road`, `two_aspect_yellow_red`,
  `single_green_arrow`, `lane_control`, `tram_bostrab`, `warning_beacon`,
  `accessibility_auxiliary`, `other`, `unknown`;
- `physical_role`: `main`, `duplicate`, `repeater`, `overhead`, `auxiliary`,
  `other`, `unknown`;
- `audience`: `motor_vehicle`, `bus`, `tram`, `bicycle`, `pedestrian`,
  `pedestrian_bicycle`, `accessibility`, `lane_control`, `special`, `unknown`;
- `modality`: `visual`, `acoustic`, `tactile`, `mixed_accessibility`, `other`,
  `unknown`;
- `color`: `red`, `yellow`, `green`, `white`, `other`, `none`, `unknown`;
- `symbol`: `circle`, `arrow`, `pedestrian`, `bicycle`,
  `pedestrian_bicycle`, `bostrab_bar`, `bostrab_point`, `bostrab_triangle`,
  `lane_cross`, `lane_down_arrow`, `lane_merge_arrow`, `other`, `none`,
  `unknown`;
- `mode`: `steady`, `flashing`, `dark`, `other`, `unknown`;
- `applies_to`: typed references to a stop line, lane, approach, connection,
  movement, or crosswalk.

Do not impose OCIT's three-slot transport representation as a universal limit.
German tram signals use white displays, accessibility devices add acoustic and
tactile outputs, and another jurisdiction may require other components.

## Germany-first legal profiles

Treat these as compositions rather than mutually exclusive global types:

| Profile | Source-backed content | Modeling consequence |
| --- | --- | --- |
| `DE_StVO_37_Wechsellichtzeichen` | Standard red/yellow/green sequence, directional arrows, lane-specific signals, pedestrian and bicycle symbols, and special signals for public transport or specified traffic. A reduced yellow-red signal is permitted. | Separate color, symbol, direction, audience, layout, and applicability. |
| `DE_StVO_37_single_green_arrow` | A one-field green arrow can release only the indicated turn while the main through signal remains red. | Model the arrow head and its movement applicability; do not treat it as an ordinary full green group. |
| `DE_StVO_37_Dauerlichtzeichen` | Red crossed bars close a lane, a green downward arrow opens it, and a flashing yellow oblique downward arrow orders a merge in that direction. | Use the lane-control family and bind only after lane evidence is reviewed. |
| `DE_BOStrab_F` | F0-F5 use white bars, a point, and an inverted triangle for tram movements. | Use a BOStrab legal profile and tram audience; do not force these displays into red/yellow/green. |
| `DE_StVO_38_warning` | Yellow flashing light warns of danger. | Keep warning beacons outside ordinary alternating road signals. |
| `DE_DIN_32981_accessibility` | Acoustic and tactile facilities assist blind and partially sighted pedestrians at traffic signals. | Model accessibility as modality/capability, not as a visual-green subtype. |

Use the VwV-StVO installation language to distinguish `Hauptsignalgeber` from
repeated, additional, or overhead heads. Installation role is independent of
audience, symbol, and logical group.

## OCIT decomposition and the `Gruen` slot trap

Keep these German technical objects separate:

```text
Lichtsignalanlage  = the installation/system
Steuergeraet       = controller
Signalgruppe       = logical signals with the same signaling state over time
Signalgeber        = physical signal head
Signalkammer       = physical chamber/lamp
Signalbild         = displayed pattern or encoded output
Signalisierungszustand = semantic signaling state
```

OCIT-O exposes separate `SignalGruppe (1:501)`, `SignalGeber (1:502)`, and
`SignalKammer (1:503)` objects. OCIT-C supply data deliberately avoids a flat
`Signalgruppentyp`; it describes release/blocking displays, transitions,
allowed signal images, controlled `Verkehrsart`, and lamp outputs instead.

Apply this parser rule without exception:

> An OCIT-C `Lampenausgaenge/Gruen` path is an interface slot, not proof of a
> visual green lamp.

OCIT-C labels the supported maximum-three lamp-output containers `Rot`, `Gelb`,
and `Gruen` and associates them with chamber numbers 0, 1, and 2. Official
Hamburg OCIT-C supply artifacts demonstrate that accessibility outputs
described as `Ton/Vibra` can occur in the `Gruen` slot. When the slot label,
group name, `Verkehrsart`, lamp names, or other evidence indicates
acoustic/tactile equipment:

```text
interface_slot = gruen
modality       = acoustic | tactile | mixed_accessibility
color          = none | unknown
visual_green   = false
```

Never emit `color=green`, a green visual head, or a vehicle release merely
because the XML ancestor is named `Gruen`. Require explicit visual-device
evidence for visual color. Likewise, keep the OCIT bit-coded `Signalbild`
separate from the safety/traffic state: OCIT-C permits different displays for
the same `Frei` or `Gesperrt` state.

## Recognition order

1. Freeze and hash the source artifact; record URL or path, authority,
   jurisdiction, edition/revision, retrieval time, and parser version.
2. Select the legal profile. Use StVO for German road traffic, BOStrab for tram
   running signals, and a named European or foreign profile only when supported.
3. Identify the physical installation and head candidates from explicit object
   records, surveyed inventory, calibrated imagery, or reviewed geometry.
4. Classify audience and modality before interpreting a color-named schema
   slot. Preserve combined pedestrian/bicycle and accessibility evidence.
5. Classify each observable display component: actual modality, color, symbol,
   direction, steady/flashing/dark mode, chamber position, and physical role.
6. Bind applicability only from stop-line, lane, approach, connection,
   movement, or crosswalk evidence. Proximity is only a candidate feature.
7. Infer a signal group only from controller/supply data or independently
   verified identical state trajectories. Allow several heads per group.
8. Associate controllers, programs, phases, timing, and control methods in a
   separate reviewed step. Never derive them from the device classification.
9. Emit field-level evidence, alternatives, status, and the blocked
   authorization gates.

## Evidence thresholds

Give every asserted field its own envelope:

```text
value
status = observed | rule_derived | reviewed | contradicted | unknown
decision = pass | review_required | blocked | not_applicable
evidence_ids[]
alternatives[]
rationale
```

Apply these minimum thresholds:

- Treat an official legal text as evidence for permitted meaning, never as
  proof that a particular device exists at a site.
- Treat a standard's scope as evidence for model separation, never as a local
  inventory record or an operational timing plan.
- Use controller or provisioning data to support group membership only after
  validating object identity, revision, and the meaning of every adapter field.
- Use calibrated multi-view imagery or a surveyed inventory for physical head,
  symbol, orientation, and mounting role. A single oblique image normally
  requires review.
- Use MAP/MAPEM, reviewed lane geometry, stop lines, and legal connections for
  applicability. OSM tags, names, shared poles, nearest distance, or road-node
  degree alone remain candidate evidence.
- Require observed state traces or an authoritative controller relation before
  claiming that several heads share one group. Similar appearance is
  insufficient.
- Keep accessibility outputs separate even when they follow the pedestrian
  release state. Coupled timing does not make an acoustic or tactile output a
  visual chamber.

## Europe and OpenDRIVE compatibility

Use the Vienna Convention as a broad legal-meaning compatibility profile for
red/yellow/green, arrows, flashing indications, and pedestrian signals. Keep
national implementation and device evidence authoritative for Germany.

For ASAM OpenDRIVE, preserve `country`, `countryRevision`, `type`, and
`subtype`. If a traffic light has no official German catalog code, use the
normative ASAM Signal Reference with `country="OpenDRIVE"`; do not invent a
`country="DE"` VzKat code. Keep physical signal placement, stop-line/lane
validity, signal-group/controller relation, and dynamic state separate.

## Primary sources

German law and administrative guidance:

- StVO section 37, *Wechsellichtzeichen, Dauerlichtzeichen und Gruenpfeil*:
  <https://www.gesetze-im-internet.de/stvo_2013/__37.html>
- StVO section 38, warning signs and yellow flashing light:
  <https://www.gesetze-im-internet.de/stvo_2013/__38.html>
- VwV-StVO, including the administrative guidance for section 37:
  <https://www.verwaltungsvorschriften-im-internet.de/bsvwvbund_26012001_S3236420014.htm?level=1>
- BOStrab section 21 and Annex 4:
  <https://www.gesetze-im-internet.de/strabbo_1987/__21.html>
  and <https://www.gesetze-im-internet.de/strabbo_1987/anlage_4.html>

German engineering and interface sources:

- FGSV RiLSA product/edition metadata:
  <https://www.fgsv-verlag.de/rilsa>
- FGSV's public RiLSA correction sheet, including examples that distinguish
  `Signalgruppe`, multiple `Signalgeber`, and `Steuergeraet`:
  <https://www.fgsv-verlag.de/pub/media/pdf/321.k.29072015.pdf>
- OCIT downloads and current-version index:
  <https://www.ocit.org/de/ocit/downloads/>
- OCIT-O Glossary V3.1:
  <https://www.ocit.org/media/ocit-o_glossar_v3.1_a01.pdf>
- OCIT-O Traffic Signal Controller V3.1, including the separate group, head,
  and chamber objects:
  <https://www.ocit.org/media/ocit-o_lstg_v3.1_a01n.pdf>
- OCIT-C LSA supply data V2.2, including the compositional signal-group and
  lamp-output representation:
  <https://www.ocit.org/media/ocit-c_lsa_versorgungsdaten_v2.2_a01.pdf>
- OCIT-C user agreement:
  <https://www.ocit.org/media/ocit-c_nutzungsvereinbarung_2024_1.pdf>
- Hamburg's official OCIT-C asset directory and two field examples containing
  `Ton/Vibra` accessibility outputs under `Lampenausgaenge/Gruen`:
  <https://daten-hamburg.de/tlf_public/OCIT-C/>,
  <https://daten-hamburg.de/tlf_public/OCIT-C/MAP_ITS_23_2394_9.3.xml>,
  and <https://daten-hamburg.de/tlf_public/OCIT-C/MAP_ITS_02_228_18.5.xml>

European product and legal sources:

- DIN EN 12368:2024-06 metadata, *Traffic control equipment - Signal heads*:
  <https://www.dinmedia.de/en/standard/din-en-12368/378833815>
- DIN EN 50556:2019-03 metadata, *Road traffic signal systems*:
  <https://www.dinmedia.de/en/standard/din-en-50556/299535833>
- DIN 32981:2018-06 metadata, accessibility equipment at traffic signals:
  <https://www.dinmedia.de/en/standard/din-32981/284006135>
- UNECE, consolidated Vienna Convention on Road Signs and Signals:
  <https://unece.org/transport/publications/convention-road-signs-and-signals-1968-european-agreement-supplementing>

ASAM sources:

- ASAM OpenDRIVE 1.9 signal introduction:
  <https://publications.pages.asam.net/standards/ASAM_OpenDRIVE/ASAM_OpenDRIVE_Specification/v1.9.0/specification/14_signals/14_01_introduction.html>
- ASAM OpenDRIVE Signal Reference catalog:
  <https://publications.pages.asam.net/standards/ASAM_OpenDRIVE/ASAM_OpenDRIVE_Signal_reference/latest/signal-catalog/01_road_signals/road_signals.html>
- ASAM OpenDRIVE junction traffic-light guideline:
  <https://publications.pages.asam.net/standards/ASAM_OpenDRIVE/ASAM_OpenDRIVE_Junction_guideline/1.0.0/junction-guidelines/09_traffic_lights/09_traffic_lights.html>
- ASAM OpenSCENARIO traffic-signal physical/dynamic separation:
  <https://publications.pages.asam.net/standards/ASAM_OpenSCENARIO/ASAM_OpenSCENARIO_XML/v1.3.0/06_general_concepts/06_11_traffic_signals.html>

## Copyright and conformance boundary

- Cite German legal texts directly and retain the retrieved version.
- Treat RiLSA and DIN/EN full text as copyrighted/licensed material. Use public
  publisher metadata and public corrections for repository documentation; do
  not reproduce protected tables, figures, or substantial wording without the
  required access and permission.
- Treat public OCIT PDFs as copyrighted specifications and follow the current
  OCIT usage agreement for data specifications, schemas, trademarks, and
  redistribution. Do not copy or redistribute protected XML/XSD packages or
  code tables merely because a conceptual adapter is useful.
- Describe Torii's mappings as independent, source-cited projections. Do not
  claim RiLSA, DIN/EN, OCIT, Vienna Convention, or ASAM conformance from a
  taxonomy match or parser result alone.
- Record exact editions and retrieval dates. A URL labeled `latest` is a lookup
  convenience, not a stable evidence identity.
