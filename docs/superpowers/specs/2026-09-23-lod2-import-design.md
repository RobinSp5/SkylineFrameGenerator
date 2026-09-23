# LoD2 Import – Design Spec (Phase 3)

Datum: 2026-09-23
Status: freigegeben
Baut auf: `2026-09-21-skyline-frame-generator-design.md` (MVP) und `2026-09-22-building-detail-design.md` (Detail-Upgrade), beide in `main`

## 1. Ziel

Echte Gebäudehöhen und echte Dachgeometrie aus amtlichen LoD2-Modellen statt Schätzungen, dort wo solche Daten offen verfügbar sind. OpenStreetMap bleibt die weltweite Rückfallebene und liefert weiterhin Straßen, Wasser und Blöcke.

Gemessener Ausgangszustand (Frankfurt, 1500 m):

| Befund | Wert |
|---|---|
| Gebäude mit echter Höhe in OSM | 0,9 % |
| Höhe geschätzt | 71,3 % |
| ohne Dachform | 93,1 % |
| Türme über 40 m, die der Schätzer auf ±5 m trifft | 0 von 20 |

Zielzustand für Gebiete mit LoD2: 0 % geschätzte Höhen, 0 % ohne Dachform.

## 2. Scope

Im Scope:
1. Provider-Schicht für Gebäudedaten mit OSM als Default und LoD2 als optionaler Quelle.
2. Provider „Hessen" (INSPIRE-WFS, verifiziert). Die Provider-Schicht ist so gebaut, dass weitere Quellen ohne Eingriff in die Pipeline dazukommen.
3. Umwandlung von GML-Flächenmodellen in wasserdichte Körper, vereinfacht auf Druckauflösung.
4. Integration in die bestehende Pipeline: LoD2-Körper ersetzen die extrudierten OSM-Gebäude; Blöcke, Straßen, Wasser und Vorrangregeln bleiben unverändert.
5. Herkunfts- und Lizenzangabe je Lauf als Datei neben dem Modell.
6. CLI-, API- und UI-Schalter.

Nicht im Scope: Gelände, Vegetation, LiDAR-Punktwolken und Höhenraster (eigene Phase), weitere Bundesländer über Hessen hinaus einschließlich Bayern (siehe 3.2), LoD3, Texturen.

## 3. Verifizierte Quellen

### 3.1 Hessen (Default-Provider für Hessen)

- Dienst: `https://inspire-hessen.de/ows/services/org.2.ef07833e-78a6-4c2c-a895-e31de788aac3_wfs`
- WFS 2.0.0, `typeNames=bu-core3d:Building`, Abfrage per `bbox` in `urn:ogc:def:crs:EPSG::4326` (Reihenfolge lat,lon), Ausgabe-CRS `urn:ogc:def:crs:EPSG::7423` (ETRS89 + DHHN2016, dreidimensional).
- Abdeckung 7,777 bis 10,224 Ost und 49,396 bis 51,655 Nord.
- Antwort: GML 3.2, je Gebäude ein `gml:MultiSurface` aus `gml:Polygon`-Flächen mit `posList` in `lat lon z`, z absolut in Metern über NN.
- Messung: 3 Gebäude, 388 KB, 0,25 s; die Schirn Kunsthalle besteht aus 137 Flächen und reicht von 96,42 bis 120,63 m.
- Lizenz: Datenlizenz Deutschland – Zero – Version 2.0. Keine Bedingungen, Namensnennung nicht erforderlich. Wir nennen die Quelle trotzdem.

### 3.2 Bayern (verifiziert, aber auf eine spätere Phase verschoben)

Verifiziert am 2026-09-23:

- Kachelindex `https://geodaten.bayern.de/odd/a/lod2/citygml/meta/metalink/{ags}.meta4` mit Dateiname, Größe, SHA-256 und zwei Spiegeln.
- Kachelnamen `{Ostwert_km}_{Nordwert_km}.gml` in UTM32 (EPSG:25832), Rasterweite **2 km** (nicht 1 km).
- Daten unter `https://download{1,2}.bayernwolke.de/a/lod2/citygml/{kachel}.gml`.
- München (AGS 09162): 109 Kacheln, zusammen 6,5 GB, je Kachel 0,6 bis 161,6 MB, Median 59,3 MB.

