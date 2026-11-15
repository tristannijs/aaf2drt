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
- **EQ en inserts op een spoor**: in `FLStudioModelBA` (de mixer). Nog niet ontcijferd.

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
- D-Verb: tien 32-bit waarden zonder namen; schaal nog niet bepaald (twee metingen).
