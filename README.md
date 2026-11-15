# AAF naar Resolve (aaf2drt)

Zet een Avid Media Composer-montage om naar een DaVinci Resolve-timeline (.drt), klaar voor de Fairlight-mixer.

*Converts an Avid link-to AAF with OP-Atom MXF media into a DaVinci Resolve timeline (.drt) with trimmed
multichannel WAVs, the editor's channel choice per clip, and a matching Fairlight mixer. Runs fully offline on macOS.*

## Waarom

Avid zet de geluidsopname van een take om naar losse mono MXF-bestanden, één per kanaal. Resolve's eigen
AAF-import geeft dan óf alle kanalen als losse clips, óf neemt niet over welk kanaal de monteur koos. Deze tool
leest de AAF zelf en bouwt de timeline rechtstreeks op:

- **één poly-WAV per clip** met alle iso-kanalen van de take, in de originele volgorde, met handles (standaard 10 frames);
- **het gekozen kanaal** staat per clip al juist ingesteld in Resolve, en stereo clips blijven stereo;
- **gain, volumelijnen, pan en fades** uit Avid gaan mee;
- **beeld**: de originele MXF's, met zoom/positie, snelheid en mattes;
- een **Fairlight-mixer** die past bij de sporen van je sequence (mono/stereo per spoor);
- framerate, resolutie en begintijdcode komen uit de AAF (getest: 24 en 25 fps, 50p-materiaal in een 25p-sequence).

Alles gebeurt lokaal. Er gaat niets naar internet: geen AAF, geen media, geen timeline.

## Installeren

1. Download de zip voor je Mac bij [Releases](../../releases): **AppleSilicon** voor M1 en nieuwer, **Intel** voor oudere Macs.
2. Pak uit en sleep **AAF naar Resolve** naar Programma's.
3. De eerste keer: rechtsklik op de app → **Open** → **Open**. (De app is niet door Apple genotariseerd.
   Lukt dat niet, open dan Terminal en typ `xattr -dr com.apple.quarantine "/Applications/AAF naar Resolve.app"`.)

Python, ffmpeg en alle onderdelen zitten in de app. Je hoeft niets anders te installeren.

## Gebruiken

1. In Avid: exporteer de sequence als **AAF**, met *Link to (Don't Export) Media*.
2. Open de app. Er opent een venster in je browser.
3. Kies de AAF, een uitvoermap en de handles, en klik **Omzetten**.
4. In Resolve: maak een project met **dezelfde framerate** als je Avid-sequence, en kies
   **File → Import → Timeline…** met de .drt uit de uitvoermap.

De uitvoermap bevat de .drt, een map `Audio` met de poly-WAV's, en `aaf2drt_report.json`. Geef de mixer de
volledige uitvoermap en de beeldmedia.

## Beperkingen

Drop-frame-tijdcode (29.97/59.94) wordt juist gerekend, maar is nog niet met een echte AAF getest. Nog niet ondersteund
of niet getest: 5.1- en andere multikanaalsporen, variabele
snelheid (timewarp met keyframes), reverse en freeze frames, AMA-gelinkte media (.mov, BRAW, R3D), genestte
sequences en multicam, crop en positie Y, titels en kleureffecten. Getest met DaVinci Resolve Studio 21.1.

Werkt iets niet? Klik in de app op **Rapport opslaan** en voeg `aaf2drt_rapport.zip` toe aan een
[issue](../../issues). Die bevat het logboek en het rapport (bestandsnamen en tijdcodes), geen media en geen AAF.

## Ontwikkelen

```
pip install -r requirements.txt
python app/main.py                                   # de app (opent de browser)
python app/main.py --engine --aaf film.aaf --out uit # alleen de omzetter
python tests/test_offline.py
```

`engine/` bevat de omzetter: `aafmodel.py` leest de AAF, `polyplan.py` stelt de takes samen uit de MXF's, `gen.py`
schrijft de Resolve-XML, `flmodel.py` maakt de Fairlight-mixer, `codec.py` en `drtio.py` lezen en schrijven het
.drt-formaat. `app/` is de lokale interface. Een nieuwe versie uitbrengen: zet het nummer in `VERSION` en push naar main. GitHub
Actions bouwt dan beide Mac-versies en zet ze in Releases (een nummer met een streepje, zoals `1.6-beta.1`, wordt een
testversie).

Licentie: MIT. Zie [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) voor de meegeleverde onderdelen.