Zurückgestellt, weil die geforderte Lizenz- und Quellenangabe nicht maschinell auslesbar ist (die Portalseiten
werden erst im Browser aufgebaut). Bei einer Namensnennungslizenz ist eine falsche Quellenangabe ein
rechtlicher Mangel, kein Schönheitsfehler. Der Einbau erfolgt, sobald die Formel wörtlich vorliegt. Die
Abdeckung wird dann nicht aus einer geratenen Stadt-Bounding-Box bestimmt, sondern aus der Vereinigung der
Kachel-Bounding-Boxen des jeweiligen Kachelindex; deckt der Index den Ausschnitt nicht vollständig ab, fällt
der Lauf ganz auf OpenStreetMap zurück statt halb.

### 3.3 Weitere (nur Architektur, kein Einbau in dieser Phase)

NRW, Baden-Württemberg, Rheinland-Pfalz, Sachsen, Sachsen-Anhalt, Brandenburg und Hamburg führen LoD2 als offene Daten. Japan (Project PLATEAU) deckt 247 Städte mit LoD2 ab und erlaubt Verkauf, Bearbeitung und Weitergabe ausdrücklich und ohne Antrag.

## 4. Architektur

Neues Modul `backend/skylineframe/lod2/` mit:

```
lod2/
  __init__.py
  provider.py     Provider-Protokoll + Registry + Auswahl nach Bounding-Box
  gml.py          GML-3.2-Flächen → Polygone mit z (reiner Parser, keine Netzwerkarbeit)
  solidify.py     Flächenmodell → wasserdichter Körper (manifold3d)
  hessen.py       WFS-Provider
  bayern.py       Kachel-Provider
  sources.py      Lizenz- und Quellenangaben je Provider
```

Provider-Protokoll:

```python
class Lod2Provider(Protocol):
    name: str                      # "hessen", "bayern"
    attribution: Attribution       # aus sources.py
    def covers(self, bbox: Bbox) -> bool: ...
    def fetch(self, bbox: Bbox, cache_dir: Path) -> list[Lod2Building]: ...

@dataclass
class Lod2Building:
    osm_id: str                    # f"lod2/{provider}/{localId}"
    surfaces: list[list[tuple[float, float, float]]]   # Ringe in WGS84 lon/lat + z in m
    name: str | None = None
```

`select_provider(bbox)` liefert den ersten Provider, dessen `covers` zutrifft, sonst `None`. Die Reihenfolge ist deterministisch (Registry-Reihenfolge), damit derselbe Ausschnitt reproduzierbar dieselbe Quelle nutzt.

## 5. Geometrie

Amtliche LoD2-Daten sind ein Flächenmodell, kein Körper. Ein Versuch an den echten Frankfurter Daten zeigt,
warum das Zusammensetzen der Flächen allein nicht trägt: einfache Gebäude schließen sich, komplexe nicht.
Die Schirn Kunsthalle (137 Flächen) liefert 8 offene Kanten und 65 Kanten mit drei oder mehr Nachbarflächen,
die Paulskirche (217 Flächen) ebenso. Ursache sind T-Stöße zwischen unterschiedlich unterteilten Wand- und
Dachflächen.

Deshalb wird nicht repariert, sondern geschnitten. `solidify.to_solid(surfaces, transformer)` arbeitet so:

1. Alle Ringe in das lokale metrische System projizieren, z bleibt in Metern über NN.
2. Grundriss: alle Flächen nach 2D projizieren, vereinigen, mit `buffer(+0.05).buffer(-0.05)` säubern und die
   größte Teilfläche nehmen.
