# Claims and Limitations

This prototype compares structurally supported rendered text elements against a selected public BDM-derived reference profile and venue geometry. It does **not** claim:

- individual human readability;
- accessibility or WCAG compliance;
- AVIXA certification or conformance with the complete current ANSI/AVIXA standard;
- analysis of OCR/image text, charts, SmartArt, arbitrary rotations, RTL/non-Latin shaping, lighting, glare, contrast, or individual visual acuity.

The public reference profile is versioned and cited in `reference/PUBLIC_BDM_REFERENCE_V1.json`. Pictures, charts, SmartArt/diagram graphics, media/OLE visuals, and inherited master/layout text are detected as unsupported coverage when present. They are not semantically analyzed; their presence prevents a whole-deck `VERIFIED` result in competition V1.


Runtime/deployment limits:

- competition V1 uses process-local ephemeral sessions and therefore requires exactly one application worker and one deployment replica;
- session capabilities protect API access, but production still requires HTTPS from the hosting platform;
- the application bounds request bytes before multipart parsing, but platform/reverse-proxy ingress limits should also be configured when available;
- public-host behavior, live Gemini networking, and unrestricted browser E2E remain separately verifiable deployment gates.
