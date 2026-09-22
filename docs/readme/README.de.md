<p align="center">
  <img src="../assets/banner.png" alt="Torii for SUMO" width="100%">
</p>

# Torii

<p align="center">
  <strong>Task-Oriented Road Infrastructure Intelligence für Eclipse SUMO</strong>
</p>

<p align="center">
  Torii verwandelt reale Verkehrsdaten und natürlichsprachliche Aufgaben in SUMO-Simulationen.
</p>

<p align="center">
  <a href="https://tarard.github.io/Torii-SUMO/">Website</a> ·
  <a href="../codex-plugin-install.md">Installation</a> ·
  <a href="../README.md">Dokumentation</a> ·
  <a href="../../examples/01_signal_control_audit/task.md">Beispiele</a> ·
  <a href="../../LICENSE">MIT-Lizenz</a>
</p>

<p align="center">
  <a href="../../README.md">English</a> ·
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.de.md">Deutsch</a>
</p>

<p align="center">
  <img src="../assets/torii-agent-architecture.svg" alt="Agent-gesteuerter Torii-Workflow zur Konstruktion von Verkehrsszenarien" width="100%">
</p>

## Was Torii macht

<table>
<tr>
<td width="33%" valign="top">
<h3>Erstellen</h3>
Erstellt SUMO-Netze aus OpenStreetMap, Fahrzeugtrajektorien und Straßenbauplänen.
</td>
<td width="33%" valign="top">
<h3>Kalibrieren</h3>
Rekonstruiert Verkehrsnachfrage und kalibriert Simulationen mit realen Sensormessungen.
</td>
<td width="33%" valign="top">
<h3>Simulieren</h3>
Führt weitere SUMO-Experimente anhand natürlichsprachlicher Anweisungen aus.
</td>
</tr>
</table>

## Schnellstart

```powershell
codex plugin marketplace add Tarard/Torii-SUMO --ref main
codex plugin add torii-sumo@torii-sumo
```

Danach kann Codex zum Beispiel so angewiesen werden:

```text
Use Torii to build a SUMO network from this OSM area.
Check connectivity, traffic signals, and routeability.
```

Torii benötigt Python 3.11+ und Eclipse SUMO.

## Hamburg Digital Twin

Torii wird verwendet, um einen realen Verkehrskorridor in der Hamburger Innenstadt zu rekonstruieren und zu validieren.

<p align="center">
  <img src="../assets/hamburg-digital-twin/torii-v1-corridor-aerial-sensors.png" alt="Mit Torii V1 rekonstruierter Hamburger Korridor auf Luftbilddaten mit Fahrspurverbindungen und virtuellen Sensoren" width="100%">
</p>

<p align="center"><sub>Torii-V1-Korridor auf Luftbilddaten mit Fahrspurverbindungen und virtuellen Sensoren.</sub></p>

Torii kombiniert offizielle Verkehrsdaten, Luftbilder und die Rekonstruktion von SUMO-Netzen in einem einzigen Workflow.

<p align="center">
  <img src="../assets/hamburg-digital-twin/torii-v1-four-stage-comparison.png" alt="LSA118-Rekonstruktion in vier Stufen: offizielle MAP-Endpunkte und Fahrtrichtungen, rekonstruierte Kurven sowie SUMO-Fahrspurverbindungen vor und nach der Bereinigung" width="100%">
</p>

<p align="center"><sub>LSA118-Rekonstruktion: offizielle MAP-Endpunkte und Fahrtrichtungen, rekonstruierte Kurven sowie Fahrspurverbindungen vor und nach der Bereinigung.</sub></p>

Die aktuelle Hamburg-Kalibrierung stimmt exakt mit der aggregierten Detektoranzahl überein, mit **0,15 Fahrzeugen MAE pro 15-Minuten-Intervall**.

## Dokumentation

[Architektur](../architecture.md) ·
[Installation](../codex-plugin-install.md) ·
[Dokumentation](../README.md) ·
[Beispiele](../../examples/01_signal_control_audit/task.md)

## Lizenz

Torii-SUMO steht unter der [MIT-Lizenz](../../LICENSE).

Frühere Releases sind auf [Zenodo](https://doi.org/10.5281/zenodo.20627976) archiviert.