3. Dachkörper: nur die nach oben zeigenden Flächen (Normalenanteil in z über 0,05) übernehmen, an jeder
   Fläche eine Schürze bis unter die Bodenhöhe ziehen und den Boden schließen. Eckpunkte auf einen Millimeter
   verschweißen, entartete Dreiecke entfernen.
4. Körper = Prisma über dem Grundriss, geschnitten mit dem Dachkörper.
5. Prüfen: `status == NoError` und `volume > 0`. Schlägt das fehl, wird das Gebäude verworfen und gezählt;
   der Lauf bricht nie ab.
6. Bodenhöhe = kleinstes z, Firsthöhe = größtes z. Der Körper wird um die Bodenhöhe nach unten verschoben,
   sodass er wie jedes andere Gebäude auf der Platte steht. Das Gelände wird bewusst eingeebnet.

Gemessen an den drei Frankfurter Gebäuden: Schirn 1014 m² Grundriss, 24,2 m hoch, 754 Dreiecke; Paulskirche
1324 m², 56,5 m, 1556 Dreiecke; ein einfaches Gebäude 50 m², 3,8 m, 48 Dreiecke. Alle wasserdicht, je unter
0,03 s.

**Dreiecksbudget.** Ein LoD2-Körper kostet ein bis zwei Größenordnungen mehr Dreiecke als das heutige Prisma
mit Dach. Zwei Begrenzungen:

- LoD2-Körper werden nur für einzeln druckbare Gebäude gebaut. Alles, was ohnehin in einem Block aufgeht,
  braucht keinen Körper.
- Nach dem Skalieren wird `Manifold.simplify(tolerance)` mit `tolerance = 0.2 mm` versucht. Der Versuch an
  echten Daten zeigt, dass `simplify` bei zu großer Toleranz einen Körper vollständig auflösen kann; deshalb
  gilt die Vereinfachung nur, wenn das Ergebnis geschlossen ist und mindestens 80 % des Volumens behält,
  sonst bleibt der unvereinfachte Körper stehen.

Die Pipeline meldet `lod2_triangles` in der Statistik, damit die Abnahme das Budget belegen kann.

## 6. Integration in die Pipeline

`fetch_features` bekommt einen zusätzlichen Schritt. Ist ein LoD2-Provider zuständig und `spec.lod2` aktiv:

1. LoD2-Gebäude holen und in Körper verwandeln.
2. Für jedes so gewonnene Gebäude ein `Building` mit dem abgeleiteten Grundriss, `height_m` = Firsthöhe über Boden, `height_is_top = True`, `roof = None` (die Dachform steckt im Körper), zusätzlich Feld `solid_m: Manifold | None` mit dem Körper in lokalen Metern.
3. OSM-Gebäude werden verworfen, wenn ihr Grundriss zu mehr als 50 % von LoD2-Grundrissen überdeckt ist. Der Rest bleibt erhalten (LoD2-Bestände enden an Landesgrenzen und lassen Neubauten aus).
4. Straßen, Wasser und `building:part` kommen unverändert aus OSM. `building:part` wird ignoriert, wenn das zugehörige Gebäude aus LoD2 stammt, weil die Rücksprünge dort bereits im Körper stecken.

`prepare` behandelt LoD2-Gebäude wie andere: Clip, Druckbarkeitsprüfung, Blockbildung über den Grundriss. Nicht einzeln druckbare LoD2-Gebäude gehen wie bisher in den Block ein, ihr Körper wird verworfen.

`scale` skaliert den Körper mit `scale × z_exaggeration` in z und `scale` in x und y, deckelt auf `plate_size_mm` und schneidet ihn auf das Quadrat.

`mesh` nutzt den Körper direkt statt Prisma plus Dach. Die Regel für versenkte Körper (`BUILDING_SINK_MM`) gilt unverändert für die Single-Vereinigung.

## 7. Herkunft und Lizenz

`export_all` schreibt zusätzlich `SOURCES.txt` in das Ausgabeverzeichnis, mit je einer Zeile pro genutzter Quelle, zum Beispiel:

