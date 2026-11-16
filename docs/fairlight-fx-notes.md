# Fairlight FX in .drt — onderzoeksnotities

## Waar Resolve effecten bewaart

- **FairlightFX op een clip**: in de `FieldsBlob` van `Sm2TiAudioClip`, protobuf → `FL::ClipFX` →
  qCompress-blok. Daarin een `f\x88fw`-container met pluginnaam (`bmd:Reverb:1112363618`) en daarna
  parameters als records van 68 bytes: naam (64 bytes, nul-opgevuld) + float32 little-endian, genormaliseerd 0..1.
- **EQ op een clip** (Inspector → Audio → Equalizer, 6 banden): in `EffectFiltersBA` van de clip. Effect 128 =
  sectie aan; per band een effect 126 met veld 3 = 6, veld 2 = 0 als de band uit staat, en parameters
  `{1: id, 3: {1: {k: zigzag-varint}}}`: 105 bandindex (0–5), 101 type (k=4; 0 shelf, 1 bell, alleen B2/B5),
  102 frequentie in Hz, 104 gain in dB × 10, 103 Q × 1000. B1 = high-pass, B6 = low-pass, B3/B4 = bell.
  Geijkt met handmatige wijzigingen en teruggelezen: Resolve neemt gegenereerde waarden exact over.
