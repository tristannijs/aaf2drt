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
- **plugins** gaan mee waar dat kan (zie hieronder);
- **beeld**: met zoom/positie, snelheid en mattes; de app kopieert alleen de gebruikte stukken van de video (met
  handles, zonder hercoderen) naar een map `Video`, zodat de uitvoermap op zichzelf compleet is;
- een **Fairlight-mixer** die past bij de sporen van je sequence (mono/stereo per spoor);
- framerate, resolutie en begintijdcode komen uit de AAF (getest: 24 en 25 fps, 50p-materiaal in een 25p-sequence).

## Plugins

| Plugin in Avid | Op een clip (real-time AudioSuite) | Als insert op een spoor |
|---|---|---|
| **EQ3 7-Band** | wordt de Equalizer van de clip | nog niet |
| **D-Verb** | wordt een Fairlight Reverb op de clip | wordt een Fairlight Reverb-insert op het spoor |
| **plugins van derden gebouwd met JUCE** (bv. Soundly Place it) | dezelfde plugin als Audio Unit, met exact dezelfde instellingen | dezelfde plugin als VST3-insert, met de standaardinstellingen: Avid zet de instellingen van plugins van derden op sporen niet in de AAF |
| andere plugins | niet; staat in het rapport | niet; staat in het rapport |

De D-Verb-vertaling is een benadering: decay, pre-delay en mix kloppen, kamertype en grootte worden een ruimte
van vergelijkbare afmetingen, diffusion heeft geen equivalent. Fairlight Reverb gaat tot 4 s decay en 200 ms
pre-delay; langer wordt ingekort (het rapport meldt het). Van een D-Verb op een spoor gaat de input-gain niet mee.

Wil je de instellingen van zo'n spoor-plugin toch meenemen, zet de plugin dan in Avid als real-time AudioSuite op
de clips in plaats van op het spoor, of bewaar de instelling als preset in de plugin zelf en kies die in Resolve.

Een plugin van derden moet in Resolve geïnstalleerd zijn op de Mac waar je de timeline opent; anders toont Resolve
hem als ontbrekend, maar de instellingen blijven bewaard. Of een plugin met JUCE gebouwd is, merk je vanzelf: de
app neemt hem dan mee, anders staat hij in het rapport. Avids eigen plugins (D-Verb, EQ3, Pro Compressor, ...)
bestaan alleen als AAX en moeten dus vertaald worden; dat gebeurt voorlopig alleen voor D-Verb en EQ3.

## Welke Resolve-versie

Getest met **DaVinci Resolve Studio 21.1** op macOS. Nieuwere versies werken waarschijnlijk ook (Resolve leest
oudere timelines in); als Blackmagic de opslag van de mixer of de clipeffecten verandert, kunnen mixer, EQ of
plugins wegvallen terwijl clips en audio wel kloppen. **Ouder dan 21 wordt niet ondersteund**: de timeline en de
mixer worden in het formaat van Resolve 21 geschreven. De gratis Resolve 21 zou moeten werken, maar is niet getest.

Alles gebeurt lokaal. Er gaat niets naar internet: geen AAF, geen media, geen timeline.

## Installeren

1. Download de zip voor je Mac bij [Releases](../../releases): **AppleSilicon** voor M1 en nieuwer, **Intel** voor oudere Macs.
2. Pak uit en sleep **AAF naar Resolve** naar Programma's.
3. De eerste keer: rechtsklik op de app → **Open** → **Open**. (De app is niet door Apple genotariseerd.
   Lukt dat niet, open dan Terminal en typ `xattr -dr com.apple.quarantine "/Applications/AAF naar Resolve.app"`.)

Python, ffmpeg en alle onderdelen zitten in de app. Je hoeft niets anders te installeren.

## Gebruiken

