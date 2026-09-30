# Research: survey of other lighting-show formats to decode for lightai's reference library

Goal: a private, never-redistributed reference library of other people's light shows (any major
program) decoded into one common model — sections/cues, timing/BPM, palettes, effect types,
energy curve, fixture types, spatial moves — plus 3D stage/fixture data for previews. Scope was
web research only: no show files were bulk-downloaded, nothing outside this file was edited.

Each format is scored 1–3 on three axes and ranked by the product (max 27):
**Value** = how much club-relevant signal it carries · **Decodability** = Open (documented
spec/XML + working parser) / Community (reverse-engineered or hybrid, no official spec) / Closed
(no spec, no parser found) · **Availability** = how much is free and public. Bracketed numbers
cite the [Sources](#sources) list; every one is a page actually opened via search or fetch.

## Ranked survey

| Format | Program | Decodable? | Free public sources | Est. volume | License / ToS notes | Club value |
|---|---|---|---|---|---|---|
| GDTF (fixture defs, DIN SPEC 15800) | any (MA, Vectorworks, Robe, ETC…) | **Open** — spec [41], MIT parsers: python-gdtf [45], Rust gdtf_parser [46] | gdtf-share.com library [48] | 7,000+ fixture files [48] | Free acct required; no commercial reuse w/o license [50] | High |
| .xsq sequences | xLights | **Open** — plain XML; app GPL-3.0 [1]; GPL-3.0 parser webXLights [2] | xLightsSeq.com [5], ACL forum [7], built-in downloader, GitHub repos [8] | 1,700+ seqs, 100k+ downloads [5] | Uploader keeps copyright, no site-wide redistribution right [6]; audio usually excluded | High |
| MVR scenes (DIN SPEC 15801) | Vectorworks, MA3, Depence, L8, Capture, WYSIWYG… | **Open** — spec [41]; zlib-style "MVR License 1.0" C++ lib [42]; MIT python-mvr [43], used by GPL-3.0 BlenderDMX [44] | Sample .mvr in libMVRgdtf & MomentFactory repos [49], gdtf-share forum thread [48] | Dozens, growing (format is 2022+) | Same gdtf-share acct/ToS as GDTF [50] | High |
| .lms / .loredit sequences | Light-O-Rama | **Open core** — XML per LOR's own docs [15]; compressed `.lcs` variant reverse-engineered [16]; MIT player libreorama actually plays `.lms` [17] | LOR forum "Sequence Sharing" (6,800+ topics) [18], individual giveaway threads [19] | Large but scattered across threads | Forum-shared, informal "personal use" norm; audio rarely included | High |
| glTF / OBJ / 3DS generic models | universal | **Open** — Khronos/ISO standards; GDTF geometry itself is usually embedded OBJ/glTF | Sketchfab, xLights' own OBJ meshes [4] | Very large | Per-model license varies (CC0 to non-commercial) — check each | Medium |
| Resolume .avc compositions | Resolume Arena/Avenue | **Open** — plain XML, confirmed by MIT converter tool [20] | Resolume forum shares [21] | Moderate | Community-shared; more video/VJ than DMX-fixture data | Medium |
| .tim sequences | Vixen 3 | **Community** — XML, readable via open app source [11]; app itself source-available under a custom BSD-like license, not GPL [12] | AusChristmasLighting thread [14], kbarre123/vixen_sequences [13] | Smaller than xLights community | Same "creator keeps rights" norm as other hobby forums | High |
| .chb sequences | Freestyler DMX | **Community** — binary, but byte layout fully documented on the official wiki (step count, fade multiplier, per-channel value/mode words) [22] | Support-forum sample thread [23] | Modest | Freestyler itself is free; sample thread informal | Medium |
| .show / .show.gz | grandMA2 | **Community** — zip of XML folders (bitmaps/colors/effects/gobos…), confirmed via forum [24] + GPL-3.0 helper lib [25] | Almost none (pro shows are confidential); one bitmap-library giveaway bundles a showfile [27] | Very low | N/A — no public full-show culture in this industry | High |
| .lightkeyproj | Lightkey (macOS) | **Community** — NSKeyedArchiver plist, fully reverse-engineered incl. class schemas, MIT [29] | None found in bulk | ~0 public examples | MIT tooling, but no sample-project corpus | Medium |
| grandMA3 show / Lua XML export | grandMA3 | **Community** — exports as XML, Lua components documented [28] | Even rarer than MA2 (newer console) | Very low | Same confidentiality norm as MA2 | Medium |
| .d4z / .d4b showfiles | Avolites Titan | **Hybrid** — zip containing a binary copy plus an auto-generated XML copy [30]; one unofficial web parser, no public source [31] | Scattered troubleshooting posts only | Very low | Unofficial tool, "not Avolites-approved" [31] | Medium |
| USITT ASCII export (.asc) | ETC Eos + many consoles | **Open** — simple plain-text spec, free from USITT [39]; QLC+ itself has an (unbuilt) importer request confirming the format's simplicity [40] | Scarce standalone samples | Very low | Public-domain-style industry standard | Low (theatrical, dimmer-centric, little chase/color/movement data) |
| .ssproj | SoundSwitch | **Closed** — no spec or parser found | Per-song "Scripts" exist but live behind SoundSwitch's own cloud service, not as files | ~0 downloadable files | Closed ecosystem; don't attempt extraction | High (best conceptual analog, but inaccessible) |
| .shw / .xhw | ChamSys MagicQ | **Closed** — contents described only at a conceptual level [32] | None in bulk | ~0 | — | Medium |
| .ONYXShow | Obsidian ONYX | **Closed** — no spec found [33] | None found | ~0 | — | Low |
| Daslight / Sunlite show files | Nicolaudie | **Closed** — no spec or parser found | Forum "free show templates" thread exists [35] but format unparseable | Low | — | Low |
| DMXIS project (txt automation) | Enttec DMXIS | **Partial** — fixture library format documented [38]; show itself is thin DAW-automation data, not a rich cue format | None in bulk | ~0 | — | Low |
| MADRIX show/timeline | MADRIX | **Closed** for the show/timeline; fixture library `.mflx` is XML [36] | None found | ~0 | — | Medium (LED/pixel content very club-relevant, but undecodable) |
| .hog / .h3 showfiles | Hog 4 / Road Hog | **Closed** — no spec found despite a dedicated forum thread [37] | None found | ~0 | — | Medium |
| ShowXpress show files | Chauvet DJ | **Closed** — no documentation found; could not confirm shared lineage with Freestyler | None found | ~0 | Free software, but format undocumented | Low |
| .c2p / .c2s | Capture | **Closed** — binary, magic bytes only, no parser [via fileinfo] | Official demo packs + one free Club sample in Student Edition [52] | Handful | Evaluation-only EULA, not for redistribution | Medium |
| .vwx | Vectorworks Spotlight | **Closed** — proprietary container, unsolved per Archive Team [54] | A few "completed project" education samples [53] | Very low | Needs paid app or viewer; **use its MVR export instead** | Medium |
| .wyg | WYSIWYG (CAST) | **Closed** — dongle-locked, proprietary [55] | None in bulk | ~0 | — | Low |
| Depence / Realizzer / L8 native | Syncronorm / Realizzer / L8 | **Closed** natively, but all three import/export MVR or generic OBJ/FBX/Collada instead [56][57][58] | N/A — **route through MVR** | N/A | — | Low (route via MVR) |
| .esf (native) | ETC Eos | **Closed** — proprietary; only the ASCII export (above) is open [38] | None | ~0 | — | Low |
| .qxw (native) | QLC+ (this app) | **Open, N/A** — already the target format; plain XML, Apache-2.0 [59] | Ships its own `resources/samples/` [60] + QLC+ forum | Small | Apache-2.0 | Baseline |

OSC and MIDI/SMPTE timecode are trigger protocols, not archival show formats — the actual
cue/effect/color data lives inside whichever program they're driving (already covered above), so
they were not scored as a separate format.

## Recommended first wave

**Show formats (build a decoder in this order):**
1. **xLights `.xsq`** — highest value×decodability×availability of anything surveyed. Pull from
   xLightsSeq.com's "Free Music/Miscellaneous Sequences" categories [5] and the AusChristmasLighting
   sharing thread [7]; GitHub code search `extension:xsq` and `filename:xlights_rgbeffects.xml`
   turns up repos like cp16net/xlights-sequences [8]. Decoder needs: an XML parser for
   `<ColorPalettes>`, `<EffectDB>`, `<DisplayElements>`/`<ElementEffects>` (cues+timing+color), plus
   `xlights_rgbeffects.xml` for fixture/3D layout. Optionally add Cryptkeeper's reverse-engineered
   FSEQ decoder [9][10] to pull the literal per-frame DMX output for energy-curve extraction.
2. **Light-O-Rama `.lms`/`.loredit`** — second-highest; genuinely open XML at the core, plus a
   maintained MIT reference implementation. Pull from the LOR forum's Sequence Sharing subforum
   [18] (browse by thread, e.g. [19]). Decoder needs: XML reader for per-channel intensity/fade/
   shimmer blocks and timing in centiseconds; consult Cryptkeeper's `lightorama-protocol` repo
   [16] if a compressed `.lcs` file turns up.
3. **Vixen 3 `.tim`** — same hobbyist ecosystem, smaller but real corpus. Pull from
   kbarre123/vixen_sequences [13] and AusChristmasLighting [14]. Decoder needs: XML reader; cross-
   check field names against the open VixenLights/Vixen source [11] since no separate written spec
   exists.
4. **Freestyler `.chb`** — picked over the tied-score Resolume `.avc` because its content is
   literally small-venue DMX fixture chases (the target domain), whereas `.avc` is closer to VJ/
   video-clip triggering. Pull from the FreeStyler support-forum sample thread [23]. Decoder needs:
   a small binary reader for the wiki-documented word layout [22] (trivial, no library required).

**3D formats (decode first, they're the best-invested standards in the whole survey):**
- **GDTF + MVR together** — this is the pairing the task is really asking for: GDTF gives fixture
  type/DMX-channel/3D-geometry, MVR gives the scene assembly (positions, rotations, truss, which
  GDTF goes where). Pull GDTF fixtures from gdtf-share.com (free account) [48]; pull sample `.mvr`
  scenes from mvrdevelopment/libMVRgdtf's `examples/gdtf_share` folder and
  MomentFactory/Omniverse-MVR-GDTF-converter [49]. Decoder needs: don't write a parser from
  scratch — use `python-mvr` (MIT) [43] and `python-gdtf`/`pygdtf` (MIT) [45] directly, the same
  libraries GPL-3.0 BlenderDMX [44] uses in production for exactly this Blender-preview use case.
- **glTF/OBJ generic models** as a supplementary geometry source for trusses/fixtures GDTF doesn't
  cover — any standard loader (e.g. `pygltflib`, `trimesh`) suffices; check each model's license
  individually since Sketchfab-style sources mix CC0 through non-commercial terms.

## Don'ts

- Don't reverse-engineer Vectorworks `.vwx`, WYSIWYG `.wyg`, Capture `.c2p`/`.c2s`, ChamSys `.shw`,
  Obsidian ONYX, Hog `.hog`/`.h3`, Daslight/Sunlite, or MADRIX's show/timeline files — all closed,
  undocumented, no maintained parser found; the effort-to-yield ratio is poor. Where the *program*
  can export MVR instead (Vectorworks, Depence, L8, Capture), use that export.
- Don't treat SoundSwitch as a data source — closed format, and its per-song content lives behind
  a cloud-service ToS, not as downloadable files.
- Don't scrape paid sequence-shop sections or accounts-gated tiers (xLightsSeq.com's for-sale
  listings, GDTF-share beyond the free personal tier [50]) — stick to explicitly free items.
- Don't redistribute anything pulled, including bundled audio — every hobbyist community here
  treats uploaded sequences as uploader-owned [6]; keep the library strictly private/internal, as
  the club already intends.
- Don't bother with OSC/timecode as a "format" — it's a trigger protocol; decode the program it
  drives instead.

## Sources
1. xLights GitHub repo (GPL-3.0) — <https://github.com/xLightsSequencer/xLights>
2. webXLights parser/writer, GPL-3.0 — <https://github.com/loganjmoore/webxlights>
3. xLights manual, Layout tab — <https://manual.xlights.org/xlights/chapters/chapter-four-tabs/layout>
4. xLights manual, Models tab (OBJ 3D models) — <https://manual.xlights.org/xlights/chapters/chapter-four-tabs/models>
5. xLightsSeq.com sequence library — <https://xlightsseq.com/sequences/>
6. xLightsSeq.com Terms and rules — <https://xlightsseq.com/help/terms/>
7. AusChristmasLighting, "xLights sharing: sequences" — <https://auschristmaslighting.com/threads/xlights-sharing-sequences.10708/>
8. cp16net/xlights-sequences repo — <https://github.com/cp16net/xlights-sequences>
9. Cryptkeeper/fseq-file-format — <https://github.com/Cryptkeeper/fseq-file-format>
10. Cryptkeeper/libtinyfseq — <https://github.com/Cryptkeeper/libtinyfseq>
11. VixenLights/Vixen repo — <https://github.com/VixenLights/Vixen>
12. Vixen 3 license page — <https://www.vixenlights.com/download/license/>
13. kbarre123/vixen_sequences repo — <https://github.com/kbarre123/vixen_sequences>
14. AusChristmasLighting, "Vixen 3 sequences" — <https://auschristmaslighting.com/threads/vixen-3-sequences.6308/>
15. Light-O-Rama Sequencer File Menu help — <https://www.lightorama.com/help/sequencer_file_menu.htm>
16. Cryptkeeper/lightorama-protocol, LCS.md — <https://github.com/Cryptkeeper/lightorama-protocol/blob/master/LCS.md>
17. Cryptkeeper/libreorama, MIT — <https://github.com/Cryptkeeper/libreorama>
18. LOR Forums, Sequence Sharing subforum — <https://forums.lightorama.com/forum/19-sequence-sharing/>
19. LOR Forums, "Giving my collection away" — <https://forums.lightorama.com/topic/54742-giving-my-collection-away/>
20. Resolume-Composition-Converter, MIT — <https://github.com/tijnisfijn/Resolume-Composition-Converter>
21. Resolume Forum composition thread — <https://resolume.com/forum/viewtopic.php?t=20504>
22. FreeStyler Wiki, "Sequences Understanding" — <https://www.freestylersupport.com/wiki/create_sequence:sequence_understanding>
23. FreeStyler Support Forum, sample files — <https://www.freestylersupport.com/fsforum/viewtopic.php?t=1521>
24. MA Lighting forum, "reading exported XML from GMA2" — <https://forum.malighting.com/forum/thread/37425-reading-exported-xml-from-gma2/>
25. szymonplotkowski/GrandMA2---Python-lib, GPL-3.0 — <https://github.com/szymonplotkowski/GrandMA2---Python-lib>
26. aGuyNamedJonas/grandma2-snippets — <https://github.com/aGuyNamedJonas/grandma2-snippets>
27. andreasschindler.com, free bitmap library + grandMA2 showfile — <https://www.andreasschindler.com/free-108-bitmap-footage-library-with-grandma2-showfile/>
28. grandMA3 help, Lua Export() — <https://help.malighting.com/grandMA3/2.2/HTML/lua_object_export.html>
29. jimhoggey/Lightkey-Skill, MIT — <https://github.com/jimhoggey/Lightkey-Skill>
30. Avolites Forum, "Quick Save x Auto Save" — <https://forum.avolites.com/viewtopic.php?t=7357>
31. Avolites Showfile Parser (unofficial) — <https://www.avosupport.de/downloads/d4z/>
32. ChamSys Docs, MagicQ Concepts — <https://secure.chamsys.co.uk/help/documentation/magicq/concepts.html>
33. Obsidian ONYX, General Concepts — <https://support.obsidiancontrol.com/Content/Getting_Started/General_Concepts.htm>
34. SoundSwitch support, "Saving Projects and Light Shows" — <https://support.soundswitch.com/en/support/solutions/articles/69000853039-soundswitch-saving-projects-and-light-shows>
35. Daslight Forum, "Free show templates for you" — <https://forum.daslight.com/viewtopic.php?t=5122>
36. MADRIX 5 Fixture Editor manual — <https://help.madrix.com/m5/pdf/User_Manual_MADRIX_5_Fixture_Editor.pdf>
37. ETC Community, "Hog 4 show file extension questions" — <https://community.etcconnect.com/control_consoles/hog-lighting-controls/f/hog-os-software-discussion/44443/hog-4-show-file-extension-questions>
38. ETC, Exporting Show Data (Eos ASCII export) — <https://www.etcconnect.com/WebDocs/Controls/EosFamilyOnlineHelp/en/Content/05_Show_Files/Exporting_Show_Data.htm>
39. Westside Systems, USITT ASCII standard reference — <http://westsidesystems.com/alq/ascii.html>
40. QLC+ Forum, USITT ASCII import/export request — <https://www.qlcplus.org/forum/viewtopic.php?p=74574>
41. mvrdevelopment/spec (DIN SPEC 15800/15801) — <https://github.com/mvrdevelopment/spec>
42. mvrdevelopment/libMVRgdtf (MVR License 1.0) — <https://github.com/mvrdevelopment/libMVRgdtf>
43. open-stage/python-mvr, MIT — <https://github.com/open-stage/python-mvr>
44. open-stage/blender-dmx, GPL-3.0 — <https://github.com/open-stage/blender-dmx>
45. jackdpage/python-gdtf, MIT — <https://github.com/jackdpage/python-gdtf>
46. michaelhugi/gdtf_parser (Rust) — <https://github.com/michaelhugi/gdtf_parser>
47. gdtf-share.com Developer Page — <https://gdtf-share.com/landing/pages/developer.php>
48. GDTF.eu blog, 7,000+ fixture files — <https://www.gdtf.eu/blog/gdtf-share-fixture-library-added-to-the-gdtf-bench-online-tool/>
49. MomentFactory/Omniverse-MVR-GDTF-converter (sample .mvr) — <https://github.com/MomentFactory/Omniverse-MVR-GDTF-converter>
50. gdtf-share.com Terms and Conditions — <https://gdtf-share.com/landing/pages/termsAndConditions.php>
51. nrgsille76/io_scene_mvr Blender addon — <https://github.com/nrgsille76/io_scene_mvr>
52. Capture, Download Demo Packs — <https://www.capture.se/Downloads/Download-Demo-Packs>
53. Vectorworks blog, "Completed Vectorworks Projects" — <https://blog.vectorworks.net/completed-vectorworks-projects-for-you-to-download>
54. Archive Team wiki, VectorWorks format — <http://fileformats.archiveteam.org/wiki/VectorWorks>
55. CAST Software, WYSIWYG product page — <https://cast-soft.com/wysiwyg-lighting-design/>
56. Syncronorm, Depence overview (MVR + generic 3D import) — <https://www.syncronorm.com/products/depence2/visualization/overview>
57. Realizzer, Supported File Formats — <http://realizzer.com/R3DHelp/supported_file_formats.htm>
58. L8, software/integration page — <https://l8.ltd/m/lc.html>
59. QLC+ repo COPYING (Apache-2.0) — <https://github.com/mcallegari/qlcplus/blob/master/COPYING>
60. QLC+ repo sample show file — `resources/samples/Sample.qxw` (this repository, read locally)