- **Inserts op een spoor**: in `FLStudioModelBA` (de mixer, een boom van `f\x88fw`-blokken met benoemde secties).
  Een Reverb-insert raakt vijf plekken (geijkt met exports zonder, met één en met twee inserts, Resolve 21.1):
  sectie `EffectContext` (per instantie een sleutel 37, 38, … met de 25 parameter-id's), een nieuwe sectie
  `EffectPresets` direct daarna (soort 2; blok `[5][N]` + per instantie 2136 bytes: byte 1 = eerste kanaal van
  het spoor, daarna dezelfde 68-byte-parameterrecords als de clip-FX vanaf byte 496), de effectnamentabel
  (`Reverb`, `Reverb:2`, … na `DolbyAtmosRenderer`), de parameternamentabel (`Out 1`, `Out 2`, dan de 25
  Reverb-parameters, id's na de hoogste bestaande) en per kanaal vector 29 = 1 (insert actief; vector 170 = 1
  voor stereo). Plus slot 0 van tabel 0x4a4 = 1. Zie `flmodel._add_reverbs`. EQ op een spoor: nog niet ontcijferd.

## Reverb (FairlightFX) — genormaliseerd v → Inspector-waarde

Gemeten in Resolve 21.1 met v = 0, 0.1, 0.25, 0.4, 0.75, 1 (en standaardwaarden).

| Parameter | Formule |
|---|---|
| Room Height | 2 · 5^v m |
| Room Length | 3 · 10^v m |
| Room Width | 4 · 10^v m |
| Room Size | 12 · 100^v m² |
| Room Ratio | afgeleid (alleen lezen) |
| Pre-Delay (T@-60dB ernaast = Reverb Time) | -200 … 200 ms, niet-lineair, symmetrisch rond v=0.5: (0 → -200, .1 → -55, .25 → -27, .4 → -10, .5 → 0, .75 → 27, 1 → 200) |
| Reverb Time (`T@-60dB`) | 100 · 40^v ms |
| Distance | 30 · v m |
| Brightness | 6 · (100/6)^v |
| Modulation | 100 · v % |
| Early Refl./Late Rev. low Freq | 150 · (10/3)^v Hz |
| Early Refl./Late Rev. high Freq | 1000 · 16^v Hz |
| Early Refl./Late Rev. low/high Gain | 24 · v − 18 dB |
| Dry/Wet | 100 · v % |
| Direct / Early Refl. / Late Rev. gain | 48 · v − 42 dB (v = 0 → −inf) |

## Avid (AAF)

- Clip-effecten: OperationGroup `Audio Suite Plugin`, attribuut `_AUDIO_SUITE_EFFECT_0_ALL_CHUNKS`.
  EQ3 7-Band: benoemde parameters (`d_frq2`, `d_gai2`, `d_bnd2`, `l_in 2`, …), elk 8 bytes naam + 8 bytes
  big-endian double (d_) of long (l_). Volledig leesbaar.
- Track-effecten: OperationGroup `Audio Track Effect Container` rond het spoor, met daarin per plugin een
  OperationGroup en `_ATN_AUDIO_RTAS_EFFECT_CHUNK`.
- D-Verb (clip, real-time AudioSuite): na `igiDbrvR` en de presetnaam, vanaf byte 56, tien big-endian int32's.
  Elk is een knopstand -1..1 (waarde / 2^31); hieronder x = (stand + 1) / 2, dus 0..1. Geijkt met zes exports
  en het aflezen van het dialoogvenster. Let op: Avid bewaart de instellingen pas na **OK** in het venster.

| # | Knop | x → waarde |
|---|---|---|
| 1 | Algorithm | round(6x): Hall, Church, Plate, Room 1, Room 2, Ambient, Non-Lin |
| 2 | Size | round(2x): Small, Medium, Large |
| 3 | Input | lineaire versterking: 20·log10(x) dB |
| 4 | Mix | 100·x % |
| 5 | Diffusion | 100·x % |
| 6 | Decay | min · f(x), min per algoritme (Small: Hall/Church 200 ms, Plate/Room 101 ms, Ambient 51 ms, Non-Lin 101 ms; Medium ×1.5, Large ×2). Hall/Church/Plate/Room: f ≈ e^(4.7x), x=1 → Inf (benadering, ±30 %). Ambient: 10^(x^0.9), max 10× min. Non-Lin: 2.5^(x^0.9) |
| 7 | HF Cut | 200 + 19800·x Hz |
| 8 | LP Filter | 200 + 19800·x Hz, x = 1 → Off |
| 9 | Pre-Delay | x ≤ 0.5: 100·x ms, daarboven 50 + 500·(x − 0.5) ms (max 300) |
| 10 | — | altijd 0 (stand 0.5) |

- Vertaling naar Fairlight Reverb (clip-FX): decay → T@-60dB (max 4 s), pre-delay → Pre-Delay (max 200 ms),
  mix → Dry/Wet, algoritme + grootte → kamerafmetingen (tabel in `aaf2drt.py`), laagste van HF Cut en LP →
  high Freq van early/late (−12 dB bij HF Cut, −18 dB bij LP), input → clipvolume. Diffusion heeft geen
  equivalent. Resolve herberekent Room Size en Room Ratio uit de afmetingen.
- D-Verb op een **spoor**: OperationGroup `Audio Track Effect Container` rond het spoor; segment 1 bevat een
  OperationGroup met `_ATN_AUDIO_RTAS_EFFECT_CHUNK` (zelfde formaat als op een clip) en `INSERT` (slot).

## Plugins van derden (AAX → AU)

- Clip-FX-container (onder `FL::ClipFX`): `[f\x88fw][1][size][5][N]` + N records. Record: `[kanaal][lengte payload]
  [soort: 1 = AU, 2 = FairlightFX]` + 412 bytes kop (plugin-id op +12, bv. `au:1399090297:1349280628`;
  weergavenaam op +384) + payload. Voor een AU is de payload de ClassInfo-plist (manufacturer, subtype, type
  `aufx`, version, `jucePluginState`); Resolve neemt een gegenereerde record ongewijzigd over.
- JUCE-plugins zetten in AAX, AU en VST3 dezelfde state (`VC2!` + lengte + XML). In de AAF zit die in
  `_AUDIO_SUITE_EFFECT_0_ALL_CHUNKS`; AAX-fabrikant- en product-id zijn dezelfde getallen als AU manufacturer
  en subtype. Getest met Soundly Place it 1.1.4.

## Plugins van derden als spoor-insert

- Avid schrijft voor een plugin van derden op een spoor alleen naam, fabrikant- en product-id en `INSERT` in de
  AAF, geen instellingen (drie exports met andere instellingen van Soundly Place it, Media Composer 2024.10).
- Resolve bewaart een VST3-insert in `EffectPresets` als record soort 1 met id `vst3:<class id>`; voor JUCE is dat
  `ABCDEF01 9182FAEB` + fabrikantcode + plugincode (= de AAX-id's). De payload is de VST3-state:
  `[0][lengte - 16][..]` + `VC2!`-state + `JUCEPrivateData` (`fl_vst3juce.bin`). Het kanaal staat in **byte 1**
  van het record (bij clip-FX in byte 0).
- Een lege payload laat Resolve de insert weggooien; een neutrale JUCE-state (`<DEFAULT/>`) laadt de plugin met
  standaardinstellingen. De parameterlijst hoeft niet mee: Resolve vult `EffectContext` zelf aan zodra de plugin
  laadt. Getest: Place it alleen, en samen met twee Reverb-inserts, in één mixer.