```
Geometrie erzeugt mit Skyline Frame Generator am 2026-09-23.
Gebäude: 3D-Gebäudemodell LoD2 Hessen, Hessische Verwaltung für Bodenmanagement und Geoinformation,
         Datenlizenz Deutschland – Zero – Version 2.0 (https://www.govdata.de/dl-de/zero-2-0).
Straßen, Wasser, Grundrisse: © OpenStreetMap-Mitwirkende, ODbL (https://www.openstreetmap.org/copyright).
Bei Verkauf, Weitergabe oder Veröffentlichung von Drucken und Dateien ist die OpenStreetMap-Namensnennung anzubringen.
```

Der Text je Quelle steht in `sources.py` und wird dort gepflegt, nicht im Code verstreut.

## 8. Bedienung

- `FrameSpec.lod2: bool = True` (nutzen, wenn verfügbar). `extra="forbid"` bleibt.
- CLI: `--lod2/--no-lod2`; die Ausgabe nennt die genutzte Quelle und die Zahl der LoD2-Gebäude.
- Statistik: `lod2_buildings` (Gebäude mit echtem Körper im Modell), `lod2_source` (Name oder leer; leer auch dann, wenn ein Provider zwar geantwortet hat, aber keine seiner Geometrien ins Modell gelangt ist), `lod2_rejected` (auf ein Prisma zurückgefallene Modelle — **zwei Ursachen in einer Zahl**, die die Ausgabe nicht auseinanderhält: Modelle, deren Körper sich nicht schließen ließ, **und** geschlossene Körper, die die Druckbarkeitsprüfung anschließend verworfen hat, weil sie unter `min_building_height_mm` bleiben, die Platte verlassen oder beim Schnitt mit dem vereinfachten Grundriss-Prisma leer bleiben bzw. einen Fehlerstatus liefern; gemessen 2026-09-23 am 1500-m-Quadrat Frankfurt: 8 + 38 = 46).
- UI: Statuszeile nennt die Quelle, zum Beispiel „Fertig: 851 Gebäude, 164 Blöcke, 612 davon aus LoD2 Hessen".

## 9. Verhalten ohne LoD2

Kein Provider zuständig, Dienst nicht erreichbar oder `--no-lod2`: exakt das heutige Verhalten. Ein Ausfall des LoD2-Dienstes darf einen Lauf nie scheitern lassen; er wird geloggt, in `lod2_source` als leer gemeldet und die Pipeline läuft mit OSM weiter.

## 10. Tests

- `gml.py`: Parsen einer kleinen, eingecheckten GML-Antwort (drei Frankfurter Gebäude, aus dem echten Dienst aufgezeichnet), Ringe, z-Werte, Namen.
- `solidify.py`: ein Würfel aus sechs Flächen wird ein geschlossener Körper mit Volumen 1; ein Flächenmodell mit fehlender Grundfläche wird verworfen; Grundriss und Höhen stimmen; die Vereinfachung reduziert die Flächenzahl und lässt den Körper geschlossen.
- `hessen.py`: Bounding-Box-Abdeckung; die URL wird korrekt gebaut (lat,lon-Reihenfolge, 3D-CRS); die Antwort wird über `httpx.MockTransport` aus der Fixture gespeist; Netzfehler führen zu einer leeren Liste und nicht zu einer Ausnahme.
- Pipeline: Lauf über die Fixture mit LoD2 → `lod2_buildings > 0`, `footprint_coverage ≥ 0.95`, wasserdicht; Lauf mit `--no-lod2` → unverändertes heutiges Ergebnis.
- Alle Tests offline. Einmalig Netz nur zum Aufzeichnen der GML-Fixture.

## 11. Kompatibilität

Dateinamen, 3MF-Teile und die bestehende API bleiben. `Building` bekommt ein optionales Feld, alle vorhandenen Aufrufe bleiben gültig. Ohne LoD2-Provider ist das Ergebnis bitgleich zum heutigen Stand.