1. In Avid: exporteer de sequence als **AAF**, met *Link to (Don't Export) Media*.
2. Open de app. Ze opent in een eigen venster.
3. Kies de AAF, een uitvoermap, de handles voor audio en beeld (0 = geen handles), of het beeld mee gekopieerd
   moet worden, en klik **Omzetten**.
4. In Resolve: maak een project met **dezelfde framerate** als je Avid-sequence, en kies
   **File → Import → Timeline…** met de .drt uit de uitvoermap.

De uitvoermap bevat de .drt, een map `Audio` met de poly-WAV's, een map `Video` met de ingekorte beeldbestanden
(Avid OP-Atom MXF, zelfde codec en tijdcode als het origineel) en `aaf2drt_report.json`. Geef de mixer de volledige uitvoermap.
Zet je het kopiëren van beeld uit, dan verwijst de timeline naar de originele bestanden in je Avid MediaFiles.

Beeld wordt zonder hercoderen ingekort; dat gebeurt voor DNxHD/DNxHR, wat Avid zelf meestal aanmaakt. Andere
codecs worden volledig gekopieerd. Finder kan DNxHD-bestanden vaak niet tonen zonder de codecs van Avid; Resolve
leest ze wel. Stukken van hetzelfde
bestand die minder dan een seconde uit elkaar liggen, worden samen in één bestand gezet.

## Beperkingen

Drop-frame-tijdcode (29.97/59.94) wordt juist gerekend, maar is nog niet met een echte AAF getest. Voor plugins:
zie de tabel hierboven. Nog niet ondersteund of niet getest: EQ op sporen, 5.1- en andere multikanaalsporen,
variabele snelheid (timewarp met keyframes), reverse en freeze frames, AMA-gelinkte media (.mov, BRAW, R3D),
genestte sequences en multicam, crop en positie Y, titels en kleureffecten.

Converteer je opnieuw naar dezelfde uitvoermap, dan hergebruikt de app bestaande WAV's alleen als hun tijdcode
en master clip nog kloppen; anders worden ze opnieuw gemaakt.

Werkt iets niet? Klik in de app op **Rapport opslaan** en voeg `aaf2drt_rapport.zip` toe aan een
[issue](../../issues). Die bevat het logboek en het rapport (bestandsnamen en tijdcodes), geen media en geen AAF.

## Ontwikkelen

```
pip install -r requirements.txt
python app/main.py                                   # de app (eigen venster; --browser voor de browser)
python app/main.py --engine --aaf film.aaf --out uit # alleen de omzetter
python -m pytest tests                               # tests zonder project-materiaal
python tools/regress.py --golden MAP film1.aaf film2.aaf   # regressietest op echte AAF's
```

`tools/regress.py` laat de omzetter lopen op je eigen AAF's en vergelijkt per clip start, duur, bronpositie,
media-start en clipeffecten, plus de grootte van de mixer en het rapport, met een opgeslagen momentopname in MAP.
De eerste keer (of met `--update`) wordt de momentopname gemaakt; daarna toont het elk verschil. Draai het voor
elke release. De AAF's en momentopnames horen niet in deze repository.

`engine/` bevat de omzetter: `aafmodel.py` leest de AAF, `polyplan.py` stelt de takes samen uit de MXF's, `gen.py`
schrijft de Resolve-XML en de clipeffecten, `flmodel.py` maakt de Fairlight-mixer (met inserts), `codec.py` en
`drtio.py` lezen en schrijven het .drt-formaat. Hoe Resolve effecten en plugins opslaat, staat in
[docs/fairlight-fx-notes.md](docs/fairlight-fx-notes.md). `app/` is de lokale interface. Een nieuwe versie uitbrengen: zet het nummer in `VERSION` en push naar main. GitHub
Actions bouwt dan beide Mac-versies en zet ze in Releases (een nummer met een streepje, zoals `1.6-beta.1`, wordt een
testversie).

Licentie: MIT. Zie [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) voor de meegeleverde onderdelen.
